"""Local budgets, import coverage, and recurring cash commitments."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from finance import FinanceStore, PURCHASE_CATEGORIES, ValidationError, parse_amount, validate_month, validate_scope


NON_SPENDING = ("Pagamento de fatura", "Transferência para investimento")


def _month_after(month: str) -> str:
    year, number = map(int, month.split("-"))
    return f"{year + (number == 12):04d}-{number % 12 + 1:02d}"


def budget_overview(store: FinanceStore, month: str, scope: str) -> dict:
    month, scope = validate_month(month), validate_scope(scope, allow_all=True)
    with store._connect() as connection:
        budgets = [dict(row) for row in connection.execute("""
            SELECT scope, category, limit_cents FROM category_budgets
            WHERE (? = 'all' OR scope = ?) ORDER BY scope, category
        """, (scope, scope))]
        spent = {(row["scope"], row["category"]): row["spent_cents"] for row in connection.execute("""
            SELECT scope, category, SUM(amount_cents) AS spent_cents FROM transactions
            WHERE kind = 'expense' AND excluded = 0 AND category NOT IN (?, ?)
              AND occurred_on >= ? AND occurred_on < ?
              AND (? = 'all' OR scope = ?)
            GROUP BY scope, category
        """, (*NON_SPENDING, month + "-01", _month_after(month) + "-01", scope, scope))}
        custom = [row[0] for row in connection.execute("""
            SELECT DISTINCT category FROM transactions WHERE kind = 'expense' AND excluded = 0
              AND category NOT IN (?, ?) AND (? = 'all' OR scope = ?)
            ORDER BY category
        """, (*NON_SPENDING, scope, scope))]
    return {"month": month, "scope": scope,
            "categories": sorted(set(PURCHASE_CATEGORIES) | set(custom)),
            "budgets": [{**row, "spent_cents": spent.get((row["scope"], row["category"]), 0)}
                        for row in budgets]}


def save_budget(store: FinanceStore, data: dict) -> dict:
    scope = validate_scope(data.get("scope"))
    category = data.get("category")
    if not isinstance(category, str) or not 1 <= len(category.strip()) <= 60 or category.strip() in NON_SPENDING:
        raise ValidationError("Escolha uma categoria válida para o orçamento.")
    category = category.strip()
    limit_cents = parse_amount(data.get("limit"))
    with store._connect() as connection:
        connection.execute("""
            INSERT INTO category_budgets (scope, category, limit_cents) VALUES (?, ?, ?)
            ON CONFLICT(scope, category) DO UPDATE SET limit_cents = excluded.limit_cents
        """, (scope, category, limit_cents))
    return {"scope": scope, "category": category, "limit_cents": limit_cents}


def delete_budget(store: FinanceStore, scope: str, category: str) -> None:
    scope = validate_scope(scope)
    with store._connect() as connection:
        cursor = connection.execute("DELETE FROM category_budgets WHERE scope = ? AND category = ?",
                                    (scope, category))
    if not cursor.rowcount:
        raise ValidationError("Orçamento não encontrado.")


def _days_covered(periods: list[dict], start: date, end: date) -> int:
    intervals = sorted((max(start, date.fromisoformat(row["starts_on"])),
                        min(end, date.fromisoformat(row["ends_on"]))) for row in periods)
    total = 0
    current_start = current_end = None
    for left, right in intervals:
        if left > right:
            continue
        if current_end is None or left > current_end + timedelta(days=1):
            if current_end is not None:
                total += (current_end - current_start).days + 1
            current_start, current_end = left, right
        else:
            current_end = max(current_end, right)
    if current_end is not None:
        total += (current_end - current_start).days + 1
    return total


def data_health(store: FinanceStore, month: str, scope: str, today: date | None = None) -> dict:
    month, scope = validate_month(month), validate_scope(scope, allow_all=True)
    today = today or date.today()
    start = date.fromisoformat(month + "-01")
    month_end = date.fromisoformat(_month_after(month) + "-01") - timedelta(days=1)
    end = min(month_end, today)
    expected = max(0, (end - start).days + 1)
    with store._connect() as connection:
        periods = [dict(row) for row in connection.execute("""
            SELECT source, scope, starts_on, ends_on FROM statement_coverage
            WHERE (? = 'all' OR scope = ?)
        """, (scope, scope))]
        cards = [dict(row) for row in connection.execute("""
            SELECT source, scope, invoice_month,
                   SUM(CASE WHEN import_key IS NOT NULL THEN 1 ELSE 0 END) AS imported,
                   SUM(CASE WHEN import_key IS NULL THEN 1 ELSE 0 END) AS manual
            FROM transactions WHERE invoice_month IS NOT NULL AND (? = 'all' OR scope = ?)
            GROUP BY source, scope, invoice_month
        """, (scope, scope))]
    accounts = []
    for account_scope, source in sorted({(row["scope"], row["source"]) for row in periods}):
        account_periods = [row for row in periods if row["scope"] == account_scope and row["source"] == source]
        covered = _days_covered(account_periods, start, end) if expected else 0
        accounts.append({"source": source, "scope": account_scope, "covered_days": covered,
                         "expected_days": expected, "status": "full" if expected and covered == expected else "partial" if covered else "missing"})
    invoice_sources = sorted({(row["scope"], row["source"]) for row in cards})
    invoices = []
    for card_scope, source in invoice_sources:
        row = next((item for item in cards if item["scope"] == card_scope and
                    item["source"] == source and item["invoice_month"] == month), None)
        imported, manual = (row["imported"], row["manual"]) if row else (0, 0)
        invoices.append({"source": source, "scope": card_scope,
                         "imported_count": imported, "manual_count": manual,
                         "status": "registered" if imported else "manual" if manual else "missing"})
    return {"month": month, "scope": scope, "through": end.isoformat() if expected else None,
            "accounts": accounts, "invoices": invoices,
            "has_gaps": not accounts or any(row["status"] != "full" for row in accounts) or
                        any(row["status"] in ("missing", "manual") for row in invoices),
            "note": "Cobertura de contas vem dos períodos importados; faturas registradas não comprovam fechamento."}


def save_commitment(store: FinanceStore, data: dict, today: date | None = None) -> dict:
    scope = validate_scope(data.get("scope"))
    kind = data.get("kind")
    description = data.get("description")
    if kind not in ("income", "expense"):
        raise ValidationError("Escolha entrada ou saída planejada.")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 100:
        raise ValidationError("Informe a descrição do compromisso.")
    due_day = data.get("due_day")
    if isinstance(due_day, bool) or not isinstance(due_day, int) or not 1 <= due_day <= 31:
        raise ValidationError("Escolha um dia de vencimento de 1 a 31.")
    starts_month = validate_month(data.get("starts_month", (today or date.today()).strftime("%Y-%m")))
    amount_cents = parse_amount(data.get("amount"))
    with store._connect() as connection:
        cursor = connection.execute("""
            INSERT INTO planned_cashflows (scope, kind, description, amount_cents, due_day, starts_month)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (scope, kind, description.strip(), amount_cents, due_day, starts_month))
    return {"id": cursor.lastrowid, "scope": scope, "kind": kind, "description": description.strip(),
            "amount_cents": amount_cents, "due_day": due_day, "starts_month": starts_month}


