"""Replace reviewed Pluggy periods atomically, retaining user classifications."""

import os
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timezone, timedelta

from finance import merchant_key, _description_words


def revision(store, source):
    with store._connect() as c:
        row = c.execute('SELECT revision FROM integration_revisions WHERE source=?',(source,)).fetchone()
    return row[0] if row else 0


def advance_revision(connection, source, expected):
    from pluggy_xp import PluggyError
    row = connection.execute('SELECT revision FROM integration_revisions WHERE source=?',(source,)).fetchone()
    current = row[0] if row else 0
    if current != expected:
        raise PluggyError('Os dados foram sincronizados após esta prévia. Consulte novamente antes de importar.')
    connection.execute('INSERT INTO integration_revisions VALUES (?,?) ON CONFLICT(source) DO UPDATE SET revision=excluded.revision',
                       (source,current+1))


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
        if period.get('basis') == 'invoice':
            months = period['invoice_months']
            placeholders = ','.join('?' for _ in months)
            records = connection.execute(
                f"SELECT * FROM transactions WHERE source=? AND scope=? AND invoice_month IN ({placeholders})",
                (period['source'],period['scope'],*months))
        else:
            records = connection.execute(
                "SELECT * FROM transactions WHERE source=? AND scope=? AND occurred_on BETWEEN ? AND ?",
                (period["source"], period["scope"], period["starts_on"], period["ends_on"]))
        for row in records:
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


def same_invoice(items, existing):
    """Recognize a whole invoice, allowing changed merchant text/purchase dates.

    Require equal amount/type multisets and a one-to-one match for at least 90%
    of the rows. A recurring purchase alone is not evidence of a duplicate bill.
    """
    if len(items) < 10 or len(items) != len(existing):
        return False
    signature = lambda rows: Counter((r['kind'], r['amount_cents']) for r in rows)
    if signature(items) != signature(existing):
        return False
    candidates = [[index for index, row in enumerate(existing)
                   if _matches({**item, 'import_key':None}, [{**row, 'import_key':''}])]
                  for item in items]
    assigned = {}

    def assign(item_index, visited):
        for row_index in candidates[item_index]:
            if row_index in visited:
                continue
            visited.add(row_index)
            if row_index not in assigned or assign(assigned[row_index], visited):
                assigned[row_index] = item_index
                return True
        return False

    matched = sum(assign(index, set()) for index in range(len(items)))
    return matched * 10 >= len(items) * 9


def restore_inter_history(store, historical_items, starts_on, statement_periods=()):
    """Explicit recovery: restore reviewed local history, retaining current live rows.

    Call only with the original CSV/PDF records from a trusted local backup.
    This is not an automatic migration or a provider refresh.
    """
    from pluggy_inter import ACCOUNT_SOURCE, CARD_SOURCE, card_payment
    sources = (ACCOUNT_SOURCE, CARD_SOURCE)
    first_day = date.fromisoformat(starts_on)
    previous_month = (first_day - timedelta(days=1)).isoformat()[:7]
    last_invoice_dates = [r['occurred_on'] for r in historical_items
                          if r['source'] == CARD_SOURCE and r['invoice_month'] == previous_month]
    card_starts_on = min(starts_on, (date.fromisoformat(max(last_invoice_dates)) + timedelta(days=1)).isoformat()) if last_invoice_dates else starts_on
    keys = set()
    for item in historical_items:
        if item["source"] not in sources or item["scope"] != "personal" or \
                item["occurred_on"] >= starts_on or not item["import_key"] or \
                (item["invoice_month"] and item["invoice_month"] >= starts_on[:7]) or \
                item["import_key"] in keys:
            raise ValueError("A recuperação deve conter somente o histórico original anterior ao início da Pluggy.")
        keys.add(item["import_key"])
    saved = backup(store)
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        old = [dict(r) for r in connection.execute(
            "SELECT * FROM transactions WHERE scope='personal' AND source IN (?,?)", sources)]
        removed = [r for r in old if r["occurred_on"] < (card_starts_on if r['source'] == CARD_SOURCE else starts_on) or
                   (r["invoice_month"] and r["invoice_month"] < starts_on[:7]) or
                   (r["source"] == CARD_SOURCE and card_payment({}, r["description"]))]
        connection.executemany("DELETE FROM transactions WHERE id=?", [(r["id"],) for r in removed])
        memory = {(r["scope"], r["merchant_key"]): r["category"]
                  for r in connection.execute("SELECT * FROM category_memory")}
        restored = []
        for original in historical_items:
            item = dict(original)
            if item["kind"] == "expense" and not item["excluded"]:
                item["category"] = memory.get((item["scope"], merchant_key(item["description"])), item["category"])
            item["pending"] = 0
            connection.execute("""INSERT INTO transactions
                (kind,description,category,amount_cents,occurred_on,excluded,source,import_key,invoice_month,scope,pending)
                VALUES (:kind,:description,:category,:amount_cents,:occurred_on,:excluded,:source,:import_key,:invoice_month,:scope,:pending)""", item)
            item["id"] = connection.execute("SELECT id FROM transactions WHERE import_key=?", (item["import_key"],)).fetchone()[0]
            restored.append(item)
        for row in removed:
            matches = _matches(row, [r for r in restored if r["source"] == row["source"]])
            connection.execute("UPDATE investment_entries SET matched_transaction_id=? WHERE matched_transaction_id=?",
                               (matches[0]["id"] if len(matches) == 1 else None, row["id"]))
        connection.execute("INSERT OR REPLACE INTO pluggy_inter_policy(singleton,starts_on,card_starts_on) VALUES (1,?,?)", (starts_on, card_starts_on))
        for source in sources:
            connection.execute("DELETE FROM authoritative_periods WHERE source=? AND scope='personal'", (source,))
            connection.execute("INSERT INTO authoritative_periods(source,scope,starts_on,ends_on) VALUES (?,'personal',?,?)",
                               (source, card_starts_on if source == CARD_SOURCE else starts_on, date.today().isoformat()))
        connection.execute("DELETE FROM statement_coverage WHERE source=? AND scope='personal'", (ACCOUNT_SOURCE,))
        for period in statement_periods:
            if period['starts_on'] < starts_on:
                connection.execute("INSERT OR IGNORE INTO statement_coverage VALUES (?,'personal',?,?)",
                                   (ACCOUNT_SOURCE, period['starts_on'], min(period['ends_on'], (first_day-timedelta(days=1)).isoformat())))
        connection.execute("INSERT OR IGNORE INTO statement_coverage VALUES (?,'personal',?,?)",
                           (ACCOUNT_SOURCE, starts_on, date.today().isoformat()))
        for item in historical_items:
            if item['invoice_month']:
                connection.execute("INSERT OR IGNORE INTO invoice_coverage(source,scope,invoice_month) VALUES (?,?,?)",
                                   (item['source'], item['scope'], item['invoice_month']))
    store.reconcile_internal_transfers()
    return {"restored": len(restored), "removed": len(removed), "backup": saved,
            "starts_on": starts_on, "card_starts_on": card_starts_on}


