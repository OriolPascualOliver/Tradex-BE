"""
VERI*FACTU MVP (Python + FastAPI)
---------------------------------

Objetivo: generar facturas con QR, cadena de huellas (hash), PDF y envío (stub) a AEAT
modo VERI*FACTU. **Este MVP es educativo**: debes ajustar el cálculo exacto de la
huella, el XML, la firma XAdES y el WSDL según la documentación oficial de AEAT.

Requisitos sugeridos (requirements.txt):
fastapi
uvicorn
pydantic
SQLAlchemy
qrcode
weasyprint
lxml
# Opcionales para integración real:
# xmlsec
# zeep

Ejecutar:
  pip install -r requirements.txt
  uvicorn verifactu_mvp_app:app --reload

Endpoints útiles:
  POST   /facturas                → Crea factura, genera QR, PDF y registro de alta
  GET    /facturas/{id}           → Consulta una factura
  GET    /facturas/{id}/pdf       → Descarga el PDF
  GET    /facturas/{id}/qr        → Descarga el PNG del QR

Variables de entorno:
  DATABASE_URL        (opcional)  default: sqlite:///./verifactu.db
  AEAT_QR_BASE_URL    (obligatorio ajustar) URL base del servicio de cotejo QR (ver AEAT)
  VERIFACTU_ENVIAR    ("1" para intentar enviar stub; por defecto no envía)
  CERT_PATH, CERT_PASS (si implementas firma XAdES)
  AEAT_WSDL_URL       (si implementas envío real por SOAP)

Referencias normativas/técnicas (ajusta en tu proyecto con la versión vigente):
- Portal SIF & VERI*FACTU (normativa, técnica, WSDL, hash, firma, QR).
"""

from __future__ import annotations

import os
import json
import hashlib
from datetime import datetime, date
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import smtplib
from email.message import EmailMessage
from pydantic import BaseModel, Field, validator

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Date,
    Float,
    DateTime,
    ForeignKey,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

import qrcode
from weasyprint import HTML

from .observability import inc_invoice_verification
from .security import hashed_path, run_isolated, content_disposition

from lxml import etree

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./verifactu.db")
AEAT_QR_BASE_URL = os.getenv("AEAT_QR_BASE_URL", "https://www.agenciatributaria.gob.es/qr")
VERIFACTU_ENVIAR = os.getenv("VERIFACTU_ENVIAR", "0") == "1"
CERT_PATH = os.getenv("CERT_PATH")
CERT_PASS = os.getenv("CERT_PASS")
AEAT_WSDL_URL = os.getenv("AEAT_WSDL_URL")
VERIFY_URL_BASE = os.getenv("VERIFY_URL_BASE", "http://localhost:8000")