def delete_commitment(store: FinanceStore, commitment_id: int) -> None:
    if isinstance(commitment_id, bool) or not isinstance(commitment_id, int) or commitment_id <= 0:
        raise ValidationError("Compromisso inválido.")
    with store._connect() as connection:
        cursor = connection.execute("DELETE FROM planned_cashflows WHERE id = ?", (commitment_id,))
    if not cursor.rowcount:
        raise ValidationError("Compromisso não encontrado.")


def save_card_due_rule(store: FinanceStore, data: dict) -> dict:
    scope = validate_scope(data.get("scope"))
    source, due_day, offset = data.get("source"), data.get("due_day"), data.get("month_offset")
    if not isinstance(source, str) or not 1 <= len(source) <= 80:
        raise ValidationError("Escolha um cartão importado.")
    if isinstance(due_day, bool) or not isinstance(due_day, int) or not 1 <= due_day <= 31:
        raise ValidationError("Escolha um dia de vencimento de 1 a 31.")
    if isinstance(offset, bool) or offset not in (0, 1):
        raise ValidationError("Escolha o mês do pagamento da fatura.")
    with store._connect() as connection:
        exists = connection.execute("""SELECT 1 FROM transactions
            WHERE invoice_month IS NOT NULL AND source = ? AND scope = ? LIMIT 1""",
                                    (source, scope)).fetchone()
        if not exists:
            raise ValidationError("Cartão não encontrado no histórico.")
        connection.execute("""
            INSERT INTO card_due_rules (source, scope, due_day, month_offset)
            VALUES (?, ?, ?, ?) ON CONFLICT(source, scope) DO UPDATE SET
                due_day = excluded.due_day, month_offset = excluded.month_offset
        """, (source, scope, due_day, offset))
        row = connection.execute("SELECT * FROM card_due_rules WHERE source = ? AND scope = ?",
                                 (source, scope)).fetchone()
    return dict(row)


