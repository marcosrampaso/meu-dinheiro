"""Replace reviewed Pluggy periods atomically, retaining user classifications."""

import os
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timezone

from finance import merchant_key, _description_words


def covered_start(store, source, scope, remote_start):
    """Retain the known interval if the oldest remote row was deleted on refresh."""
    with store._connect() as connection:
        previous = connection.execute("SELECT MIN(starts_on) FROM authoritative_periods WHERE source=? AND scope=?",
                                      (source, scope)).fetchone()[0]
    today = date.today()
    try:
        floor = today.replace(year=today.year-1).isoformat()
    except ValueError:
        floor = today.replace(year=today.year-1, day=28).isoformat()
    return max(floor, min(remote_start, previous)) if previous else remote_start


def backup(store):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = store.path.with_name(store.path.name + f".before-pluggy-{stamp}.bak")
    with store._connect() as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
    os.chmod(path, 0o600)
    return path.name


def _old_inter(connection, periods):
    rows = {}
    for period in periods:
        for row in connection.execute(
            "SELECT * FROM transactions WHERE source=? AND scope=? AND occurred_on BETWEEN ? AND ?",
            (period["source"], period["scope"], period["starts_on"], period["ends_on"]),
        ):
            rows[row["id"]] = dict(row)
    return list(rows.values())


def replacement_counts(store, periods):
    with store._connect() as connection:
        return len(_old_inter(connection, periods))


def _matches(item, old):
    keyed = [row for row in old if row["import_key"] == item["import_key"]]
    if keyed and keyed[0]["category"] != "Importado":
        return keyed
    same = [row for row in old if row["occurred_on"] == item["occurred_on"]
            and row["amount_cents"] == item["amount_cents"] and row["kind"] == item["kind"]]
    exact = [row for row in same if merchant_key(row["description"]) == merchant_key(item["description"])
             and merchant_key(item["description"])]
    if exact:
        return exact + [r for r in keyed if r not in exact]
    words = _description_words(item["description"])
    similar = [row for row in same if words and
               len(words & _description_words(row["description"])) / max(1, len(words | _description_words(row["description"]))) >= .5]
    return similar + [r for r in keyed if r not in similar]


def apply_inter(store, pending):
    saved = backup(store)
    preserved = 0
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        old = _old_inter(connection, pending["periods"])
        memory = {(r["scope"], r["merchant_key"]): r["category"] for r in connection.execute("SELECT * FROM category_memory")}
        old_by_source = defaultdict(list)
        for row in old:
            old_by_source[row["source"]].append(row)
        if old:
            connection.executemany("DELETE FROM transactions WHERE id=?", [(r["id"],) for r in old])
        remapped = {}
        for original in pending["items"]:
            item = dict(original)
            matches = _matches(item, old_by_source[item["source"]])
            classified = {(r["category"], r["excluded"]) for r in matches if r["category"] != "Importado"}
            if item["category"] == "Importado" and len(classified) == 1:
                item["category"], item["excluded"] = classified.pop()
                preserved += 1
            elif item["category"] == "Importado":
                remembered = memory.get((item["scope"], merchant_key(item["description"])))
                if remembered:
                    item["category"] = remembered
                    preserved += 1
            connection.execute("""INSERT INTO transactions
                (kind,description,category,amount_cents,occurred_on,excluded,source,import_key,invoice_month,scope,pending)
                VALUES (:kind,:description,:category,:amount_cents,:occurred_on,:excluded,:source,:import_key,:invoice_month,:scope,:pending)
                ON CONFLICT(import_key) DO UPDATE SET kind=excluded.kind,description=excluded.description,
                category=excluded.category,amount_cents=excluded.amount_cents,occurred_on=excluded.occurred_on,
                excluded=excluded.excluded,invoice_month=excluded.invoice_month,pending=excluded.pending""", item)
            new_id = connection.execute("SELECT id FROM transactions WHERE import_key=?", (item["import_key"],)).fetchone()[0]
            for row in matches:
                remapped[row["id"]] = new_id
        for row in old:
            connection.execute("UPDATE investment_entries SET matched_transaction_id=? WHERE matched_transaction_id=?",
                               (remapped.get(row["id"]), row["id"]))
        for period in pending["periods"]:
            connection.execute("INSERT OR REPLACE INTO authoritative_periods(source,scope,starts_on,ends_on) VALUES (?,?,?,?)",
                               (period["source"], period["scope"], period["starts_on"], period["ends_on"]))
            if "conta corrente" in period["source"]:
                connection.execute("INSERT OR IGNORE INTO statement_coverage VALUES (?,?,?,?)",
                                   (period["source"], period["scope"], period["starts_on"], period["ends_on"]))
        balance = pending["balance"]
        if balance:
            connection.execute("""INSERT INTO account_balances VALUES (?,?,?,?)
                ON CONFLICT(source,scope,as_of) DO UPDATE SET amount_cents=excluded.amount_cents""",
                (balance["source"], "personal", balance["as_of"], balance["amount_cents"]))
    store.reconcile_internal_transfers()
    return {"imported": len(pending["items"]), "replaced": len(old), "skipped": 0,
            "categories_preserved": preserved, "balance_updated": bool(pending["balance"]), "backup": saved}