# Salidas
OUTPUT_DIR = os.path.abspath(os.getenv("OUTPUT_DIR", "./salida"))
os.makedirs(OUTPUT_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# DB setup
# -----------------------------------------------------------------------------
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    serie = Column(String, default="A", index=True)
    numero = Column(Integer, index=True)
    fecha = Column(Date, default=date.today)

    emisor_nif = Column(String, index=True)
    emisor_nombre = Column(String)
    receptor_nif = Column(String)
    receptor_nombre = Column(String)

    tipo = Column(String, default="F1")  # Simplificado: usa el código correcto según AEAT

    base = Column(Float)
    tipo_iva = Column(Float)
    cuota_iva = Column(Float)
    total = Column(Float)

    estado = Column(String, default="EMITIDA")

    qr_path = Column(String)
    pdf_path = Column(String)
    hash_actual = Column(String)

    items = relationship("Item", back_populates="invoice", cascade="all, delete-orphan")
    registros = relationship("Ledger", back_populates="invoice", cascade="all, delete-orphan")


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"))
    descripcion = Column(String)
    cantidad = Column(Float, default=1.0)
    precio_unitario = Column(Float)

    invoice = relationship("Invoice", back_populates="items")


class Ledger(Base):
    __tablename__ = "ledger"
    id = Column(Integer, primary_key=True)
    factura_id = Column(Integer, ForeignKey("invoices.id"), index=True)
    tipo_registro = Column(String)  # 'ALTA' o 'ANULACION'
    payload_json = Column(Text)
    hash_anterior = Column(String)
    hash_actual = Column(String)
    creado_en = Column(DateTime, default=datetime.utcnow)

    invoice = relationship("Invoice", back_populates="registros")


Base.metadata.create_all(engine)


# -----------------------------------------------------------------------------
# Esquemas Pydantic
# -----------------------------------------------------------------------------
class ItemIn(BaseModel):
    descripcion: str
    cantidad: float = 1.0
    precio_unitario: float

    @validator("cantidad", "precio_unitario")
    def non_negative(cls, v):
        if v < 0:
            raise ValueError("Debe ser >= 0")
        return v


class InvoiceIn(BaseModel):
    serie: str = "A"
    numero: Optional[int] = None  # si None, el sistema asigna secuencia por serie
    fecha: Optional[date] = None

    emisor_nif: str
    emisor_nombre: str
    receptor_nif: str
    receptor_nombre: str

    email: Optional[str] = None

    tipo: str = "F1"

    items: List[ItemIn] = Field(..., min_items=1)
    tipo_iva: float = 21.0


class InvoiceOut(BaseModel):
    id: int
    serie: str
    numero: int
    fecha: date
    emisor_nif: str
    receptor_nif: str
    base: float
    tipo_iva: float
    cuota_iva: float
    total: float
    estado: str
    hash_actual: Optional[str]
    qr_path: Optional[str]
    pdf_path: Optional[str]


# -----------------------------------------------------------------------------
# Utilidades
# -----------------------------------------------------------------------------

def next_number(db, serie: str) -> int:
    last = (
        db.query(Invoice)
        .filter(Invoice.serie == serie)
        .order_by(Invoice.numero.desc())
        .first()
    )
    return (last.numero + 1) if (last and last.numero) else 1


def calc_totals(items: List[ItemIn], tipo_iva: float) -> tuple[float, float, float]:
    base = sum(i.cantidad * i.precio_unitario for i in items)
    base = round(base, 2)
    cuota = round(base * tipo_iva / 100.0, 2)
    total = round(base + cuota, 2)
    return base, cuota, total


# NOTA: Este algoritmo de huella es **placeholder**. Ajusta al publicado por AEAT
# (campos, orden, normalización y codificación) en:
# "Algoritmo de cálculo de codificación de la huella o hash".

def build_registro_alta(inv: Invoice, prev_hash: Optional[str], fecha_hora: Optional[str] = None):
    payload = {
        "NIFEmisor": inv.emisor_nif,
        "SerieFactura": inv.serie,
        "NumeroFactura": str(inv.numero),
        "FechaExpedicion": inv.fecha.isoformat(),
        "TipoFactura": inv.tipo,
        "BaseImponible": f"{inv.base:.2f}",
        "CuotaTotal": f"{inv.cuota_iva:.2f}",
        "ImporteTotal": f"{inv.total:.2f}",
        "HuellaAnterior": prev_hash or "",
        "FechaHoraGeneracion": fecha_hora or datetime.now().isoformat(timespec="seconds"),
    }
    # Canonicalización simple → ajusta a la especificación oficial
    canon = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return payload, digest


def generar_qr(inv: Invoice) -> str:

    """Genera el PNG del QR con la URL de cotejo.

    IMPORTANTE: Los parámetros exactos (nombres/orden/formato) los dicta el doc
    "Características del QR y especificaciones del servicio de cotejo". Aquí usamos
    parámetros genéricos como ejemplo.
    """
    url = (
        f"{AEAT_QR_BASE_URL}?nif={inv.emisor_nif}"
        f"&serie={inv.serie}&num={inv.numero}"
        f"&fecha={inv.fecha.isoformat()}&total={inv.total:.2f}"
    )
    identifier = f"{inv.serie}_{inv.numero}"
    path = hashed_path(identifier, "qr", "png", OUTPUT_DIR)

    def _render() -> None:
        img = qrcode.make(url)
        img.save(path)

    try:
        run_isolated(_render)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="QR generation timed out")

    return path


