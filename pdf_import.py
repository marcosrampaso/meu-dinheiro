"""Conservative text extraction for card statement PDFs."""

from __future__ import annotations

import base64
import binascii
import io
import re
import sys
from pathlib import Path

from finance import ValidationError, parse_amount


MAX_PDF_BYTES = 5_000_000
MAX_PDF_PAGES = 50
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
DATE = r"\d{2}/\d{2}(?:/\d{2,4})?"
MONEY = r"(?:[-+]?\s*R\$\s*)?(?:[-+]?\d[\d.]*,\d{2}|\d[\d.]*,\d{2}-|\(\d[\d.]*,\d{2}\))"
ENTRY = re.compile(rf"^\s*(?P<date>{DATE})\s+(?P<description>.+?)\s+(?P<amount>{MONEY})\s*$", re.IGNORECASE)
INTER_MONTHS = {
    "jan": "01", "fev": "02", "mar": "03", "abr": "04", "mai": "05", "jun": "06",
    "jul": "07", "ago": "08", "set": "09", "out": "10", "nov": "11", "dez": "12",
}
INTER_ENTRY = re.compile(
    r"^\s*(?P<day>\d{1,2})\s+de\s+(?P<month>jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\.?\s+"
    r"(?P<description>.+?)\s+(?P<sign>[+-])\s+R\$\s*(?P<amount>\d[\d.]*,\d{2})\s*$",
    re.IGNORECASE,
)
SUMMARY = re.compile(
    r"^(?:total|subtotal|saldo|limite|vencimento|valor\s+da\s+fatura|pagamento\s+m[ií]nimo|"
    r"pagamento\s+(?:da\s+)?fatura|pagamento\s+recebido)\b", re.IGNORECASE,
)
INTER_TOTAL_LABELS = {
    "despesas do mês": "expenses_cents",
    "valor antecipado": "advanced_cents",
    "fatura atual": "invoice_cents",
}


def _reader(pdf_bytes: bytes, password: str):
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        if reader.is_encrypted:
            if not password:
                raise ValidationError("Este PDF tem senha. Informe a senha no formulário de importação.")
            if not reader.decrypt(password):
                raise ValidationError("A senha do PDF está incorreta.")
        if not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
            raise ValidationError("A fatura deve ter de 1 a 50 páginas.")
        return reader
    except ValidationError:
        raise
    except Exception as exc:
        if "cryptography" in str(exc).lower():
            raise ValidationError("Este PDF protegido usa AES. Instale a dependência opcional 'cryptography' para abri-lo.") from exc
        raise ValidationError("Não foi possível abrir o PDF. Verifique o arquivo ou a senha.") from exc


def extract_pdf_text(content_base64: str, password: str = "") -> str:
    if not isinstance(content_base64, str) or len(content_base64) > 7_000_000:
        raise ValidationError("O PDF deve ter até 5 MB.")
    if not isinstance(password, str) or len(password) > 120:
        raise ValidationError("Senha do PDF inválida.")
    try:
        pdf_bytes = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("Dados do PDF inválidos.") from exc
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValidationError("O PDF deve ser válido e ter até 5 MB.")
    if not pdf_bytes.startswith(b"%PDF"):
        header_at = pdf_bytes.find(b"%PDF", 0, 1_000_000)
        if header_at < 0 or any(byte not in b"\x00\t\n\r " for byte in pdf_bytes[:header_at]):
            raise ValidationError("O PDF deve ser válido e ter até 5 MB.")
        pdf_bytes = pdf_bytes[header_at:]
    reader = _reader(pdf_bytes, password)
    pages = []
    try:
        for page in reader.pages:
            pages.append(page.extract_text(extraction_mode="layout") or "")
            if sum(len(text) for text in pages) > 1_000_000:
                raise ValidationError("O texto da fatura é grande demais.")
    except ValidationError:
        raise
    except Exception as exc:
        if "cryptography" in str(exc).lower():
            raise ValidationError("Este PDF protegido usa AES. Instale a dependência opcional 'cryptography' para abri-lo.") from exc
        raise ValidationError("Não foi possível ler o texto do PDF.") from exc
    text = "\n".join(pages)
    if not text.strip():
        raise ValidationError("Este PDF não contém texto selecionável. Precisará de OCR.")
    return text


def statement_rows(text: str) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        match = ENTRY.match(line)
        inter_match = INTER_ENTRY.match(line) if not match else None
        if match:
            raw_date = match.group("date")
            description = match.group("description").strip()
            amount = match.group("amount")
            style = "generic"
        elif inter_match:
            raw_date = f"{int(inter_match.group('day')):02d}/{INTER_MONTHS[inter_match.group('month').lower()]}"
            description = inter_match.group("description").strip()
            amount = inter_match.group("sign") + inter_match.group("amount")
            style = "inter"
        else:
            continue
        if SUMMARY.match(description) or not re.search(r"[A-Za-zÀ-ÿ]", description):
            continue
        if style == "inter" and amount.startswith("+") and re.search(r"\bpagamento\b", description, re.IGNORECASE):
            continue
        if len(re.findall(DATE, description)) > 0:
            continue
        rows.append({
            "date": raw_date,
            "description": description[:120],
            "amount": amount,
            "style": style,
        })
    if not rows:
        raise ValidationError("Não encontrei lançamentos em linhas do PDF. Envie uma amostra sem dados pessoais para ajustar o leitor.")
    if len(rows) > 5000:
        raise ValidationError("Importe no máximo 5000 lançamentos por fatura.")
    return rows


def inter_statement_totals(text: str) -> dict[str, int]:
    """Read the itemized statement totals, ignoring the decorative summary header."""
    totals = {}
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip().lower()
        for label, key in INTER_TOTAL_LABELS.items():
            if line.startswith(label + " "):
                match = re.search(r"R\$\s*([\d.]+,\d{2})\s*$", line, re.IGNORECASE)
                if match:
                    amount = match.group(1)
                    totals[key] = 0 if re.fullmatch(r"0+(?:\.0+)*,00", amount) else parse_amount(amount)
    return totals


def guess_statement_month(filename: str, text: str) -> str | None:
    for candidate in (filename, text[:5000]):
        date_match = re.search(r"(?:vencimento|fatura)\D{0,50}(\d{2})/(\d{2})/(20\d{2})", candidate, re.IGNORECASE)
        if date_match:
            month, year = int(date_match.group(2)), int(date_match.group(3))
            if 1 <= month <= 12:
                return f"{year:04d}-{month:02d}"
        for pattern in (r"(20\d{2})[-_/](0[1-9]|1[0-2])", r"(0[1-9]|1[0-2])[-_/](20\d{2})"):
            match = re.search(pattern, candidate)
            if match:
                if len(match.group(1)) == 4:
                    return f"{match.group(1)}-{match.group(2)}"
                return f"{match.group(2)}-{match.group(1)}"
    return None