def apply_xp(store, pending):
    saved = backup(store)
    entries, snapshot = pending["entries"], pending["snapshot"]
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        old = []
        if pending["period"]:
            period = pending["period"]
            old = [dict(r) for r in connection.execute("""SELECT e.*,c.classification,c.origin
                FROM investment_entries e LEFT JOIN investment_flow_classifications c USING(import_key)
                WHERE source='XP' AND substr(occurred_at,1,10) BETWEEN ? AND ?""",
                (period["starts_on"], period["ends_on"]))]
            connection.executemany("DELETE FROM investment_flow_classifications WHERE import_key=?", [(r["import_key"],) for r in old])
            connection.executemany("DELETE FROM investment_entries WHERE import_key=?", [(r["import_key"],) for r in old])
            connection.execute("INSERT OR REPLACE INTO authoritative_periods(source,scope,starts_on,ends_on) VALUES ('XP','personal',?,?)",
                               (period["starts_on"], period["ends_on"]))
        for entry in entries:
            previous = [r for r in old if r["import_key"] == entry["import_key"] or
                        (r["occurred_at"][:10] == entry["occurred_on"] and r["amount_cents"] == entry["amount_cents"]
                         and (merchant_key(r["description"]) == merchant_key(entry["description"]) or
                              (_description_words(r["description"]) and _description_words(r["description"]) == _description_words(entry["description"]))))]
            overrides = {r["classification"] for r in previous if r["classification"] and r["origin"] == "manual"}
            has_override = len(overrides) == 1
            classification = overrides.pop() if len(overrides) == 1 else entry.get("classification")
            connection.execute("""INSERT INTO investment_entries
                (import_key,occurred_at,description,amount_cents,balance_cents,source)
                VALUES (:import_key,:occurred_at,:description,:amount_cents,:balance_cents,'XP')
                ON CONFLICT(import_key) DO UPDATE SET occurred_at=excluded.occurred_at,
                description=excluded.description,amount_cents=excluded.amount_cents""", entry)
            if classification:
                connection.execute("INSERT OR REPLACE INTO investment_flow_classifications(import_key,classification,origin) VALUES (?,?,?)",
                                   (entry["import_key"], classification, "manual" if has_override else "pluggy"))
            linked = {r["matched_transaction_id"] for r in previous if r["matched_transaction_id"] is not None}
            if len(linked) == 1:
                connection.execute("UPDATE investment_entries SET matched_transaction_id=? WHERE import_key=?", (linked.pop(), entry["import_key"]))
        matches = store._xp_matches(connection, entries)
        for match in matches:
            connection.execute("UPDATE transactions SET category='Transferência para investimento',excluded=1 WHERE id=?", (match["transaction_id"],))
            connection.execute("UPDATE investment_entries SET matched_transaction_id=? WHERE import_key=?", (match["transaction_id"], match["xp_import_key"]))
        connection.execute("""INSERT INTO investment_snapshots
            (as_of,total_cents,assets_cents,receivables_cents,cash_cents,provider,complete,checked_at)
            VALUES (:as_of,:total_cents,:assets_cents,0,:cash_cents,'pluggy',0,:checked_at)
            ON CONFLICT(as_of) DO UPDATE SET total_cents=excluded.total_cents,assets_cents=excluded.assets_cents,
            receivables_cents=0,cash_cents=excluded.cash_cents,provider='pluggy',complete=0,checked_at=excluded.checked_at""", snapshot)
        connection.execute("DELETE FROM investment_positions WHERE as_of=?", (snapshot["as_of"],))
        connection.execute("DELETE FROM investment_manual_snapshots WHERE as_of=?", (snapshot["as_of"],))
        for asset in snapshot["assets"]:
            connection.execute("""INSERT INTO investment_positions
                (as_of,name,category,value_cents,cost_cents,quantity,net_cents,cost_known,quote_date)
                VALUES (:as_of,:name,:category,:value_cents,:cost_cents,:quantity,:net_cents,:cost_known,:quote_date)""",
                {**asset, "as_of": snapshot["as_of"]})
            connection.execute("INSERT OR IGNORE INTO investment_assets(name,category) VALUES (?,?)", (asset["name"],asset["category"]))
    return {"imported": len(entries), "replaced": len(old), "asset_count": len(snapshot["assets"]),
            "as_of": snapshot["as_of"], "backup": saved, "complete": False}
