"""User-confirmed emergency reserve plans and deterministic monthly scenarios."""

import calendar
from datetime import date, timedelta
import re

from finance import ValidationError, parse_amount, validate_scope
from planning import calendar_overview


def _nonnegative(value):
    if isinstance(value, str) and re.fullmatch(r"0+(?:[.,]0{1,2})?", value.strip()):
        return 0
    return parse_amount(value)


def _date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValidationError(f"Informe {label} no formato AAAA-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"Informe {label} válida.") from None


def _shift_month(anchor, offset, day=None):
    absolute = anchor.year * 12 + anchor.month - 1 + offset
    year, month = divmod(absolute, 12)
    return date(year, month + 1, min(day or anchor.day, calendar.monthrange(year, month + 1)[1]))


def _next_due(anchor, today, day=None):
    if anchor >= today:
        return anchor
    offset = (today.year - anchor.year) * 12 + today.month - anchor.month
    candidate = _shift_month(anchor, offset, day)
    return candidate if candidate >= today else _shift_month(anchor, offset + 1, day)


def validate_plan(data, today=None):
    today = today or date.today()
    scope = validate_scope(data.get("scope", "personal"))
    name = data.get("name", "Minha reserva de emergência")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValidationError("Dê um nome ao plano, com até 80 caracteres.")
    months = data.get("target_months")
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 24:
        raise ValidationError("Escolha uma meta de 1 a 24 meses de despesas.")
    balance_on = _date(data.get("balance_as_of"), "a data do saldo")
    if balance_on > today:
        raise ValidationError("O saldo confirmado deve ter uma data de hoje ou anterior.")
    next_on = _date(data.get("next_contribution_on"), "a data do próximo aporte")
    if next_on < today or next_on > today + timedelta(days=366):
        raise ValidationError("Escolha o próximo aporte entre hoje e os próximos 12 meses.")
    day = data.get("contribution_day", next_on.day)
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31 or \
            next_on.day != min(day, calendar.monthrange(next_on.year, next_on.month)[1]):
        raise ValidationError("O próximo aporte deve respeitar o dia mensal escolhido.")
    return {"scope": scope, "name": name.strip(),
            "essential_cents": parse_amount(data.get("essential_expenses")),
            "target_months": months, "monthly_cents": _nonnegative(data.get("monthly_contribution")),
            "reserved_cents": _nonnegative(data.get("reserved_balance")),
            "balance_as_of": balance_on.isoformat(), "next_contribution_on": next_on.isoformat(),
            "contribution_day": day}


def _projection(plan, today):
    essential = plan["essential_cents"]
    target = essential * plan["target_months"]
    reserved, monthly = plan["reserved_cents"], plan["monthly_cents"]
    missing = max(0, target - reserved)
    count = (missing + monthly - 1) // monthly if monthly and missing else 0 if not missing else None
    anchor = _date(plan["next_contribution_on"], "a data do próximo aporte")
    day = plan.get("contribution_day", anchor.day)
    first_due = _next_due(anchor, today, day)
    # Keep the original day when rolling a 31st into a shorter month.
    first_offset = (first_due.year - anchor.year) * 12 + first_due.month - anchor.month
    completion = None
    if count is not None and 0 < count <= 1200 and first_due.year <= 9898:
        completion = _shift_month(anchor, first_offset + count - 1, day).isoformat()
    points = [{"on": today.isoformat(), "balance_cents": reserved, "planned_cents": 0}]
    running = reserved
    for index in range(min(count or 0, 24)):
        amount = min(monthly, max(0, target - running))
        running += amount
        points.append({"on": _shift_month(anchor, first_offset + index, day).isoformat(),
                       "balance_cents": running, "planned_cents": amount})
    return {"target_cents": target, "remaining_cents": missing,
            "progress_percent": round(min(100, reserved / target * 100), 2),
            "months_covered": round(reserved / essential, 2),
            "contributions_needed": count, "completion_on": completion,
            "next_contribution_on": first_due.isoformat(),
            "balance_stale": (today - date.fromisoformat(plan["balance_as_of"])).days > 30,
            "status": "complete" if not missing else "paused" if not monthly else "building",
            "milestones": [{"months": months, "target_cents": essential * months,
                             "achieved": reserved >= essential * months}
                            for months in sorted({1, min(3, plan["target_months"]), plan["target_months"]})],
            "points": points, "projection_truncated": bool(count and count > 24),
            "note": "Cenário com aportes mensais constantes. Sem rendimentos, inflação, impostos ou resgates futuros. Não executa transferências."}


def _cash_check(store, plan, projection, today):
    cash = calendar_overview(store, plan["scope"], today)
    events = [{"on": row["due_on"], "kind": row["kind"], "amount_cents": row["amount_cents"],
               "source": "calendar"} for row in cash["events"]]
    contributions = [point for point in projection["points"][1:] if point["on"] <= cash["through"]]
    events.extend({"on": point["on"], "kind": "expense", "amount_cents": point["planned_cents"],
                   "source": "reserve"} for point in contributions)
    # Expenses first on the same date: do not presume an incoming payment has cleared.
    events.sort(key=lambda row: (row["on"], row["kind"] == "income"))
    running = cash["opening_balance_cents"]
    minimum = running
    for event in events:
        if running is not None:
            running += event["amount_cents"] * (1 if event["kind"] == "income" else -1)
            minimum = min(minimum, running)
    status = "missing_balance" if running is None else "negative_projection" if minimum < 0 else "review_projection"
    return {"status": status, "through": cash["through"],
            "opening_balance_as_of": cash["opening_balance_as_of"], "balance_stale": cash["balance_stale"],
            "minimum_balance_cents": minimum, "ending_balance_cents": running,
            "simulated_contributions": len(contributions),
            "partial_invoices": sum(bool(row.get("partial")) for row in cash["events"]),
            "note": "Simulação parcial: saldo das contas e vencimentos cadastrados, com os novos aportes deste plano. Não inclui despesas não cadastradas, não confirma receitas futuras nem identifica dinheiro já reservado dentro dessas contas. Confira antes de aportar."}


def _overview(store, plan, today):
    projection = _projection(plan, today)
    return {"configured": True, "scope": plan["scope"], "plan": plan,
            "projection": projection, "cash_check": _cash_check(store, plan, projection, today)}


def reserve_overview(store, scope="personal", today=None):
    today = today or date.today()
    scope = validate_scope(scope)
    with store._connect() as connection:
        row = connection.execute("SELECT * FROM reserve_plans WHERE scope=?", (scope,)).fetchone()
    return _overview(store, dict(row), today) if row else {"configured": False, "scope": scope, "plan": None}


def preview_reserve(store, data, today=None):
    today = today or date.today()
    return _overview(store, validate_plan(data, today), today)


def save_reserve(store, data, today=None):
    today = today or date.today()
    plan = validate_plan(data, today)
    with store._connect() as connection:
        connection.execute("""INSERT INTO reserve_plans
            (scope,name,essential_cents,target_months,monthly_cents,reserved_cents,balance_as_of,next_contribution_on,contribution_day)
            VALUES (:scope,:name,:essential_cents,:target_months,:monthly_cents,:reserved_cents,:balance_as_of,:next_contribution_on,:contribution_day)
            ON CONFLICT(scope) DO UPDATE SET name=excluded.name,essential_cents=excluded.essential_cents,
            target_months=excluded.target_months,monthly_cents=excluded.monthly_cents,reserved_cents=excluded.reserved_cents,
            balance_as_of=excluded.balance_as_of,next_contribution_on=excluded.next_contribution_on,contribution_day=excluded.contribution_day,
            updated_at=CURRENT_TIMESTAMP""", plan)
    return reserve_overview(store, plan["scope"], today)


def delete_reserve(store, scope):
    scope = validate_scope(scope)
    with store._connect() as connection:
        connection.execute("DELETE FROM reserve_plans WHERE scope=?", (scope,))
