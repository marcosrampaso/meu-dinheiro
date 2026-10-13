"""Editable local XP deposits, withdrawals, and dated portfolio totals."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import date

from finance import FinanceStore, ValidationError, parse_amount


def imported_flow_kind(description: str, amount_cents: int) -> str:
    label = unicodedata.normalize("NFKD", description).encode("ascii", "ignore").decode().casefold().strip()
    if amount_cents > 0 and re.match(r"^(pix recebido|ted recebida|ted recebido)\b", label):
        return "external"
    if amount_cents < 0 and re.match(r"^(pix enviado|ted enviada|ted enviado)\b", label):
        return "external"
    if re.search(r"\b(pix|ted|transferencia)\b", label) and not re.search(
            r"\b(investimento|aplicacao|resgate|conta investimento)\b", label):
        return "review"
    return "internal"


def _valid_date(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("Informe uma data válida.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("Informe uma data válida.") from exc
    if parsed.isoformat() != value or parsed > date.today():
        raise ValidationError("A data deve ser válida e não pode estar no futuro.")
    return value


def _valid_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError("Registro de investimento inválido.")
    return value


def save_manual_flow(store: FinanceStore, data: dict, flow_id: int | None = None) -> dict:
    occurred_on = _valid_date(data.get("occurred_on"))
    kind = data.get("kind")
    if kind not in ("deposit", "withdrawal"):
        raise ValidationError("Escolha aporte ou resgate.")
    amount_cents = parse_amount(data.get("amount"))
    asset_id = data.get("asset_id")
    if asset_id is not None:
        asset_id = _valid_id(asset_id)
    external = data.get("external", asset_id is None)
    if not isinstance(external, bool) or (asset_id is None and not external):
        raise ValidationError("Informe se o dinheiro entrou ou saiu da XP.")
    with store._connect() as connection:
        if asset_id is not None and not connection.execute(
                "SELECT 1 FROM investment_assets WHERE id = ?", (asset_id,)).fetchone():
            raise ValidationError("Investimento não encontrado na carteira.")
        if flow_id is None:
            cursor = connection.execute("""INSERT INTO investment_manual_flows
                (occurred_on, kind, amount_cents, asset_id, external) VALUES (?, ?, ?, ?, ?)""",
                (occurred_on, kind, amount_cents, asset_id, int(external)))
            flow_id = cursor.lastrowid
        else:
            _valid_id(flow_id)
            cursor = connection.execute("""UPDATE investment_manual_flows
                SET occurred_on = ?, kind = ?, amount_cents = ?, asset_id = ?, external = ? WHERE id = ?""",
                (occurred_on, kind, amount_cents, asset_id, int(external), flow_id))
            if not cursor.rowcount:
                raise ValidationError("Aporte ou resgate não encontrado.")
    return {"id": flow_id, "occurred_on": occurred_on, "kind": kind,
            "amount_cents": amount_cents, "asset_id": asset_id, "external": external}


def delete_manual_flow(store: FinanceStore, flow_id: int) -> None:
    _valid_id(flow_id)
    with store._connect() as connection:
        cursor = connection.execute("DELETE FROM investment_manual_flows WHERE id = ?", (flow_id,))
        if not cursor.rowcount:
            raise ValidationError("Aporte ou resgate não encontrado.")


def save_manual_snapshot(store: FinanceStore, data: dict) -> dict:
    as_of = _valid_date(data.get("as_of"))
    total_cents = parse_amount(data.get("total"))
    with store._connect() as connection:
        connection.execute("""INSERT INTO investment_manual_snapshots (as_of, total_cents)
            VALUES (?, ?) ON CONFLICT(as_of) DO UPDATE SET total_cents = excluded.total_cents""",
            (as_of, total_cents))
    return {"as_of": as_of, "total_cents": total_cents}


def delete_manual_snapshot(store: FinanceStore, as_of: str) -> None:
    as_of = _valid_date(as_of)
    with store._connect() as connection:
        cursor = connection.execute("DELETE FROM investment_manual_snapshots WHERE as_of = ?", (as_of,))
        if not cursor.rowcount:
            raise ValidationError("Posição manual não encontrada.")


def manual_overview(store: FinanceStore) -> dict:
    with store._connect() as connection:
        flows = [dict(row) for row in connection.execute(
            """SELECT f.id, f.occurred_on, f.kind, f.amount_cents, f.asset_id, f.external,
                      a.name AS asset_name, a.category AS asset_category
               FROM investment_manual_flows AS f LEFT JOIN investment_assets AS a ON a.id = f.asset_id
               ORDER BY f.occurred_on DESC, f.id DESC""")]
        snapshots = [dict(row) for row in connection.execute(
            "SELECT as_of, total_cents FROM investment_manual_snapshots ORDER BY as_of DESC")]
        assets = [dict(row) for row in connection.execute("""SELECT a.id, a.name, a.category
            FROM investment_assets AS a JOIN investment_positions AS p
              ON p.name = a.name AND p.category = a.category
            WHERE p.as_of = (SELECT MAX(as_of) FROM investment_snapshots)
            ORDER BY p.value_cents DESC, a.name""")]
        imported = [dict(row) for row in connection.execute("""SELECT e.occurred_at, e.description,
            e.amount_cents, c.classification FROM investment_entries AS e
            LEFT JOIN investment_flow_classifications AS c ON c.import_key = e.import_key""")]
    matching = Counter((row["occurred_at"][:10], row["amount_cents"])
                       for row in imported if (row["classification"] or imported_flow_kind(
                           row["description"], row["amount_cents"])) == "external")
    for flow in reversed(flows):
        amount = flow["amount_cents"] * (1 if flow["kind"] == "deposit" else -1)
        key = (flow["occurred_on"], amount)
        flow["external"] = bool(flow["external"])
        flow["matched_import"] = flow["external"] and matching[key] > 0
        if flow["matched_import"]:
            matching[key] -= 1
    totals = {asset["id"]: {**asset, "deposited_cents": 0, "withdrawn_cents": 0} for asset in assets}
    for flow in flows:
        if flow["asset_id"] in totals:
            key = "deposited_cents" if flow["kind"] == "deposit" else "withdrawn_cents"
            totals[flow["asset_id"]][key] += flow["amount_cents"]
    return {"flows": flows, "snapshots": snapshots, "assets": assets,
            "asset_totals": list(totals.values())}
