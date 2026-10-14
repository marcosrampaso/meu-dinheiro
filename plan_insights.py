"""Local spending evidence and user-reviewed assumptions for reserve plans."""

import calendar
from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import json
import re
from statistics import median
import unicodedata

from finance import ValidationError, merchant_key, validate_scope
from planning import _days_covered
from reserve_plan import _nonnegative


NON_SPENDING = {"pagamento de fatura", "antecipacao da fatura", "transferencia para investimento",
                "movimento de investimento", "transferencia entre contas", "entrada desconsiderada"}
NON_INCOME = {"cashback", "estorno", "estorno de pix", "reembolso"}
ESSENTIAL = {"mercado", "moradia", "saude", "transporte"}
FLEXIBLE = {"lazer", "compras", "viagens", "tabaco", "cuidados pessoais"}
ROLES = {"essential", "flexible", "review"}


def _plain(value):
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold().strip()


def _months(today, count):
    absolute = today.year * 12 + today.month - 1
    return [f"{(absolute - offset) // 12:04d}-{(absolute - offset) % 12 + 1:02d}"
            for offset in range(count, 0, -1)]


def _window(value):
    if isinstance(value, bool) or not isinstance(value, int) or value not in (3, 6):
        raise ValidationError("Escolha os últimos 3 ou 6 meses encerrados.")
    return value


def _basis_month(row):
    # Each installment belongs to its invoice, even if its original purchase is older.
    return row["invoice_month"] or row["occurred_on"][:7]


def _ignored(row):
    category, description = _plain(row["category"]), _plain(row["description"])
    return bool(row["excluded"]) or category in NON_SPENDING or (
        row["invoice_month"] is None and "fatura" in description and
        ("pagamento" in description or "antecipacao" in description))


def _income(row):
    category, description = _plain(row["category"]), _plain(row["description"])
    return (row["kind"] == "income" and row["invoice_month"] is None and not _ignored(row)
            and category not in NON_INCOME and
            not re.match(r"^(cashback|estorno|reembolso|resgate)\b", description))


