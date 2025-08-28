from fastapi import APIRouter, HTTPException, Header, Response, Depends, Request
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from datetime import datetime, timedelta

import os, json, time, hashlib

from openai import OpenAI
from jinja2 import Environment, BaseLoader
from weasyprint import HTML

from .security import run_isolated, sanitize_filename, content_disposition

# ---------------------------------------------------------------------------
# Optional authentication dependency
# ---------------------------------------------------------------------------
ENABLE_USER_AUTH = os.getenv("ENABLE_USER_AUTH", "0") == "1"
if ENABLE_USER_AUTH:
    from .dependencies import get_current_user
else:  # pragma: no cover - simple fallback for unauthenticated mode
    def get_current_user():  # type: ignore[override]
        """Fallback dependency when authentication is disabled."""
        return "anonymous"

from . import database

# --- Seguridad simple (demo) ---
EXPECTED_API_KEY = os.getenv("API_KEY")
def check_api_key(x_api_key: Optional[str]):
    if EXPECTED_API_KEY and x_api_key != EXPECTED_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

# --- Modelos ---
class Client(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = "particular"
    name: str
    nif: Optional[str] = ""
    billingAddress: Optional[str] = ""
    email: Optional[str] = ""
    phone: Optional[str] = ""
    company: Optional[bool] = None

class QuoteItem(BaseModel):
    concept: str
    qty: float
    unit: str
    unit_price: float
    subtotal: float

class QuoteRequest(BaseModel):
    client: Client
    description: str
    when: Optional[str] = None
    payment_type: Optional[str] = "fixhub"
    documents: Optional[List[str]] = None

class Quote(BaseModel):
    quote_id: str
    currency: str = "EUR"
    items: List[QuoteItem]
    tax_rate: int = 21
    subtotal: float
    tax_total: float
    total: float
    schedule: Optional[str] = None
    terms: Optional[str] = ""
    note: Optional[str] = ""
    raw_text: Optional[str] = ""
    demo: bool = False

# --- Memoria en RAM (demo). Cambiar a DB en prod. ---
DB: dict[str, Quote] = {}

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ENABLED = bool(OPENAI_API_KEY)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_ENABLED else None
MODEL = os.getenv("MODEL_NAME", "gpt-4o-mini")  # ver doc oficial para modelos soportados
PROMPT_FILE = os.path.join(os.path.dirname(__file__), "..", "aps", "prompt.txt")

# --- Seguridad adicional ---
PII_FIELDS = {"name", "nif", "billingAddress", "email", "phone"}
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "aps")
ALLOWED_DOCS = set(filter(None, os.getenv("QUOTE_DOC_WHITELIST", "").split(",")))
DEMO_RATE_LIMIT_SECONDS = int(os.getenv("DEMO_RATE_LIMIT_SECONDS", "60"))
DEMO_DAILY_QUOTA = int(os.getenv("DEMO_DAILY_QUOTA", "20"))
DEMO_USAGE: Dict[str, Dict[str, Any]] = {}

# --- Router ---
router = APIRouter()

