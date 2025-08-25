from fastapi import FastAPI, HTTPException, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
import os, io, json
from openai import OpenAI
from jinja2 import Environment, BaseLoader
from weasyprint import HTML

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

# --- Memoria en RAM (demo). Cambiar a DB en prod. ---
DB: dict[str, Quote] = {}

# --- OpenAI ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL = os.getenv("MODEL_NAME", "gpt-4o-mini")  # ver doc oficial para modelos soportados

# --- App ---
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en prod: restringe dominios
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Utils ---
def make_prompt(q: QuoteRequest) -> list[dict]:
    when_txt = q.when or "sin fecha definida"
    pay_txt  = q.payment_type or "sin especificar"
    system = ("Eres un asistente que genera presupuestos técnicos cortos y claros "
              "para servicios de hogar/empresa en España. Devuelve SOLO JSON válido.")
    user = (f"Cliente: {q.client.name} (NIF {q.client.nif}). "
            f"Tipo: {q.client.type}. Dirección fiscal: {q.client.billingAddress}. "
            f"Email: {q.client.email}. Tel: {q.client.phone}. "
            f"Descripción: {q.description}. Fecha: {when_txt}. Pago: {pay_txt}. "
            "Estructura deseada: items (concept, qty, unit, unit_price, subtotal), "
            "tax_rate (entero %), subtotal, tax_total, total, schedule (ISO), terms, note. "
            "No inventes importes irreales; usa números razonables.")
    return [
        {"role":"system", "content": system},
        {"role":"user",   "content": user}
    ]

def parse_json(s: str) -> dict:
    try:
        return json.loads(s)
    except Exception as e:
        raise HTTPException(502, f"Modelo devolvió JSON inválido: {e}")

# --- Endpoints ---
@app.post("/api/quotes/generate", response_model=Quote)
def generate(q: QuoteRequest, x_api_key: Optional[str] = Header(None)):
    check_api_key(x_api_key)

    # 1) Llamada a OpenAI con salida JSON (json_object)
    # Docs oficiales: Chat Completions + JSON / Structured Outputs
    # https://platform.openai.com/docs/api-reference/chat  (chat)
    # https://platform.openai.com/docs/guides/structured-outputs (structured outputs)
    completion = client.chat.completions.create(
        model=MODEL,
        response_format={"type":"json_object"},
        messages=make_prompt(q)
    )
    content = completion.choices[0].message.content
    data = parse_json(content)

    # Fallback / defaults
    items = [QuoteItem(**it) for it in data.get("items", [])]
    subtotal = sum(it.subtotal for it in items) if items else float(data.get("subtotal", 0))
    tax_rate = int(data.get("tax_rate", 21))
    tax_total = round(subtotal * tax_rate / 100, 2)
    total = round(subtotal + tax_total, 2)

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
        raw_text=data.get("raw_text","")
    )
    DB[quote.quote_id] = quote
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

@app.patch("/api/quotes/{quote_id}", response_model=Quote)
def patch_quote(quote_id: str, body: PatchBody, x_api_key: Optional[str] = Header(None)):
    check_api_key(x_api_key)
    if quote_id not in DB:
        raise HTTPException(404, "Quote not found")
    q = DB[quote_id]

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
</body>
</html>
""")

@app.post("/api/quotes/{quote_id}/pdf")
def pdf(quote_id: str, x_api_key: Optional[str] = Header(None)):
    check_api_key(x_api_key)
    if quote_id not in DB:
        raise HTTPException(404, "Quote not found")
    q = DB[quote_id]

    html = TPL.render(quote=q.model_dump())
    pdf_bytes = HTML(string=html).write_pdf()  # Docs: WeasyPrint
    # Opción B: enviar al webhook de Fixhub si está configurado
    webhook = os.getenv("FIXHUB_WEBHOOK_URL", "").strip()
    if webhook:
        # Aquí harías requests.post(webhook, files={"file":("presupuesto.pdf", pdf_bytes, "application/pdf")}, data={...})
        # Para demo devolvemos status
        return {"status":"sent", "bytes": len(pdf_bytes)}

    # Opción A: devolver binario al frontend
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{quote_id}.pdf"'})
