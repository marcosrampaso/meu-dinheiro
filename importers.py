"""Parse local CSV, OFX, and PDF statements without connecting to a bank."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from finance import ValidationError, parse_amount, validate_month, validate_scope
from pdf_import import extract_pdf_text, guess_statement_month, inter_statement_totals, statement_rows


MAX_ROWS = 5000
MAX_CONTENT_CHARS = 2_000_000
BANK_NAMES = {"inter": "Inter", "bb": "Banco do Brasil"}


def _names_xp_investimentos(text: str) -> bool:
    """Require an explicit institution name, not a person's name or a bare 'XP'."""
    normalized = _normal(text)
    return bool(re.search(r"\bbanco\s+xp\b|\bxp\s+banco\b|"
                          r"\bxp\b.{0,45}\b(?:investimentos|corretora|cctvm)\b|"
                          r"\b(?:investimentos|corretora|cctvm)\b.{0,45}\bxp\b", normalized))


def _format(filename: str, content: str | None, content_base64: str | None = None) -> str:
    if not isinstance(filename, str):
        raise ValidationError("Arquivo inválido.")
    suffix = Path(filename).suffix.lower()
    if suffix not in (".csv", ".ofx", ".pdf"):
        raise ValidationError("Use um arquivo CSV, OFX ou PDF.")
    if suffix == ".pdf":
        if not isinstance(content_base64, str):
            raise ValidationError("Dados do PDF inválidos.")
    elif not isinstance(content, str) or len(content) > MAX_CONTENT_CHARS:
        raise ValidationError("O arquivo CSV ou OFX deve ter até 2 MB.")
    return suffix[1:]