# --- Utils ---
def _redact_pii(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: ("[REDACTED]" if k in PII_FIELDS else _redact_pii(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact_pii(v) for v in data]
    return data


def forward_to_openai(custom_message: str, payload: dict,
                      documents: Optional[List[str]] = None,
                      response_format: Optional[dict] = None):
    """Forward data to OpenAI, logging the sent messages with PII redacted."""
    messages = [
        {"role": "system", "content": custom_message},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
    ]

    log_messages = [
        {"role": "system", "content": custom_message},
        {"role": "user", "content": json.dumps(_redact_pii(payload), ensure_ascii=False)}
    ]

    if documents:
        for name in documents:
            base = os.path.basename(name)
            if base not in ALLOWED_DOCS:
                continue
            safe_path = os.path.join(DOCS_DIR, base)
            try:
                with open(safe_path, "r", encoding="utf-8") as f:
                    content = f.read()
                messages.append({"role": "user", "content": content})
                log_messages.append({"role": "user", "content": f"[Document {base}]"})
            except Exception:
                log_messages.append({"role": "user", "content": f"[Error leyendo {base}]"})

    with open("last_openai_message.json", "w", encoding="utf-8") as f:
        json.dump(log_messages, f, ensure_ascii=False, indent=2)

    params = {"model": MODEL, "messages": messages, "timeout": int(os.getenv("OPENAI_TIMEOUT", "10"))}
    if response_format:
        params["response_format"] = response_format

    retries = int(os.getenv("OPENAI_MAX_RETRIES", "2"))
    last_err = None
    for _ in range(retries):
        try:
            return client.chat.completions.create(**params)
        except Exception as e:  # pragma: no cover - network errors
            last_err = e
            time.sleep(1)
    raise HTTPException(502, f"OpenAI request failed: {last_err}")

def parse_json(s: str) -> dict:
    try:
        return json.loads(s)
    except Exception as e:
        raise HTTPException(502, f"Modelo devolvió JSON inválido: {e}")

# --- Endpoints ---
@router.post("/api/quotes/generate", response_model=Quote)
def generate(
    q: QuoteRequest,
    request: Request,
    x_api_key: Optional[str] = Header(None),
    device_id: str = Header(..., alias="X-Device-Id"),
    current_user: str = Depends(get_current_user),
):
    check_api_key(x_api_key)
    if not OPENAI_ENABLED:
        raise HTTPException(503, "OpenAI integration disabled")

    # Demo account rate limiting and quotas
    if current_user.startswith("demo"):
        now = datetime.utcnow()
        info = DEMO_USAGE.get(current_user)
        if not info or info.get("day") != now.date():
            info = {"day": now.date(), "count": 0, "last": None}
        if info["last"] and now - info["last"] < timedelta(seconds=DEMO_RATE_LIMIT_SECONDS):
            raise HTTPException(429, "Too many requests, slow down")
        if info["count"] >= DEMO_DAILY_QUOTA:
            raise HTTPException(429, "Daily quota exceeded")
        info["count"] += 1
        info["last"] = now
        DEMO_USAGE[current_user] = info

    try:
        prompt_scope: dict[str, str] = {}
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            exec(f.read(), {}, prompt_scope)
        custom_msg = prompt_scope.get("custom_msg", "")
    except FileNotFoundError:
        raise HTTPException(500, "Prompt configuration file not found")


    completion = forward_to_openai(custom_msg, q.model_dump(), q.documents,
                                   response_format={"type": "json_object"})
    data = parse_json(completion.choices[0].message.content)

    # Fallback / defaults
    items = [QuoteItem(**it) for it in data.get("items", [])]
    subtotal = sum(it.subtotal for it in items) if items else float(data.get("subtotal", 0))
    tax_rate = int(data.get("tax_rate", 21))
    tax_total = round(subtotal * tax_rate / 100, 2)
    total = round(subtotal + tax_total, 2)

    is_demo = current_user in {"demo@fixhub.es", "demo2@fixhub.es"}
    quote = Quote(
        quote_id=f"q_{len(DB)+1:05d}",
        currency=data.get("currency","EUR"),
        items=items,
        tax_rate=tax_rate,
        subtotal=float(subtotal),
        tax_total=float(tax_total),
        total=float(total),
        schedule=q.when,
        terms=data.get("terms",""),
        note=data.get("note",""),
        raw_text=data.get("raw_text",""),
        demo=is_demo
    )
    DB[quote.quote_id] = quote
    database.add_audit_log(
        actor=current_user,
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
        action="create",
        obj=f"quote:{quote.quote_id}",
        before=None,
        after=quote.model_dump(),
    )
    return quote

class PatchItem(BaseModel):
    index: int
    concept: Optional[str] = None
    qty: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    subtotal: Optional[float] = None

class PatchBody(BaseModel):
    items: Optional[List[PatchItem]] = None
    tax_rate: Optional[int] = None
    terms: Optional[str] = None
    note: Optional[str] = None

@router.patch("/api/quotes/{quote_id}", response_model=Quote)
def patch_quote(
    quote_id: str,
    body: PatchBody,
    request: Request,
    x_api_key: Optional[str] = Header(None),
    current_user: str = Depends(get_current_user),
):
    check_api_key(x_api_key)
    if not OPENAI_ENABLED:
        raise HTTPException(503, "OpenAI integration disabled")
    if quote_id not in DB:
        raise HTTPException(404, "Quote not found")
    q = DB[quote_id]
    before = q.model_dump()

    forward_to_openai("Actualiza un presupuesto con los campos proporcionados",
                      {"quote_id": quote_id, **body.model_dump(exclude_unset=True)})

    # actualiza items
    if body.items:
        items = q.items.copy()
        for p in body.items:
            if 0 <= p.index < len(items):
                it = items[p.index].model_copy(update={k:v for k,v in p.model_dump().items() if k not in ("index",) and v is not None})
                # recalcular subtotal si cambió qty o unit_price y no se mandó subtotal explícito
                if (p.qty is not None or p.unit_price is not None) and p.subtotal is None:
                    it.subtotal = round((it.qty or 0) * (it.unit_price or 0), 2)
                items[p.index] = it
        q.items = items

    # otros campos
    if body.tax_rate is not None:
        q.tax_rate = body.tax_rate
    if body.terms is not None:
        q.terms = body.terms
    if body.note is not None:
        q.note = body.note

    # recálculo
    q.subtotal = round(sum(i.subtotal for i in q.items), 2)
    q.tax_total = round(q.subtotal * q.tax_rate / 100, 2)
    q.total     = round(q.subtotal + q.tax_total, 2)

    DB[quote_id] = q
    database.add_audit_log(
        actor=current_user,
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
        action="update",
        obj=f"quote:{quote_id}",
        before=before,
        after=q.model_dump(),
    )
    return q

# plantilla HTML para el PDF (demo)
TPL = Environment(loader=BaseLoader()).from_string("""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<style>
  body { font-family: Inter, system-ui, Arial; font-size: 12px; color:#111; }
  h1 { font-size: 18px; margin: 0 0 6px; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; }
  th, td { border: 1px solid #ddd; padding: 6px; text-align: left; }
  tfoot td { font-weight: bold; }
  .muted { color:#555; font-size: 11px; }
</style>
</head>
<body>
  <h1>Presupuesto {{quote.quote_id}}</h1>
  <div class="muted">Programado: {{quote.schedule or "Sin fecha"}}</div>
  {% if demo %}<div style="position:fixed; top:40%; left:20%; font-size:72px; color:rgba(200,0,0,0.2); transform:rotate(-30deg);">DEMO</div>{% endif %}

  <table>
    <thead><tr><th>Concepto</th><th>Ud</th><th>Cant.</th><th>Precio</th><th>Importe</th></tr></thead>
    <tbody>
    {% for it in quote.items %}
      <tr>
        <td>{{it.concept}}</td>
        <td>{{it.unit}}</td>
        <td>{{"%.2f"|format(it.qty)}}</td>
        <td>{{"%.2f"|format(it.unit_price)}} {{quote.currency}}</td>
        <td>{{"%.2f"|format(it.subtotal)}} {{quote.currency}}</td>
      </tr>
    {% endfor %}
    </tbody>
    <tfoot>
      <tr><td colspan="4">Base imponible</td><td>{{"%.2f"|format(quote.subtotal)}} {{quote.currency}}</td></tr>
      <tr><td colspan="4">IVA {{quote.tax_rate}}%</td><td>{{"%.2f"|format(quote.tax_total)}} {{quote.currency}}</td></tr>
      <tr><td colspan="4">TOTAL</td><td>{{"%.2f"|format(quote.total)}} {{quote.currency}}</td></tr>
    </tfoot>
  </table>

  <p class="muted">{{quote.terms}}</p>
  {% if quote.note %}<p class="muted">Nota: {{quote.note}}</p>{% endif %}
  <p class="muted">{{seal}}</p>
</body>
</html>
""")

@router.get("/api/quotes/{quote_id}/pdf")
def pdf(quote_id: str, x_api_key: Optional[str] = Header(None)):
    check_api_key(x_api_key)

    if not OPENAI_ENABLED:
        raise HTTPException(503, "OpenAI integration disabled")

    sanitize_filename(quote_id)

    if quote_id not in DB:
        raise HTTPException(404, "Quote not found")
    q = DB[quote_id]
    forward_to_openai("Genera un PDF para este presupuesto", q.model_dump())


    # Metadatos y checksum
    prompt_version = "unknown"
    tarifa = None
    try:
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        prompt_version = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
        scope: dict[str, object] = {}
        try:
            exec(content, {}, scope)
        except Exception:
            pass
        tarifa = scope.get("tarifa_hora_eur")
    except FileNotFoundError:
        pass

    data_for_checksum = {
        "quote": q.model_dump(),
        "prompt_version": prompt_version,
        "iva": q.tax_rate,
        "tarifa": tarifa,
        "demo": q.demo,
    }
    checksum = hashlib.sha256(
        json.dumps(data_for_checksum, sort_keys=True).encode("utf-8")
    ).hexdigest()

    seal_parts = [f"Prompt {prompt_version}", f"IVA {q.tax_rate}%"]
    if tarifa is not None:
        seal_parts.append(f"Tarifa {tarifa}")
    seal_parts.append(f"Checksum {checksum}")
    if q.demo:
        seal_parts.append("DEMO")
    seal = " | ".join(seal_parts)

    html = TPL.render(quote=q.model_dump(), seal=seal, demo=q.demo)
    pdf_bytes = HTML(string=html).write_pdf()  # Docs: WeasyPrint
    # Opción B: enviar al webhook de Fixhub si está configurado

    webhook = os.getenv("FIXHUB_WEBHOOK_URL", "").strip()
    if webhook:
        return {"status": "sent", "bytes": len(pdf_bytes)}


    # Opción A: devolver binario al frontend
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{quote_id}.pdf"',
            "X-Checksum": checksum,
        },
    )

