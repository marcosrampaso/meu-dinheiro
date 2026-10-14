"""Dated observations of the same XP assets-and-cash valuation basis."""

import json
import re
import unicodedata


def received_income(description: str, amount_cents: int) -> bool:
    """Identify posted distributions; redemption principal is not a distribution."""
    plain = unicodedata.normalize("NFKD", description).encode("ascii", "ignore").decode().casefold()
    return amount_cents > 0 and not re.search(r"\b(resgate|venda)\b", plain) and bool(re.match(
        r"^\s*(?:(?:credito|pagamento|transferencia)\s+(?:(?:de|do|dos)\s+)?)?"
        r"(?:dividendo[s]?|provento[s]?|rendimento[s]?|jcp|juros sobre (?:o )?capital)\b", plain))


def capture_observation(connection, snapshot):
    assets = snapshot["assets"]
    if snapshot["assets_cents"] != sum(a["value_cents"] for a in assets) or \
            snapshot["total_cents"] != snapshot["assets_cents"] + snapshot["cash_cents"]:
        raise ValueError("A posição XP não confere com os ativos e o saldo disponível.")
    connection.execute("""INSERT INTO investment_observations
        (as_of,checked_at,assets_cents,cash_cents,total_cents,assets_json)
        VALUES (:as_of,:checked_at,:assets_cents,:cash_cents,:total_cents,:assets_json)
        ON CONFLICT(as_of,checked_at) DO UPDATE SET assets_cents=excluded.assets_cents,
        cash_cents=excluded.cash_cents,total_cents=excluded.total_cents,assets_json=excluded.assets_json""",
        {**snapshot, "assets_json": json.dumps(assets, ensure_ascii=False)})


def seed_observations(connection):
    """Retain existing real Pluggy snapshots, without inventing historical values."""
    for row in connection.execute("""SELECT * FROM investment_snapshots s WHERE provider='pluggy'
        AND checked_at IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM investment_observations o WHERE o.as_of=s.as_of)""").fetchall():
        snapshot = dict(row)
        snapshot["assets"] = [dict(r) for r in connection.execute("""SELECT name,category,value_cents,
            cost_cents,cost_known,quantity,net_cents,quote_date FROM investment_positions WHERE as_of=?""",
            (snapshot["as_of"],))]
        if snapshot["assets"]:
            capture_observation(connection, snapshot)


def daily_observations(connection):
    """Preserve all synchronizations; chart the last observation of each day."""
    days = {}
    for row in connection.execute("SELECT * FROM investment_observations ORDER BY as_of,checked_at,id"):
        item = dict(row)
        assets = json.loads(item.pop("assets_json"))
        quote_dates = [a["quote_date"] for a in assets if a.get("quote_date")]
        item["oldest_quote_on"] = min(quote_dates) if quote_dates else None
        item["newest_quote_on"] = max(quote_dates) if quote_dates else None
        item["missing_quote_dates"] = sum(not a.get("quote_date") for a in assets)
        days[item["as_of"]] = item
    return list(days.values())
