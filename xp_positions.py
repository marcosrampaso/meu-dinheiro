"""Read the XP 'PosicaoDetalhada.xlsx' portfolio snapshot without external packages."""

from __future__ import annotations

import base64
import binascii
import io
import re
import zipfile
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from xml.etree import ElementTree as ET

from finance import ValidationError, parse_amount


NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _cells(content: bytes) -> list[dict[str, str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            if "xl/worksheets/sheet1.xml" not in names or "xl/sharedStrings.xml" not in names:
                raise ValidationError("Planilha de posição da XP não reconhecida.")
            if sum(item.file_size for item in archive.infolist()) > 10_000_000:
                raise ValidationError("A planilha descompactada é muito grande.")
            shared = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(node.itertext()) for node in shared.findall(f"{NS}si")]
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, IndexError, ValueError, ET.ParseError) as exc:
        raise ValidationError("Arquivo XLSX inválido ou não reconhecido.") from exc
    result = []
    for row in sheet.findall(f".//{NS}sheetData/{NS}row"):
        if len(result) >= 1000:
            raise ValidationError("A planilha de posição tem linhas demais.")
        values = {}
        for cell in row.findall(f"{NS}c"):
            column = re.match(r"[A-Z]+", cell.get("r", ""))
            if not column:
                continue
            value = cell.find(f"{NS}v")
            if value is not None and value.text is not None:
                raw = value.text
                try:
                    values[column.group()] = strings[int(raw)] if cell.get("t") == "s" else raw
                except (ValueError, IndexError) as exc:
                    raise ValidationError("Referência de texto inválida na planilha da XP.") from exc
            elif cell.get("t") == "inlineStr":
                values[column.group()] = "".join(cell.itertext())
        result.append(values)
    return result


def _money(raw: str) -> int:
    return 0 if re.fullmatch(r"R\$\s*0,00", raw.strip()) else parse_amount(raw)


def _cost(price: str, quantity: str) -> int:
    price_cents = _money(price)
    try:
        units = Decimal(quantity.replace(",", "."))
    except Exception as exc:
        raise ValidationError("Quantidade inválida na posição da XP.") from exc
    if units <= 0:
        raise ValidationError("Quantidade inválida na posição da XP.")
    return int((Decimal(price_cents) * units).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_xp_positions(filename: str, content_base64: str) -> dict:
    if not isinstance(filename, str) or not filename.lower().endswith(".xlsx"):
        raise ValidationError("Selecione a planilha PosicaoDetalhada.xlsx da XP.")
    if not isinstance(content_base64, str) or len(content_base64) > 2_800_000:
        raise ValidationError("A planilha da XP deve ter até 2 MB.")
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValidationError("Arquivo XLSX inválido.") from exc
    if len(content) > 2_000_000:
        raise ValidationError("A planilha da XP deve ter até 2 MB.")
    rows = _cells(content)
    if len(rows) < 4 or rows[2].get("B") != "Total investido":
        raise ValidationError("Formato da posição detalhada da XP não reconhecido.")
    metadata = " ".join(row.get("F", "") for row in rows[:2])
    match = re.search(r"(\d{2}/\d{2}/\d{4}),\s*\d{2}:\d{2}", metadata)
    if not match:
        raise ValidationError("Data da posição da XP não encontrada.")
    try:
        as_of = datetime.strptime(match.group(1), "%d/%m/%Y").date().isoformat()
        total_cents = _money(rows[3].get("A", ""))
        cash_cents = _money(rows[3].get("C", ""))
    except ValueError as exc:
        raise ValidationError("Data ou valor total inválido na posição da XP.") from exc
    assets = []
    receivables_cents = 0
    section = None
    for row in rows[4:]:
        name = row.get("A", "").strip()
        if name == "Dividendos, proventos e outras distribuições":
            section = "receivables"
        elif section != "receivables" and name in ("Ações", "Fundos de Investimentos", "Fundos Imobiliários"):
            section = name
        if not name or name.startswith("Dividendos") or name in (
            "Ações", "Fundos de Investimentos", "Fundos Imobiliários", "Proventos", "Custódia Remunerada"
        ) or " | " in name:
            continue
        if section == "Ações" and row.get("B", "").startswith("R$"):
            assets.append({"name": name, "category": section, "value_cents": _money(row["B"]),
                           "cost_cents": _cost(row.get("E", ""), row.get("G", "")),
                           "quantity": row.get("G", ""), "net_cents": None})
        elif section == "Fundos de Investimentos" and row.get("C", "").startswith("R$"):
            assets.append({"name": name, "category": section, "value_cents": _money(row["C"]),
                           "cost_cents": _money(row.get("E", "")), "quantity": None,
                           "net_cents": _money(row.get("F", ""))})
        elif section == "Fundos Imobiliários" and row.get("B", "").startswith("R$"):
            assets.append({"name": name, "category": section, "value_cents": _money(row["B"]),
                           "cost_cents": _cost(row.get("D", ""), row.get("F", "")),
                           "quantity": row.get("F", ""), "net_cents": None})
        elif section == "receivables" and row.get("D", "").startswith("R$"):
            receivables_cents += _money(row["D"])
    assets_cents = sum(item["value_cents"] for item in assets)
    if not assets or assets_cents + receivables_cents + cash_cents != total_cents:
        raise ValidationError("A soma dos ativos, proventos e saldo não bate com o total da XP.")
    return {"as_of": as_of, "total_cents": total_cents, "assets_cents": assets_cents,
            "receivables_cents": receivables_cents, "cash_cents": cash_cents, "assets": assets}