def html_factura(inv: Invoice, items: List[Item], timestamp: str) -> str:
    filas = "".join(
        f"<tr><td>{it.descripcion}</td><td style='text-align:right'>{it.cantidad:.2f}</td>"
        f"<td style='text-align:right'>{it.precio_unitario:.2f}</td>"
        f"<td style='text-align:right'>{it.cantidad * it.precio_unitario:.2f}</td></tr>"
        for it in items
    )
    qr_rel = inv.qr_path and os.path.relpath(inv.qr_path, start=os.getcwd())
    return f"""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<title>Factura {inv.serie}-{inv.numero}</title>
<style>
 body {{ font-family: Arial, sans-serif; font-size: 12px; }}
 h1 {{ font-size: 18px; margin: 0 0 6px 0; }}
 table {{ width: 100%; border-collapse: collapse; }}
 th, td {{ border: 1px solid #ddd; padding: 6px; }}
 .right {{ text-align: right; }}
 .small {{ font-size: 10px; color:#555; }}
 .row {{ display:flex; justify-content:space-between; margin: 8px 0; }}
 .box {{ width:48%; }}
</style>
</head>
<body>
  <h1>Factura {inv.serie}-{inv.numero}</h1>
  <div class="row">
    <div class="box">
      <strong>Emisor</strong><br/>
      {inv.emisor_nombre}<br/>
      NIF: {inv.emisor_nif}
    </div>
    <div class="box">
      <strong>Receptor</strong><br/>
      {inv.receptor_nombre}<br/>
      NIF: {inv.receptor_nif}
    </div>
  </div>

  <div class="row">
    <div>Fecha de expedición: {inv.fecha.isoformat()}</div>
    <div>Tipo: {inv.tipo}</div>
  </div>

  <table>
    <thead>
      <tr><th>Descripción</th><th class="right">Cantidad</th><th class="right">Precio</th><th class="right">Importe</th></tr>
    </thead>
    <tbody>
      {filas}
    </tbody>
  </table>

  <div class="row" style="margin-top:10px;">
    <div class="box small">
      <em>VERI*FACTU</em> — Factura verificable en la sede electrónica de la AEAT.
    </div>
    <div class="box right">
      Base: {inv.base:.2f} €<br/>
      IVA ({inv.tipo_iva:.2f}%): {inv.cuota_iva:.2f} €<br/>
      <strong>Total: {inv.total:.2f} €</strong>
    </div>
  </div>

  <div style="margin-top:10px;display:flex;align-items:center;gap:12px;">
    <div>
      <img src="{qr_rel}" alt="QR" style="width:140px;height:140px;border:1px solid #ccc;"/>
    </div>
    <div class="small">
      Escanea el QR para cotejar/verificar en la AEAT según modalidad VERI*FACTU.
    </div>
  </div>

  <div class="small" style="margin-top:8px;">
    Huella (hash) actual: {inv.hash_actual or ""}<br/>
    Sello temporal: {timestamp}
  </div>
</body>
</html>
"""



def render_pdf(inv: Invoice, items: List[Item]) -> str:
    html = html_factura(inv, items)
    identifier = f"{inv.serie}_{inv.numero}"
    path = hashed_path(identifier, "factura", "pdf", OUTPUT_DIR)

    def _render() -> None:
        HTML(string=html, base_url=os.getcwd()).write_pdf(path)

    try:
        run_isolated(_render)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="PDF generation timed out")

    return path


