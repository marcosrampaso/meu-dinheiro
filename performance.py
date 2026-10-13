"""Estimate XP account performance between dated snapshots with Modified Dietz."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from finance import FinanceStore, ValidationError
from manual_investments import imported_flow_kind, manual_overview


def classify_flow(store: FinanceStore, import_key: str, classification: str) -> None:
    if not isinstance(import_key, str) or not re.fullmatch(r"[0-9a-f]{64}", import_key):
        raise ValidationError("Movimentação XP inválida.")
    if classification not in ("external", "internal"):
        raise ValidationError("Escolha externo ou interno.")
    with store._connect() as connection:
        row = connection.execute("SELECT 1 FROM investment_entries WHERE import_key = ?", (import_key,)).fetchone()
        if row is None:
            raise ValidationError("Movimentação XP não encontrada.")
        connection.execute("""
            INSERT INTO investment_flow_classifications (import_key, classification) VALUES (?, ?)
            ON CONFLICT(import_key) DO UPDATE SET classification = excluded.classification
        """, (import_key, classification))


def _dietz(start: dict, end: dict, flows: list[dict]) -> Decimal | None:
    begin, finish = date.fromisoformat(start["as_of"]), date.fromisoformat(end["as_of"])
    days = (finish - begin).days
    if days <= 0 or start["total_cents"] <= 0:
        return None
    total_flow = sum(row["amount_cents"] for row in flows)
    weighted = sum((Decimal(row["amount_cents"]) * Decimal((finish - date.fromisoformat(row["occurred_at"][:10])).days)
                    / Decimal(days) for row in flows), Decimal(0))
    denominator = Decimal(start["total_cents"]) + weighted
    if denominator <= 0:
        return None
    return (Decimal(end["total_cents"] - start["total_cents"] - total_flow) / denominator)


def _benchmark_end(start: dict, end: dict, flows: list[dict], rates: list[dict]) -> Decimal:
    """Grow the same starting capital and dated external flows at the daily CDI."""
    value = Decimal(start["total_cents"])
    by_day = {row["quote_date"]: Decimal(row["price"]) for row in rates}
    flow_by_day = {}
    for row in flows:
        day = row["occurred_at"][:10]
        flow_by_day[day] = flow_by_day.get(day, 0) + row["amount_cents"]
    day = date.fromisoformat(start["as_of"]) + timedelta(days=1)
    through = date.fromisoformat(end["as_of"])
    while day <= through:
        key = day.isoformat()
        if key in by_day:
            value *= 1 + by_day[key] / 100
        value += flow_by_day.get(key, 0)
        day += timedelta(days=1)
    return value


def performance_overview(store: FinanceStore) -> dict:
    with store._connect() as connection:
        imported_snapshots = [dict(row) for row in connection.execute("""
            SELECT as_of, total_cents FROM investment_snapshots ORDER BY as_of
        """)]
        manual_snapshots = [dict(row) for row in connection.execute("""
            SELECT as_of, total_cents FROM investment_manual_snapshots ORDER BY as_of
        """)]
        # An explicitly entered total wins for the comparison on the same date.
        snapshots = sorted({row["as_of"]: row for row in imported_snapshots + manual_snapshots}.values(),
                           key=lambda row: row["as_of"])
        if len(snapshots) < 2:
            return {"status": "needs_snapshots", "snapshot_count": len(snapshots),
                    "points": [], "flows": [], "intervals": [],
                    "message": "Registre outro saldo total da XP em uma data diferente ou importe outra posição."}
        first, last = snapshots[0]["as_of"], snapshots[-1]["as_of"]
        entries = [dict(row) for row in connection.execute("""
            SELECT e.import_key, e.occurred_at, e.description, e.amount_cents,
                   c.classification AS override
            FROM investment_entries AS e LEFT JOIN investment_flow_classifications AS c
              ON c.import_key = e.import_key
            WHERE substr(e.occurred_at, 1, 10) > ? AND substr(e.occurred_at, 1, 10) <= ?
            ORDER BY e.occurred_at
        """, (first, last))]
    for entry in entries:
        override = entry.pop("override")
        entry["classification"] = override or imported_flow_kind(entry["description"], entry["amount_cents"])
        entry["manual"] = bool(override)
    visible_flows = [row for row in entries if row["classification"] in ("external", "review") or row["manual"]]
    if any(row["classification"] == "review" for row in entries):
        return {"status": "review_flows", "snapshot_count": len(snapshots),
                "points": [], "flows": visible_flows, "intervals": [],
                "message": "Revise as transferências XP antes de calcular a rentabilidade."}
    manual_flows = manual_overview(store)["flows"]
    for flow in manual_flows:
        if first < flow["occurred_on"] <= last and not flow["matched_import"]:
            entries.append({"occurred_at": flow["occurred_on"] + "T12:00:00",
                            "amount_cents": flow["amount_cents"] * (1 if flow["kind"] == "deposit" else -1),
                            "classification": "external"})
    rates = sorted(store.market_quotes("bcb", "12"), key=lambda row: row["quote_date"])
    portfolio_factor = cdi_factor = Decimal(1)
    points = [{"date": first, "portfolio_percent": 0.0, "cdi_percent": 0.0}]
    intervals = []
    for begin, end in zip(snapshots, snapshots[1:]):
        interval_flows = [row for row in entries if begin["as_of"] < row["occurred_at"][:10] <= end["as_of"]
                          and row["classification"] == "external"]
        result = _dietz(begin, end, interval_flows)
        if result is None or result <= -1:
            return {"status": "invalid_interval", "snapshot_count": len(snapshots),
                    "points": [], "flows": visible_flows, "intervals": [],
                    "message": "Um intervalo não permite calcular a rentabilidade; confira posições e fluxos."}
        period_rates = [row for row in rates if begin["as_of"] < row["quote_date"] <= end["as_of"]]
        if (not period_rates or (date.fromisoformat(period_rates[0]["quote_date"]) -
                                 date.fromisoformat(begin["as_of"])).days > 7 or
                (date.fromisoformat(end["as_of"]) -
                 date.fromisoformat(period_rates[-1]["quote_date"])).days > 7 or
                any((date.fromisoformat(right["quote_date"]) -
                     date.fromisoformat(left["quote_date"])).days > 6
                    for left, right in zip(period_rates, period_rates[1:]))):
            return {"status": "needs_cdi", "snapshot_count": len(snapshots),
                    "points": [], "flows": visible_flows, "intervals": [],
                    "message": "Aguardando taxas CDI do Banco Central para comparar as posições."}
        cdi_end = _benchmark_end(begin, end, interval_flows, period_rates)
        cdi_return = _dietz(begin, {"as_of": end["as_of"], "total_cents": cdi_end}, interval_flows)
        if cdi_return is None:
            return {"status": "invalid_interval", "snapshot_count": len(snapshots),
                    "points": [], "flows": visible_flows, "intervals": [],
                    "message": "Os fluxos do período impedem uma comparação válida."}
        cdi_factor *= 1 + cdi_return
        portfolio_factor *= 1 + result
        points.append({"date": end["as_of"],
                       "portfolio_percent": round(float((portfolio_factor - 1) * 100), 4),
                       "cdi_percent": round(float((cdi_factor - 1) * 100), 4)})
        intervals.append({"starts_on": begin["as_of"], "ends_on": end["as_of"],
                          "external_flow_cents": sum(row["amount_cents"] for row in interval_flows),
                          "return_percent": round(float(result * 100), 4)})
    return {"status": "ready", "snapshot_count": len(snapshots), "points": points,
            "flows": visible_flows, "intervals": intervals,
            "portfolio_percent": points[-1]["portfolio_percent"], "cdi_percent": points[-1]["cdi_percent"],
            "message": "Estimativa por Dietz modificado: posições XP e fluxos externos classificados entre as datas."}
