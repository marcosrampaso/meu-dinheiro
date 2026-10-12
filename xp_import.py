"""Read XP cash-account statements as investment evidence, outside cash flow."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from datetime import datetime

from finance import ValidationError, parse_amount


def _signed_cents(value: str) -> int:
    if not isinstance(value, str):
        raise ValidationError("Valor inválido no extrato da XP.")
    raw = value.strip()
    negative = raw.startswith("-")
    if negative:
        raw = raw[1:].strip()
    if re.fullmatch(r"R\$\s*0,00", raw):
        return 0
    cents = parse_amount(raw)
    return -cents if negative else cents


def parse_xp_statement(filename: str, content: str) -> list[dict]:
    if not isinstance(filename, str) or not filename.lower().endswith(".csv"):
        raise ValidationError("Selecione um extrato CSV da XP.")
    if not isinstance(content, str) or len(content.encode("utf-8")) > 2_000_000:
        raise ValidationError("O CSV da XP deve ter até 2 MB.")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), delimiter=";")
    expected = ["Data", "Hora", "Descricao", "Valor", "Saldo"]
    if reader.fieldnames != expected:
        raise ValidationError("Formato da XP não reconhecido. Esperadas: Data, Hora, Descricao, Valor, Saldo.")
    entries = []
    occurrences: Counter[str] = Counter()
    for row in reader:
        if len(entries) >= 5000:
            raise ValidationError("O extrato da XP tem mais de 5.000 lançamentos.")
        if None in row or any(row[column] is None for column in expected):
            raise ValidationError("Linha incompleta no extrato da XP.")
        try:
            occurred_at = datetime.strptime(f"{row['Data']} {row['Hora']}", "%d/%m/%y %H:%M:%S")
        except ValueError as exc:
            raise ValidationError("Data ou hora inválida no extrato da XP.") from exc
        description = row["Descricao"].strip()
        if not description or len(description) > 240:
            raise ValidationError("Descrição inválida no extrato da XP.")
        amount_cents = _signed_cents(row["Valor"])
        if not amount_cents:
            raise ValidationError("Lançamento de valor zero no extrato da XP.")
        balance_cents = _signed_cents(row["Saldo"])
        identity = f"{occurred_at.isoformat()}|{description}|{amount_cents}|{balance_cents}"
        occurrences[identity] += 1
        import_key = hashlib.sha256(f"xp|{identity}|{occurrences[identity]}".encode()).hexdigest()
        entries.append({"import_key": import_key, "occurred_at": occurred_at.isoformat(),
                        "occurred_on": occurred_at.date().isoformat(), "description": description,
                        "amount_cents": amount_cents, "balance_cents": balance_cents})
    if not entries:
        raise ValidationError("O extrato da XP não contém lançamentos.")
    return entries