def _csv_rows(content: str) -> tuple[list[str], list[list[str]]]:
    content = content.lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(content[:8192], delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        first_line = content.splitlines()[0] if content.splitlines() else ""
        delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    rows = [row for row in csv.reader(io.StringIO(content), delimiter=delimiter) if any(cell.strip() for cell in row)]
    def looks_like_header(row: list[str]) -> bool:
        mapping = _suggest([cell.strip() for cell in row])
        return (mapping["date"] is not None and mapping["description"] is not None
                and (mapping["amount"] is not None or mapping["debit"] is not None
                     or mapping["credit"] is not None))

    header_index = next((index for index, row in enumerate(rows) if looks_like_header(row)), 0)
    rows = rows[header_index:]
    if len(rows) < 2:
        raise ValidationError("O CSV precisa ter cabeçalho e pelo menos um lançamento.")
    columns = [column.strip() for column in rows[0]]
    if not 2 <= len(columns) <= 100 or any(not column for column in columns):
        raise ValidationError("O cabeçalho do CSV é inválido.")
    if len(rows) - 1 > MAX_ROWS:
        raise ValidationError("Importe no máximo 5000 lançamentos por arquivo.")
    for line_number, row in enumerate(rows[1:], start=2):
        if len(row) > len(columns):
            raise ValidationError(f"A linha {line_number} tem colunas extras.")
    return columns, [row + [""] * (len(columns) - len(row)) for row in rows[1:]]


def _normal(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(char)
    )


def _suggest(columns: list[str]) -> dict[str, int | None]:
    rules = {
        "date": ("data", "date", "dt", "data compra", "data lancamento"),
        "description": ("descricao", "historico", "estabelecimento", "lancamento", "detalhe", "transacao"),
        "amount": ("valor", "valor rs", "valor r$", "valor transacao", "amount"),
        "debit": ("debito", "saida", "valor debito"),
        "credit": ("credito", "entrada", "valor credito"),
        "id": ("id", "fitid", "identificador", "id transacao"),
    }
    mapping: dict[str, int | None] = {}
    normalized_columns = [_normal(name).strip() for name in columns]
    for key, names in rules.items():
        mapping[key] = next((index for preferred in names for index, name in enumerate(normalized_columns)
                             if name == preferred), None)
    if mapping["debit"] is not None and mapping["credit"] is not None:
        mapping["amount"] = None
    return mapping


def _ofx_field(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}\b[^>]*>\s*([^<\r\n]+)", block, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _ofx_rows(content: str) -> list[dict[str, str]]:
    blocks = re.findall(
        r"<STMTTRN\b[^>]*>(.*?)(?:</STMTTRN\s*>|(?=<STMTTRN\b)|$)",
        content, flags=re.IGNORECASE | re.DOTALL,
    )
    if not blocks:
        raise ValidationError("Nenhuma transação foi encontrada no OFX.")
    if len(blocks) > MAX_ROWS:
        raise ValidationError("Importe no máximo 5000 lançamentos por arquivo.")
    return [
        {
            "date": _ofx_field(block, "DTPOSTED"),
            "amount": _ofx_field(block, "TRNAMT"),
            "description": _ofx_field(block, "NAME") or _ofx_field(block, "MEMO") or "Lançamento",
            "id": _ofx_field(block, "FITID"),
        }
        for block in blocks
    ]


def inspect_import(filename: str, content: str | None = None, content_base64: str | None = None, pdf_password: str = "") -> dict:
    file_format = _format(filename, content, content_base64)
    if file_format == "csv":
        columns, rows = _csv_rows(content)
        return {
            "format": "csv", "columns": columns, "suggested_mapping": _suggest(columns),
            "sample": rows[:3], "count": len(rows),
            "account_balance": account_balance_from_csv(content),
        }
    if file_format == "ofx":
        rows = _ofx_rows(content)
        return {"format": "ofx", "columns": [], "suggested_mapping": {}, "sample": rows[:3], "count": len(rows)}
    text = extract_pdf_text(content_base64, pdf_password)
    rows = statement_rows(text)
    totals = inter_statement_totals(text) if any(row.get("style") == "inter" for row in rows) else {}
    parsed_invoice_cents = sum(
        -_signed_cents(row["amount"], index)
        for index, row in enumerate(rows, start=1)
    ) if totals else None
    return {
        "format": "pdf", "columns": [], "suggested_mapping": {},
        "sample": rows[:3], "count": len(rows),
        "statement_month": guess_statement_month(filename, text),
        "suggested_expense_sign": "negative" if any(row.get("style") == "inter" for row in rows) else "positive",
        "reconciliation": {**totals, "parsed_invoice_cents": parsed_invoice_cents,
                           "matches": parsed_invoice_cents == totals.get("invoice_cents")}
        if "invoice_cents" in totals else None,
    }


def _date(value: str, line_number: int) -> str:
    value = value.strip()
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%Y%m%d"):
        try:
            return datetime.strptime(value[:10], pattern).date().isoformat()
        except ValueError:
            pass
    raise ValidationError(f"Data inválida na linha {line_number}: {value[:20]}")


def _pdf_date(value: str, statement_month: str, line_number: int) -> str:
    if re.fullmatch(r"\d{2}/\d{2}", value):
        day, month = map(int, value.split("/"))
        invoice_year, invoice_number = map(int, statement_month.split("-"))
        year = invoice_year - 1 if month > invoice_number else invoice_year
        try:
            return date(year, month, day).isoformat()
        except ValueError as exc:
            raise ValidationError(f"Data inválida na linha {line_number}: {value}") from exc
    return _date(value, line_number)


def _signed_cents(value: str, line_number: int) -> int:
    raw = re.sub(r"\s+", "", value.replace("\u2212", "-").replace("R$", ""))
    if not raw:
        raise ValidationError(f"Valor vazio na linha {line_number}.")
    if not re.fullmatch(r"(?:[-+]?[\d.,]+|[\d.,]+-|\([\d.,]+\))", raw):
        raise ValidationError(f"Valor inválido na linha {line_number}: {value[:30]}")
    negative = raw.startswith("-") or raw.endswith("-") or (raw.startswith("(") and raw.endswith(")"))
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    elif raw.startswith("-") or raw.endswith("-"):
        raw = raw.strip("-")
    elif raw.startswith("+"):
        raw = raw[1:]
    try:
        cents = parse_amount(raw)
    except ValidationError as exc:
        raise ValidationError(f"Valor inválido na linha {line_number}: {value[:30]}") from exc
    return -cents if negative else cents


def account_balance_from_csv(content: str) -> dict | None:
    """Return a dated bank balance when a CSV contains a 'Saldo' metadata line."""
    if not isinstance(content, str):
        return None
    balance = None
    for row in csv.reader(io.StringIO(content.lstrip("\ufeff")), delimiter=";"):
        if len(row) >= 2 and _normal(row[0]).strip() == "saldo":
            raw = row[1].strip()
            balance = 0 if re.fullmatch(r"[-+]?0+(?:\.0+)*,00", raw) else _signed_cents(raw, 1)
            break
    if balance is None:
        return None
    columns, rows = _csv_rows(content)
    date_index = _suggest(columns)["date"]
    if date_index is None:
        return None
    as_of = max(_date(row[date_index], index) for index, row in enumerate(rows, start=2))
    return {"as_of": as_of, "amount_cents": balance}


def account_period_from_csv(content: str) -> dict | None:
    """Read an explicit statement interval from the CSV preamble, if present."""
    if not isinstance(content, str):
        return None
    for row in csv.reader(io.StringIO(content.lstrip("\ufeff")), delimiter=";"):
        if len(row) >= 2 and _normal(row[0]).strip() == "periodo":
            match = re.fullmatch(r"\s*(\d{2}/\d{2}/\d{4})\s+a\s+(\d{2}/\d{2}/\d{4})\s*", row[1])
            if not match:
                return None
            starts_on, ends_on = (_date(value, 1) for value in match.groups())
            if starts_on > ends_on:
                raise ValidationError("Período do extrato inválido.")
            return {"starts_on": starts_on, "ends_on": ends_on}
    return None


def account_daily_balances_from_csv(content: str) -> list[dict]:
    """Take the first (end-of-day) balance for each date in a newest-first CSV."""
    if not isinstance(content, str):
        return []
    columns, rows = _csv_rows(content)
    date_index = _suggest(columns)["date"]
    balance_index = next((index for index, column in enumerate(columns)
                          if _normal(column).strip() == "saldo"), None)
    if date_index is None or balance_index is None:
        return []
    daily = {}
    for line_number, row in enumerate(rows, start=2):
        as_of = _date(row[date_index], line_number)
        if as_of not in daily:
            raw = row[balance_index].strip()
            if not raw:
                continue
            daily[as_of] = 0 if re.fullmatch(r"[-+]?0+(?:\.0+)*,00", raw) else _signed_cents(raw, line_number)
    return [{"as_of": day, "amount_cents": daily[day]} for day in sorted(daily)]


def _column(mapping: dict, name: str, length: int, required: bool = False) -> int | None:
    index = mapping.get(name)
    if index is None or index == "":
        if required:
            raise ValidationError(f"Selecione a coluna de {name}.")
        return None
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < length:
        raise ValidationError(f"Coluna de {name} inválida.")
    return index


def prepare_import(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValidationError("Dados da importação inválidos.")
    filename = payload.get("filename")
    content = payload.get("content")
    content_base64 = payload.get("content_base64")
    file_format = _format(filename, content, content_base64)
    bank = payload.get("bank")
    mode = payload.get("mode")
    account = payload.get("account")
    scope = validate_scope(payload.get("scope", "personal"))
    if bank not in BANK_NAMES:
        raise ValidationError("Escolha Inter ou Banco do Brasil.")
    if mode not in ("account", "card"):
        raise ValidationError("Escolha conta ou cartão.")
    expense_sign = payload.get("expense_sign", "positive" if mode == "card" else "negative")
    if expense_sign not in ("positive", "negative"):
        raise ValidationError("Escolha como as despesas aparecem no extrato.")
    if not isinstance(account, str) or not 1 <= len(account.strip()) <= 50:
        raise ValidationError("Identifique a conta ou o cartão com até 50 caracteres.")
    account = account.strip()
    source = f"{BANK_NAMES[bank]} · {account}"
    excluded_indices: set[int] = set()
    investment_indices: set[int] = set()
    reversed_pix_indices: set[int] = set()
    cashback_indices: set[int] = set()
    advance_indices: set[int] = set()
    if file_format == "csv":
        columns, raw_rows = _csv_rows(content)
        mapping = payload.get("mapping")
        if not isinstance(mapping, dict):
            raise ValidationError("Selecione as colunas do CSV.")
        date_index = _column(mapping, "date", len(columns), required=True)
        description_index = _column(mapping, "description", len(columns), required=True)
        amount_index = _column(mapping, "amount", len(columns))
        debit_index = _column(mapping, "debit", len(columns))
        credit_index = _column(mapping, "credit", len(columns))
        id_index = _column(mapping, "id", len(columns))
        history_index = next((index for index, name in enumerate(columns)
                              if _normal(name).strip() == "historico"), None)
        if amount_index is None and debit_index is None and credit_index is None:
            raise ValidationError("Selecione valor ou as colunas débito/crédito.")
        if amount_index is not None and (debit_index is not None or credit_index is not None):
            raise ValidationError("Use valor único ou débito/crédito, não ambos.")
        normalized_rows = []
        histories = []
        for line_number, row in enumerate(raw_rows, start=2):
            occurred_on = _date(row[date_index], line_number)
            description = row[description_index].strip()[:120]
            if not description:
                raise ValidationError(f"Descrição vazia na linha {line_number}.")
            if amount_index is not None:
                signed = _signed_cents(row[amount_index], line_number)
                kind = "expense" if (signed > 0 and expense_sign == "positive") or (signed < 0 and expense_sign == "negative") else "income"
                cents = abs(signed)
            else:
                debit = _signed_cents(row[debit_index], line_number) if debit_index is not None and row[debit_index].strip() else 0
                credit = _signed_cents(row[credit_index], line_number) if credit_index is not None and row[credit_index].strip() else 0
                if bool(debit) == bool(credit):
                    raise ValidationError(f"Informe débito ou crédito na linha {line_number}.")
                kind = "expense" if debit else "income"
                cents = abs(debit or credit)
            if mode == "account" and kind == "expense":
                details = _normal(" ".join(row))
                if re.search(r"\bpagamento\b.*\bfatura\b|\bfatura\b.*\bpagamento\b", details):
                    excluded_indices.add(len(normalized_rows))
            history = _normal(row[history_index]).strip() if history_index is not None else ""
            if mode == "account":
                if history in ("aplicacao", "resgate"):
                    investment_indices.add(len(normalized_rows))
                elif history == "cashback":
                    cashback_indices.add(len(normalized_rows))
                elif kind == "expense" and _names_xp_investimentos(" ".join(row)):
                    investment_indices.add(len(normalized_rows))
            histories.append(history)
            normalized_rows.append((occurred_on, description, kind, cents, row[id_index].strip() if id_index is not None else ""))
        if mode == "account" and history_index is not None:
            sent = {}
            for index, (occurred_on, description, kind, cents, _) in enumerate(normalized_rows):
                if histories[index] == "pix enviado" and kind == "expense":
                    sent.setdefault((occurred_on, _normal(description), cents), []).append(index)
            for index, (occurred_on, description, kind, cents, _) in enumerate(normalized_rows):
                if histories[index] != "pix enviado devolvido" or kind != "income":
                    continue
                matches = sent.get((occurred_on, _normal(description), cents), [])
                if len(matches) == 1:
                    reversed_pix_indices.update((index, matches[0]))
    elif file_format == "ofx":
        normalized_rows = []
        for line_number, row in enumerate(_ofx_rows(content), start=1):
            occurred_on = _date(row["date"][:8], line_number)
            signed = _signed_cents(row["amount"], line_number)
            kind = "expense" if (signed > 0 and expense_sign == "positive") or (signed < 0 and expense_sign == "negative") else "income"
            normalized_rows.append((occurred_on, row["description"].strip()[:120], kind, abs(signed), row["id"]))
    else:
        statement_month = validate_month(payload.get("statement_month"))
        text = extract_pdf_text(content_base64, payload.get("pdf_password", ""))
        rows = statement_rows(text)
        if "expense_sign" not in payload and any(row["style"] == "inter" for row in rows):
            expense_sign = "negative"
        totals = inter_statement_totals(text) if bank == "inter" and any(row["style"] == "inter" for row in rows) else {}
        normalized_rows = []
        for line_number, row in enumerate(rows, start=1):
            occurred_on = _pdf_date(row["date"], statement_month, line_number)
            signed = _signed_cents(row["amount"], line_number)
            kind = "expense" if (signed > 0 and expense_sign == "positive") or (signed < 0 and expense_sign == "negative") else "income"
            normalized_rows.append((occurred_on, row["description"], kind, abs(signed), ""))
        advance = totals.get("advanced_cents", 0)
        if expense_sign == "negative" and advance:
            candidates = [index for index, item in enumerate(normalized_rows)
                          if item[2] == "income" and item[3] == advance]
            if len(candidates) == 1:
                advance_indices.add(candidates[0])
    if mode == "account":
        for index, (_, description, kind, _, _) in enumerate(normalized_rows):
            if kind == "expense" and _names_xp_investimentos(description):
                investment_indices.add(index)
    seen: Counter[str] = Counter()
    prepared = []
    for index, (occurred_on, description, kind, cents, external_id) in enumerate(normalized_rows):
        identity = f"{bank}|{account}|{mode}|{external_id}" if external_id else f"{bank}|{account}|{mode}|{statement_month if file_format == 'pdf' else ''}|{occurred_on}|{description}|{kind}|{cents}"
        if scope == "business":
            identity = "business|" + identity
        if not external_id:
            seen[identity] += 1
            identity += f"|{seen[identity]}"
        import_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        prepared.append({
            "kind": kind, "description": description, "amount_cents": cents,
            "category": ("Antecipação da fatura" if index in advance_indices else
                         "Pagamento de fatura" if index in excluded_indices else
                         "Estorno de Pix" if index in reversed_pix_indices else
                         "Transferência para investimento" if index in investment_indices else
                         "Cashback" if index in cashback_indices else "Importado"),
            "occurred_on": occurred_on,
            "excluded": index in advance_indices or index in excluded_indices or index in investment_indices
            or index in reversed_pix_indices
            or index in cashback_indices,
            "source": source, "import_key": import_key,
            "invoice_month": statement_month if file_format == "pdf" else None,
            "scope": scope,
        })
    return prepared