def delete_card_due_rule(store: FinanceStore, rule_id: int) -> None:
    if isinstance(rule_id, bool) or not isinstance(rule_id, int) or rule_id <= 0:
        raise ValidationError("Vencimento de cartão inválido.")
    with store._connect() as connection:
        cursor = connection.execute("DELETE FROM card_due_rules WHERE id = ?", (rule_id,))
    if not cursor.rowcount:
        raise ValidationError("Vencimento de cartão não encontrado.")


def _due_on(year: int, month: int, due_day: int) -> date:
    return date(year, month, min(due_day, calendar.monthrange(year, month)[1]))


def calendar_overview(store: FinanceStore, scope: str, today: date | None = None, days: int = 42) -> dict:
    scope = validate_scope(scope, allow_all=True)
    today = today or date.today()
    horizon = today + timedelta(days=days)
    with store._connect() as connection:
        commitments = [dict(row) for row in connection.execute("""
            SELECT * FROM planned_cashflows WHERE (? = 'all' OR scope = ?) ORDER BY scope, due_day, id
        """, (scope, scope))]
        card_rules = [dict(row) for row in connection.execute("""
            SELECT * FROM card_due_rules WHERE (? = 'all' OR scope = ?) ORDER BY scope, source
        """, (scope, scope))]
        invoices = [dict(row) for row in connection.execute("""
            SELECT source, scope, invoice_month,
              SUM(CASE WHEN kind = 'expense' THEN amount_cents ELSE -amount_cents END) AS total_cents,
              SUM(CASE WHEN import_key IS NOT NULL THEN 1 ELSE 0 END) AS imported_count,
              SUM(CASE WHEN import_key IS NULL THEN 1 ELSE 0 END) AS manual_count,
              SUM(pending) AS pending_count
            FROM transactions WHERE invoice_month IS NOT NULL AND (? = 'all' OR scope = ?)
            GROUP BY source, scope, invoice_month
        """, (scope, scope))]
        balances = [dict(row) for row in connection.execute("""
            SELECT source, scope, as_of, amount_cents FROM account_balances AS balances
            WHERE as_of <= ? AND (? = 'all' OR scope = ?)
              AND as_of = (SELECT MAX(as_of) FROM account_balances AS candidate
                           WHERE candidate.source = balances.source AND candidate.scope = balances.scope
                             AND candidate.as_of <= ?)
        """, (today.isoformat(), scope, scope, today.isoformat()))]
    events = []
    month_start = date(today.year, today.month, 1)
    month = month_start
    while month <= horizon:
        for item in commitments:
            due = _due_on(month.year, month.month, item["due_day"])
            if today < due <= horizon and due.strftime("%Y-%m") >= item["starts_month"]:
                events.append({"id": item["id"], "source_type": "recurring",
                               "due_on": due.isoformat(), "scope": item["scope"],
                               "kind": item["kind"], "description": item["description"],
                               "amount_cents": item["amount_cents"]})
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    for invoice in invoices:
        if invoice["total_cents"] <= 0:
            continue
        rule = next((row for row in card_rules if row["source"] == invoice["source"] and
                     row["scope"] == invoice["scope"]), None)
        if not rule:
            continue
        payment_month = _month_after(invoice["invoice_month"]) if rule["month_offset"] else invoice["invoice_month"]
        year, number = map(int, payment_month.split("-"))
        due = _due_on(year, number, rule["due_day"])
        if today < due <= horizon:
            events.append({"id": rule["id"], "source_type": "card",
                           "due_on": due.isoformat(), "scope": invoice["scope"], "kind": "expense",
                           "description": "Fatura " + invoice["source"],
                           "amount_cents": invoice["total_cents"],
                           "partial": invoice["imported_count"] == 0 or invoice["manual_count"] > 0 or invoice["pending_count"] > 0})
    events.sort(key=lambda item: (item["due_on"], item["source_type"], item["id"]))
    opening = sum(row["amount_cents"] for row in balances) if balances else None
    running = opening
    for event in events:
        if running is not None:
            running += event["amount_cents"] * (1 if event["kind"] == "income" else -1)
        event["projected_balance_cents"] = running
    oldest = min((row["as_of"] for row in balances), default=None)
    known_cards = sorted({(row["scope"], row["source"]) for row in invoices})
    return {"scope": scope, "through": horizon.isoformat(), "commitments": commitments,
            "card_rules": card_rules,
            "known_cards": [{"scope": card_scope, "source": source} for card_scope, source in known_cards],
            "events": events, "opening_balance_cents": opening,
            "opening_balance_as_of": oldest,
            "balance_stale": bool(oldest and (today - date.fromisoformat(oldest)).days > 7),
            "projected_balance_cents": running,
            "note": "Projeção simples com vencimentos cadastrados. Não inclui gastos futuros não planejados nem confirma pagamentos."}