def send_email_with_pdf(pdf_path: str, recipient: str) -> None:
    """Send the generated invoice PDF via SMTP."""
    smtp_server = os.getenv("SMTP_SERVER", "localhost")
    smtp_port = int(os.getenv("SMTP_PORT", "25"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_SENDER", smtp_user or "no-reply@example.com")

    with open(pdf_path, "rb") as f:
        data = f.read()

    msg = EmailMessage()
    msg["Subject"] = "Factura"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content("Adjuntamos su factura en PDF.")
    msg.add_attachment(data, maintype="application", subtype="pdf", filename=os.path.basename(pdf_path))

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        if smtp_user and smtp_password:
            server.starttls()
            server.login(smtp_user, smtp_password)
        server.send_message(msg)


def build_xml_registro(payload: dict, digest_hex: str) -> bytes:
    """Construye un XML mínimo del registro de alta.

    **Ajusta** las etiquetas y namespaces al esquema oficial publicado por AEAT
    en "Diseños de registro" y "Esquemas".
    """
    nsmap = {None: "urn:es:aeat:verifactu:registro:v1"}
    root = etree.Element("RegistroFacturacionAlta", nsmap=nsmap)
    for k, v in payload.items():
        e = etree.SubElement(root, k)
        e.text = str(v)
    hu = etree.SubElement(root, "Huella")
    hu.text = digest_hex
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def sign_xades(xml_bytes: bytes, cert_path: Optional[str], cert_pass: Optional[str]) -> bytes:
    """Devuelve el XML firmado (stub).

    Para firma real XAdES (enveloped) implementa con `xmlsec` o integra un
    servicio de firma.
    """
    # TODO: Implementar firma XAdES conforme a las "Especificaciones técnicas de la firma"
    # Retornamos el mismo XML como *placeholder* para el MVP.
    return xml_bytes


def send_to_aeat(wsdl_url: str, signed_xml: bytes) -> dict:
    """Envío **simulado** a AEAT.

    Para envío real: usa `zeep` (SOAP) con el WSDL oficial y maneja acuses y
    errores conforme a "Validaciones y errores".
    """
    # TODO: Implementar cliente SOAP real con zeep.
    return {"estado": "ENVIADO_SIMULADO", "csv": "SIM-CSV", "timestamp": datetime.utcnow().isoformat()}


# -----------------------------------------------------------------------------
# Router
# -----------------------------------------------------------------------------
router = APIRouter()


@router.post("/", response_model=InvoiceOut)
def crear_factura(datos: InvoiceIn):
    with SessionLocal() as db:
        numero = datos.numero or next_number(db, datos.serie)
        ffecha = datos.fecha or date.today()

        base, cuota, total = calc_totals(datos.items, datos.tipo_iva)

        inv = Invoice(
            serie=datos.serie,
            numero=numero,
            fecha=ffecha,
            emisor_nif=datos.emisor_nif,
            emisor_nombre=datos.emisor_nombre,
            receptor_nif=datos.receptor_nif,
            receptor_nombre=datos.receptor_nombre,
            tipo=datos.tipo,
            base=base,
            tipo_iva=datos.tipo_iva,
            cuota_iva=cuota,
            total=total,
        )
        db.add(inv)
        db.flush()  # obtener inv.id

        # Items
        for it in datos.items:
            db.add(Item(invoice_id=inv.id, descripcion=it.descripcion, cantidad=it.cantidad, precio_unitario=it.precio_unitario))
        db.flush()

        # Hash encadenado y registro de alta
        prev = (
            db.query(Ledger)
            .order_by(Ledger.id.desc())
            .first()
        )
        prev_hash = prev.hash_actual if prev else None
        payload, digest = build_registro_alta(inv, prev_hash)
        timestamp = payload["FechaHoraGeneracion"]

        ledger = Ledger(
            factura_id=inv.id,
            tipo_registro="ALTA",
            payload_json=json.dumps(payload, ensure_ascii=False),
            hash_anterior=prev_hash,
            hash_actual=digest,
        )
        db.add(ledger)

        inv.hash_actual = digest

        # QR y PDF
        inv.qr_path = generar_qr(inv)
        inv.pdf_path = render_pdf(inv, inv.items, timestamp)

        if datos.email:
            try:
                send_email_with_pdf(inv.pdf_path, datos.email)
            except Exception:
                pass

        # XML, firma y (opcional) envío
        xml_reg = build_xml_registro(payload, digest)
        xml_signed = sign_xades(xml_reg, CERT_PATH, CERT_PASS)

        envio_info = None
        if VERIFACTU_ENVIAR and AEAT_WSDL_URL:
            envio_info = send_to_aeat(AEAT_WSDL_URL, xml_signed)

        db.commit()

        out = InvoiceOut(
            id=inv.id,
            serie=inv.serie,
            numero=inv.numero,
            fecha=inv.fecha,
            emisor_nif=inv.emisor_nif,
            receptor_nif=inv.receptor_nif,
            base=inv.base,
            tipo_iva=inv.tipo_iva,
            cuota_iva=inv.cuota_iva,
            total=inv.total,
            estado=inv.estado,
            hash_actual=inv.hash_actual,
            qr_path=inv.qr_path,
            pdf_path=inv.pdf_path,
        )
        return out


@router.get("/{factura_id}", response_model=InvoiceOut)
def obtener_factura(factura_id: int):
    with SessionLocal() as db:
        inv = db.get(Invoice, factura_id)
        if not inv:
            raise HTTPException(status_code=404, detail="Factura no encontrada")
        return InvoiceOut(
            id=inv.id,
            serie=inv.serie,
            numero=inv.numero,
            fecha=inv.fecha,
            emisor_nif=inv.emisor_nif,
            receptor_nif=inv.receptor_nif,
            base=inv.base,
            tipo_iva=inv.tipo_iva,
            cuota_iva=inv.cuota_iva,
            total=inv.total,
            estado=inv.estado,
            hash_actual=inv.hash_actual,
            qr_path=inv.qr_path,
            pdf_path=inv.pdf_path,
        )


@router.get("/{factura_id}/pdf")
def descargar_pdf(factura_id: int):
    with SessionLocal() as db:
        inv = db.get(Invoice, factura_id)
        if not inv or not inv.pdf_path or not os.path.exists(inv.pdf_path):
            raise HTTPException(status_code=404, detail="PDF no disponible")
        fname = os.path.basename(inv.pdf_path)
        headers = content_disposition(fname)
        return FileResponse(inv.pdf_path, media_type="application/pdf", headers=headers)


@router.get("/{factura_id}/qr")
def descargar_qr(factura_id: int):
    with SessionLocal() as db:
        inv = db.get(Invoice, factura_id)
        if not inv or not inv.qr_path or not os.path.exists(inv.qr_path):
            raise HTTPException(status_code=404, detail="QR no disponible")

        fname = os.path.basename(inv.qr_path)
        headers = content_disposition(fname)
   

        return FileResponse(inv.qr_path, media_type="image/png", filename=os.path.basename(inv.qr_path))

@router.get("/{factura_id}/verify")
def verificar_factura(factura_id: int):
    """Recalculate hash to verify invoice integrity."""
    with SessionLocal() as db:
        inv = db.get(Invoice, factura_id)
        if not inv:
            raise HTTPException(status_code=404, detail="Factura no encontrada")
        data = f"{inv.serie}{inv.numero}{inv.fecha}{inv.emisor_nif}{inv.receptor_nif}{inv.total}"
        digest = hashlib.sha256(data.encode()).hexdigest()
        verified = inv.hash_actual == digest
        inc_invoice_verification()
        return {"verified": verified}