def history_insights(store, scope="personal", months=6, today=None):
    today = today or date.today()
    scope, months = validate_scope(scope), _window(months)
    window = _months(today, months)
    with store._connect() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM transactions WHERE scope=?", (scope,))]
        periods = [dict(row) for row in connection.execute("SELECT * FROM statement_coverage WHERE scope=?", (scope,))]
        invoice_coverage = [dict(row) for row in connection.execute("SELECT * FROM invoice_coverage WHERE scope=?", (scope,))]
        saved = connection.execute("SELECT * FROM plan_history_reviews WHERE scope=?", (scope,)).fetchone()
    relevant = [row for row in rows if _basis_month(row) in window]
    spending = [row for row in relevant if row["kind"] == "expense" and not row["pending"] and not _ignored(row)]
    income = [row for row in relevant if not row["pending"] and _income(row)]
    account_sources = {row["source"] for row in periods}
    # Cash transactions without a documented statement also mean incomplete coverage.
    undocumented_cash = {row["source"] for row in relevant if row["invoice_month"] is None}
    account_sources |= undocumented_cash
    card_sources = {row["source"] for row in rows if row["invoice_month"] is not None}
    card_sources |= {row["source"] for row in invoice_coverage}
    coverage_keys = {(row["source"], row["invoice_month"]) for row in invoice_coverage}
    monthly = []
    for month in window:
        start = date.fromisoformat(month + "-01")
        end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        accounts = [{"source": source, "complete": _days_covered(
            [row for row in periods if row["source"] == source], start, end) == end.day}
                    for source in sorted(account_sources)]
        cards = [{"source": source, "registered": (source, month) in coverage_keys}
                 for source in sorted(card_sources)]
        observed = [row for row in relevant if _basis_month(row) == month]
        full = bool(accounts) and all(row["complete"] for row in accounts) and all(row["registered"] for row in cards)
        has_pending = any(row["pending"] for row in observed)
        monthly.append({"month": month, "accounts": accounts, "cards": cards,
                        "coverage": "documented" if full and not has_pending else "partial" if observed or any(
                            row["complete"] for row in accounts) or any(row["registered"] for row in cards) else "missing",
                        "pending_count": sum(bool(row["pending"]) for row in observed),
                        "spending_cents": sum(row["amount_cents"] for row in spending if _basis_month(row) == month),
                        "income_cents": sum(row["amount_cents"] for row in income if _basis_month(row) == month)})
    documented = [row["month"] for row in monthly if row["coverage"] == "documented"]
    # Prefer at least three documented months; otherwise show provisional observations.
    base = documented if len(documented) >= 3 else [row["month"] for row in monthly if
        row["coverage"] == "documented" or any(_basis_month(item) == row["month"] for item in spending)]
    base_spending = [row for row in spending if _basis_month(row) in base]
    preferences = json.loads(saved["roles_json"]) if saved else {}
    categories = []
    for category in sorted({row["category"] for row in base_spending}):
        grouped = [row for row in base_spending if row["category"] == category]
        amounts = [sum(row["amount_cents"] for row in grouped if _basis_month(row) == month) for month in base]
        suggestion = "essential" if _plain(category) in ESSENTIAL else "flexible" if _plain(category) in FLEXIBLE else "review"
        categories.append({"category": category, "average_cents": round(sum(amounts) / len(base)),
                           "minimum_cents": min(amounts), "maximum_cents": max(amounts),
                           "months_present": sum(value > 0 for value in amounts), "count": len(grouped),
                           "role": preferences.get(category, suggestion), "reviewed": category in preferences})
    categories.sort(key=lambda item: (-item["average_cents"], item["category"]))
    recurring = []
    merchants = defaultdict(list)
    for row in spending:
        description = re.sub(r"\(?\b(?:parcela\s*)?\d{1,2}\s*(?:/|de)\s*\d{1,2}\b\)?", "", row["description"], flags=re.I)
        key = merchant_key(description)
        if key:
            merchants[key].append(row)
    for items in merchants.values():
        counts = Counter(_basis_month(row) for row in items)
        # Several grocery/restaurant purchases per month do not establish a fixed bill.
        if len(counts) < 3 or max(counts.values()) != 1 or window[-1] not in counts:
            continue
        amounts = [row["amount_cents"] for row in items]
        typical = round(median(amounts))
        if max(amounts) - min(amounts) > typical * .2:
            continue
        latest = max(items, key=lambda row: (_basis_month(row), row["occurred_on"], row["id"]))
        installment = any(re.search(r"\b\d{1,2}\s*/\s*\d{1,2}\b|parcela", row["description"], re.I) for row in items)
        recurring.append({"description": latest["description"], "category": latest["category"],
                          "typical_cents": typical, "months_present": len(counts),
                          "type": "installment" if installment else "recurring",
                          "note": "Possível parcelamento; confirme o prazo restante." if installment else
                          "Uma cobrança por mês, com valores próximos. Confirme se continuará."})
    future = [row for row in rows if row["kind"] == "expense" and not _ignored(row) and
              row["invoice_month"] is not None and row["invoice_month"] >= today.strftime("%Y-%m")]
    future_by_month = [{"month": month, "amount_cents": sum(row["amount_cents"] for row in future if row["invoice_month"] == month),
                        "pending_cents": sum(row["amount_cents"] for row in future if row["invoice_month"] == month and row["pending"])}
                       for month in sorted({_basis_month(row) for row in future})]
    base_months = [row for row in monthly if row["month"] in base]
    base_income = [row for row in income if _basis_month(row) in base]
    total = sum(row["spending_cents"] for row in base_months)
    unclassified = sum(row["amount_cents"] for row in base_spending if _plain(row["category"]) in
                       {"importado", "sem categoria", "outros"})
    warnings = []
    if len(base) < 3:
        warnings.append("Menos de três meses com gastos observados. A média ainda pode variar bastante.")
    if len(documented) < 3:
        warnings.append("A base usa meses com cobertura parcial. Despesas ausentes podem aumentar o custo real.")
    elif len(base) != len(window):
        warnings.append("Meses sem cobertura documentada foram deixados fora das médias.")
    if card_sources:
        warnings.append("Faturas registradas não comprovam fechamento. Compras usam o mês da fatura; pagamentos não são somados novamente.")
    if unclassified:
        warnings.append("Há gastos em Importado, Outros ou sem categoria. Revise antes de decidir o que é essencial.")
    if any(row["pending_count"] for row in monthly):
        warnings.append("Lançamentos pendentes ficam fora das médias históricas.")
    refunds = sum(row["amount_cents"] for row in relevant if row["kind"] == "income" and
                  not row["pending"] and (_plain(row["category"]) in NON_INCOME or row["invoice_month"] is not None))
    if refunds:
        warnings.append("Estornos e cashback não viram renda mensal. As médias de despesas são brutas, sem abatimentos sem vínculo com a compra.")
    if scope == "personal":
        warnings.append("Transferências PJ → PF não são renda nova. Confirme a renda líquida disponível para este plano.")
    if not base_income:
        warnings.append("Não há entradas válidas observadas nesta base para sugerir uma renda. Informe a renda disponível manualmente.")
    fingerprint = hashlib.sha256(json.dumps([relevant, periods, invoice_coverage, dict(saved) if saved else None, window],
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"scope": scope, "window_months": months, "window": window, "base_months": base,
            "signature": fingerprint, "monthly": monthly, "categories": categories,
            "recurring": sorted(recurring, key=lambda row: -row["typical_cents"]),
            "future_invoices": future_by_month, "warnings": warnings,
            "quality": "documented" if len(documented) >= 3 else "provisional" if base else "empty",
            "average_spending_cents": round(total / len(base)) if base else None,
            "observed_income_cents": round(sum(row["amount_cents"] for row in base_income) / len(base)) if base_income else None,
            "unclassified_cents": unclassified,
            "preferences": {"income_cents": saved["income_cents"] if saved else None,
                            "buffer_percent": saved["buffer_percent"] if saved else 10},
            "note": "Análise local, sem envio à IA. Recorrências são indícios, não contas fixas confirmadas. Médias descrevem o histórico; não garantem renda ou sobra futura."}


def preview_history(store, data, today=None):
    if not isinstance(data, dict):
        raise ValidationError("Informe os dados da revisão.")
    analysis = history_insights(store, data.get("scope", "personal"), _window(data.get("months", 6)), today)
    if data.get("signature") != analysis["signature"]:
        raise ValidationError("O histórico mudou. Atualize a análise e revise novamente.")
    roles = data.get("roles", {})
    categories = {row["category"] for row in analysis["categories"]}
    if not isinstance(roles, dict) or set(roles) != categories or any(
            not isinstance(value, str) or value not in ROLES for value in roles.values()):
        raise ValidationError("Revise o papel de cada categoria do histórico.")
    buffer = data.get("buffer_percent", 10)
    if isinstance(buffer, bool) or not isinstance(buffer, int) or not 0 <= buffer <= 50:
        raise ValidationError("Escolha uma margem de segurança entre 0% e 50% da renda.")
    raw_income = data.get("monthly_income")
    income = None if raw_income in (None, "") else _nonnegative(raw_income)
    target_months = data.get("target_months", 6)
    if isinstance(target_months, bool) or not isinstance(target_months, int) or not 1 <= target_months <= 24:
        raise ValidationError("Escolha de 1 a 24 meses de proteção.")
    reserved = _nonnegative(data.get("reserved_balance", "0"))
    essential = sum(row["average_cents"] for row in analysis["categories"] if roles[row["category"]] == "essential")
    flexible = sum(row["average_cents"] for row in analysis["categories"] if roles[row["category"]] == "flexible")
    unresolved = sum(row["average_cents"] for row in analysis["categories"] if roles[row["category"]] == "review")
    expenses = essential + flexible + unresolved
    margin = round(income * buffer / 100) if income is not None else None
    scenarios = []
    for key, title, reduction in [("maintain", "Manter seus hábitos", 0), ("adjust", "Pequenos ajustes", 10), ("accelerate", "Acelerar a meta", 20)]:
        saving = round(flexible * reduction / 100)
        surplus = income - expenses + saving - margin if income is not None else None
        contribution = max(0, surplus) if surplus is not None else None
        missing = max(0, essential * target_months - reserved)
        payments = (missing + contribution - 1) // contribution if contribution and essential else 0 if essential and not missing else None
        scenarios.append({"id": key, "title": title, "reduction_percent": reduction,
                          "saving_cents": saving, "spending_cents": expenses - saving,
                          "surplus_cents": surplus, "contribution_cents": contribution,
                          "contributions_needed": payments, "margin_cents": margin})
    return {"analysis": analysis, "roles": roles, "income_cents": income, "buffer_percent": buffer,
            "essential_cents": essential, "flexible_cents": flexible, "unresolved_cents": unresolved,
            "target_cents": essential * target_months, "scenarios": scenarios,
            "note": "Cenários dependem da renda confirmada e de reduzir somente os gastos marcados como flexíveis. Não incluem novos compromissos ou despesas ausentes do histórico. Confira também a projeção de caixa."}


def save_history_review(store, data, today=None):
    result = preview_history(store, data, today)
    if data.get("acknowledged") is not True or not result["analysis"]["base_months"]:
        raise ValidationError("Confira o histórico e confirme a revisão antes de aplicar a base.")
    if not result["essential_cents"]:
        raise ValidationError("Selecione ao menos uma categoria essencial com gastos para montar a reserva.")
    with store._connect() as connection:
        connection.execute("""INSERT INTO plan_history_reviews (scope,roles_json,income_cents,buffer_percent)
            VALUES (?,?,?,?) ON CONFLICT(scope) DO UPDATE SET roles_json=excluded.roles_json,
            income_cents=excluded.income_cents,buffer_percent=excluded.buffer_percent,updated_at=CURRENT_TIMESTAMP""",
            (result["analysis"]["scope"], json.dumps(result["roles"], ensure_ascii=False), result["income_cents"], result["buffer_percent"]))
    # Return the new fingerprint so subsequent simulations remain usable after saving preferences.
    result["analysis"] = history_insights(store, result["analysis"]["scope"], result["analysis"]["window_months"], today)
    return result
