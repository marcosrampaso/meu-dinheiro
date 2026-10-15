"""Read-only classification evidence and explicit, atomic category corrections."""

from collections import defaultdict
import hashlib
import json
import re

from finance import PURCHASE_CATEGORIES, ValidationError, merchant_key, validate_month, validate_scope
from plan_insights import _ignored, _income, _plain, NON_SPENDING


def review_key(description):
    clean = re.sub(r"\(?\b(?:parcela\s*)?\d{1,2}\s*(?:/|de)\s*\d{1,2}\b\)?", "", description, flags=re.I)
    return merchant_key(clean) or _plain(clean)


def row_signature(row):
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def editable(row):
    return not _ignored(row) and (row["kind"] == "expense" or _income(row))


def review_overview(store, scope="personal", month=None, category="", search="", kind="expense",
                    payment_type="all", include_excluded=False, merchant="", offset=0):
    scope = validate_scope(scope, allow_all=True)
    if month is not None:
        month = validate_month(month)
    if kind not in ("all", "income", "expense") or payment_type not in ("all", "account", "credit"):
        raise ValidationError("Escolha um tipo de lançamento e uma forma de pagamento válidos.")
    if not isinstance(include_excluded, bool) or isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100000:
        raise ValidationError("Filtro de revisão inválido.")
    if any(not isinstance(value, str) or len(value) > maximum for value, maximum in
           [(category, 60), (search, 120), (merchant, 250)]):
        raise ValidationError("Filtro de texto muito longo ou inválido.")
    with store._connect() as connection:
        records = [dict(row) for row in connection.execute("""SELECT * FROM transactions
            WHERE (?='all' OR scope=?) AND (? IS NULL OR COALESCE(invoice_month,substr(occurred_on,1,7))=?)
            ORDER BY COALESCE(invoice_month,substr(occurred_on,1,7)) DESC,occurred_on DESC,id DESC""",
            (scope, scope, month, month))]
    records = [row for row in records if (kind == "all" or row["kind"] == kind) and
               (payment_type == "all" or (row["invoice_month"] is None) == (payment_type == "account")) and
               (include_excluded or editable(row))]
    choices = sorted(set(PURCHASE_CATEGORIES) | {row["category"] for row in records if editable(row)})
    categories = sorted({row["category"] for row in records})
    needle = _plain(search)
    rows = [row for row in records if (not category or row["category"] == category) and
            (not needle or needle in _plain(row["description"] + " " + row["source"])) and
            (not merchant or review_key(row["description"]) == merchant)]
    monthly = defaultdict(lambda: {"expense_cents": 0, "income_cents": 0, "count": 0, "pending_cents": 0})
    merchants = {}
    for row in rows:
        if not editable(row):
            continue
        current_month = row["invoice_month"] or row["occurred_on"][:7]
        item = monthly[current_month]
        item[row["kind"] + "_cents"] += row["amount_cents"]
        item["count"] += 1
        if row["pending"]:
            item["pending_cents"] += row["amount_cents"]
        key = (row["scope"], review_key(row["description"]))
        group = merchants.setdefault(key, {"scope": row["scope"], "key": key[1], "description": row["description"],
            "expense_cents": 0, "income_cents": 0, "count": 0, "installment_count": 0})
        group[row["kind"] + "_cents"] += row["amount_cents"]
        group["count"] += 1
        group["installment_count"] += bool(re.search(r"parcela|\b\d{1,2}\s*/\s*\d{1,2}\b", row["description"], re.I))
    return {"scope": scope, "month": month, "category": category, "search": search, "kind": kind,
            "payment_type": payment_type, "include_excluded": include_excluded, "merchant": merchant,
            "offset": offset, "limit": 50, "total": len(rows), "categories": categories, "choices": choices,
            "expense_cents": sum(row["expense_cents"] for row in monthly.values()),
            "income_cents": sum(row["income_cents"] for row in monthly.values()),
            "monthly": [{"month": month, **values} for month, values in sorted(monthly.items())],
            "merchants": sorted(merchants.values(), key=lambda row: -(row["expense_cents"] + row["income_cents"]))[:10],
            "items": [{**row, "editable": editable(row), "signature": row_signature(row),
                       "installment_hint": bool(re.search(r"parcela|\b\d{1,2}\s*/\s*\d{1,2}\b", row["description"], re.I))}
                      for row in rows[offset:offset + 50]],
            "note": "Cartão pelo mês da fatura e conta pela data do lançamento. Valores pendentes são provisórios. Meses ausentes não são tratados como zero. Valores parecidos não comprovam parcelas; a indicação vem da descrição. A classificação altera somente a categoria, não o valor, a data ou a natureza financeira."}


def apply_review(store, data):
    scope = validate_scope(data.get("scope", "personal"), allow_all=True)
    items, remember = data.get("items"), data.get("remember", True)
    if not isinstance(remember, bool) or not isinstance(items, list) or not 1 <= len(items) <= 40:
        raise ValidationError("Revise e envie de 1 a 40 alterações por vez.")
    ids = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"id", "category", "signature"}:
            raise ValidationError("Correção de categoria inválida.")
        identifier, category, signature = item["id"], item["category"], item["signature"]
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0 or identifier in ids:
            raise ValidationError("Lançamento inválido ou repetido.")
        if not isinstance(category, str) or not 1 <= len(category.strip()) <= 60 or _plain(category) in NON_SPENDING:
            raise ValidationError("Escolha uma categoria de compra ou receita. Movimentações financeiras são revisadas em Finanças.")
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise ValidationError("Atualize os lançamentos antes de salvar a classificação.")
        ids.add(identifier)
    learned = 0
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        originals = []
        for item in items:
            row = connection.execute("SELECT * FROM transactions WHERE id=? AND (?='all' OR scope=?)",
                                     (item["id"], scope, scope)).fetchone()
            if row is None or row_signature(row) != item["signature"]:
                raise ValidationError("Um lançamento mudou ou foi substituído. Atualize a lista; nenhuma alteração foi salva.")
            row = dict(row)
            if not editable(row):
                raise ValidationError("Movimentações fora dos gastos precisam ser revisadas em Finanças. Nenhuma categoria foi alterada.")
            originals.append(row)
        rules = defaultdict(set)
        for row, item in zip(originals, items):
            category = item["category"].strip()
            connection.execute("UPDATE transactions SET category=? WHERE id=?", (category, row["id"]))
            if row["kind"] == "expense" and merchant_key(row["description"]) and category in PURCHASE_CATEGORIES:
                rules[(row["scope"], row["description"])].add(category)
        # A mixed purchase at one merchant must not become an ambiguous automatic rule.
        by_key = defaultdict(set)
        for (row_scope, description), categories in rules.items():
            by_key[(row_scope, merchant_key(description))].update(categories)
        if remember:
            for (row_scope, description), categories in rules.items():
                if len(by_key[(row_scope, merchant_key(description))]) == 1:
                    store._remember_category(connection, description, row_scope, next(iter(categories)))
            learned = sum(len(categories) == 1 for categories in by_key.values())
    return {"applied": len(items), "learned": learned}