def apply_inter(store, pending):
    saved = backup(store)
    preserved = 0
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        advance_revision(connection,'Inter',pending['revision'])
        connection.execute("INSERT OR IGNORE INTO pluggy_inter_policy(singleton,starts_on,card_starts_on) VALUES (1,?,?)",
                           (pending["starts_on"], pending['card_starts_on']))
        if pending.get('full_history'):
            connection.execute("""UPDATE pluggy_inter_policy SET account_history_start=CASE
                WHEN account_history_start IS NULL OR account_history_start > ? THEN ? ELSE account_history_start END
                WHERE singleton=1""", (pending['account_history_start'],pending['account_history_start']))
        old = _old_inter(connection, pending["periods"])
        from category_learning import exact_categories, example_key
        memory = exact_categories(connection, [{**item,'id':index} for index,item in enumerate(pending['items'])])
        old_by_source = defaultdict(list)
        for row in old:
            old_by_source[row["source"]].append(row)
        if old:
            connection.executemany("DELETE FROM transactions WHERE id=?", [(r["id"],) for r in old])
        remapped = {}
        for index, original in enumerate(pending["items"]):
            item = dict(original)
            candidates = old_by_source[item['source']]
            if pending.get('full_card_history') and item['invoice_month']:
                candidates = [row for row in candidates if row['invoice_month'] == item['invoice_month'] or row['import_key'] == item['import_key']]
            matches = _matches(item, candidates)
            classified = {(r["category"], r["excluded"]) for r in matches if r["category"] != "Importado"}
            if item["category"] == "Importado" and len(classified) == 1:
                item["category"], item["excluded"] = classified.pop()
                preserved += 1
            elif item["category"] == "Importado":
                remembered = memory.get(index)
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
            # Move reviewed training labels to stable provider keys when a manual/CSV
            # transaction is replaced. Never train on inferred merchant-rule assignments.
            for row in matches:
                remapped[row["id"]] = new_id
                old_key = example_key(row)
                sample = connection.execute('SELECT * FROM category_examples WHERE example_key=?',(old_key,)).fetchone()
                if sample and sample['category'] == item['category']:
                    new_key = example_key({**item,'id':new_id})
                    if new_key != old_key:
                        connection.execute('DELETE FROM category_examples WHERE example_key=?',(old_key,))
                    connection.execute("""INSERT INTO category_examples
                        (example_key,scope,merchant_key,description,amount_cents,payment_type,category)
                        VALUES (?,?,?,?,?,?,?) ON CONFLICT(example_key) DO UPDATE SET
                        description=excluded.description,merchant_key=excluded.merchant_key,amount_cents=excluded.amount_cents,
                        payment_type=excluded.payment_type,updated_at=CURRENT_TIMESTAMP""",
                        (new_key,item['scope'],merchant_key(item['description']) or sample['merchant_key'],item['description'],
                         item['amount_cents'],'credit' if item['invoice_month'] else 'account',item['category']))
        for row in old:
            connection.execute("UPDATE investment_entries SET matched_transaction_id=? WHERE matched_transaction_id=?",
                               (remapped.get(row["id"]), row["id"]))
        for month in pending.get('card_months', []):
            connection.execute("""INSERT INTO invoice_coverage(source,scope,invoice_month,provider)
                VALUES (?,'personal',?,'pluggy') ON CONFLICT(source,scope,invoice_month)
                DO UPDATE SET provider='pluggy'""", ('Inter · cartão principal',month))
        for period in pending["periods"]:
            if period.get('basis') == 'invoice':
                continue
            connection.execute("DELETE FROM authoritative_periods WHERE source=? AND scope=?",
                               (period["source"], period["scope"]))
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
            "categories_preserved": preserved, "balance_updated": bool(pending["balance"]), "backup": saved,
            "card_imported": sum(item['invoice_month'] is not None for item in pending['items']),
            "account_imported": sum(item['invoice_month'] is None for item in pending['items']),
            "card_invoice_months": pending.get('card_months', [])}


def apply_xp(store, pending):
    saved = backup(store)
    entries, snapshot = pending["entries"], pending["snapshot"]
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        advance_revision(connection,'XP',pending['revision'])
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
        from investment_history import capture_observation
        capture_observation(connection, snapshot)
    return {"imported": len(entries), "replaced": len(old), "asset_count": len(snapshot["assets"]),
            "as_of": snapshot["as_of"], "backup": saved, "complete": False, "performance_saved": True}
