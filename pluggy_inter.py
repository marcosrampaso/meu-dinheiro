"""Reviewable import of a personal Inter account and card via Meu Pluggy."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qs, urlencode

from finance import FinanceStore, merchant_key
from pluggy_xp import PluggyError, PluggyXP, UUID, _http_json


ACCOUNT_SOURCE = "Inter · conta corrente"
CARD_SOURCE = "Inter · cartão principal"
MAX_TRANSACTIONS = 5000


def live_start(store: FinanceStore) -> str:
    """Keep the initial live month fixed across later synchronizations."""
    with store._connect() as connection:
        policy = connection.execute("SELECT starts_on FROM pluggy_inter_policy WHERE singleton=1").fetchone()
    return policy[0] if policy else date.today().replace(day=1).isoformat()


def account_live_start(store: FinanceStore, starts_on: str) -> str:
    with store._connect() as connection:
        row = connection.execute('SELECT account_history_start FROM pluggy_inter_policy WHERE singleton=1').fetchone()
    return min(starts_on, row[0]) if row and row[0] else starts_on


def card_live_start(store: FinanceStore, starts_on: str) -> str:
    with store._connect() as connection:
        policy = connection.execute("SELECT card_starts_on FROM pluggy_inter_policy WHERE singleton=1").fetchone()
    return policy[0] if policy else starts_on


def card_payment(row: dict, description: str) -> bool:
    """Bank settlements are not merchant refunds or additional purchases."""
    key = merchant_key(description)
    return row.get("operationType") == "PAGAMENTO_FATURA" or key in {
        "pagamento on line", "pagamento online", "pagamento recebido",
        "pagamento de fatura", "pagamento da fatura", "pagamento fatura",
    }


def _cents(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise PluggyError("A Pluggy devolveu um valor de transação inválido.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PluggyError("A Pluggy devolveu um valor de transação inválido.") from exc
    if not amount.is_finite() or abs(amount) > Decimal("999999999.99"):
        raise PluggyError("A Pluggy devolveu um valor de transação inválido.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _iso_date(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PluggyError(f"A Pluggy devolveu {label} sem data válida.")
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError as exc:
        raise PluggyError(f"A Pluggy devolveu {label} sem data válida.") from exc
    return parsed.isoformat()


def _pages(path: str, api_key: str, http_json, max_pages: int = 20) -> list[dict]:
    rows = []
    for page in range(1, max_pages + 1):
        join = "&" if "?" in path else "?"
        result = http_json("GET", f"{path}{join}page={page}&pageSize=100", api_key=api_key)
        batch, total_pages = result.get("results"), result.get("totalPages")
        if not isinstance(batch, list) or not isinstance(total_pages, int) or not 0 <= total_pages <= max_pages:
            raise PluggyError("A Pluggy devolveu uma lista incompleta de contas ou faturas.")
        if any(not isinstance(row, dict) for row in batch):
            raise PluggyError("A Pluggy devolveu uma lista de contas ou faturas inválida.")
        if total_pages == 0:
            if batch:
                raise PluggyError("A Pluggy devolveu uma lista de contas ou faturas inválida.")
            return []
        rows.extend(batch)
        if page >= total_pages:
            return rows
    raise PluggyError("A Pluggy devolveu mais páginas que o limite de segurança.")


def _transactions(account_id: str, api_key: str, http_json) -> list[dict]:
    path = "/v2/transactions?" + urlencode({"accountId": account_id})
    seen_paths = set()
    rows = []
    for _ in range(40):
        if path in seen_paths:
            raise PluggyError("A paginação das transações da Pluggy se repetiu.")
        seen_paths.add(path)
        result = http_json("GET", path, api_key=api_key)
        batch, next_query = result.get("results"), result.get("next")
        if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
            raise PluggyError("A Pluggy devolveu transações incompletas.")
        rows.extend(batch)
        if len(rows) > MAX_TRANSACTIONS:
            raise PluggyError("Há mais de 5.000 transações. Importe um período menor antes de sincronizar.")
        if next_query is None:
            return rows
        if not isinstance(next_query, str) or not next_query.startswith("?") or \
                parse_qs(next_query[1:]).get("accountId") != [account_id]:
            raise PluggyError("A Pluggy devolveu uma página seguinte inválida.")
        path = "/v2/transactions" + next_query
    raise PluggyError("A Pluggy devolveu mais páginas que o limite de segurança.")


def _bank_classification(row: dict, description: str, kind: str) -> tuple[str, bool]:
    plain = description.casefold()
    operation = row.get("operationType")
    if kind == "expense" and ("pagamento" in plain and
                              ("fatura" in plain or "cartao" in (merchant_key(description) or ""))):
        return "Pagamento de fatura", True
    if kind == "income" and plain.startswith("cashback"):
        return "Cashback", True
    if re.match(r"^(aplica[cç][aã]o|resgate)\s*[-:]?\s*(cdb|lci|lca|rdb|fundo|invest)", plain):
        return "Movimento de investimento", True
    receiver = row.get("paymentData") or {}
    if isinstance(receiver, dict):
        receiver = receiver.get("receiver") or {}
    name = receiver.get("name", "") if isinstance(receiver, dict) else ""
    if kind == "expense" and ("xp investimentos" in plain or
                              (isinstance(name, str) and "xp investimentos" in name.casefold())):
        return "Transferência para investimento", True
    if operation in ("RENDIMENTO_APLIC_FINANCEIRA", "RESGATE_APLIC_FINANCEIRA"):
        return "Movimento de investimento", True
    return "Importado", False


class PluggyInter:
    def __init__(self, store: FinanceStore, pluggy: PluggyXP):
        self.store = store
        self.pluggy = pluggy
        self._lock = threading.Lock()
        self._pending: dict | None = None

    def clear_preview(self) -> None:
        with self._lock:
            self._pending = None

    def preview(self, http_json=_http_json, *, full_history=False) -> dict:
        if not isinstance(full_history, bool):
            raise PluggyError('Escolha se deseja consultar o histórico completo da conta.')
        from pluggy_sync import revision
        current_revision = revision(self.store,'Inter')
        config = self.pluggy.load_config()
        item_id = config.get("inter_item_id")
        if not item_id:
            raise PluggyError("Informe o itemId do Inter pessoal nas Configurações.")
        auth = http_json("POST", "/auth", {"clientId": config["client_id"],
                                           "clientSecret": config["client_secret"]})
        api_key = auth.get("apiKey")
        if not isinstance(api_key, str) or not api_key:
            raise PluggyError("A Pluggy não devolveu uma chave de sessão válida.")
        accounts = _pages("/accounts?" + urlencode({"itemId": item_id}), api_key, http_json)
        relevant = [account for account in accounts if account.get("type") in ("BANK", "CREDIT")]
        if not relevant:
            raise PluggyError("Esse item não trouxe conta nem cartão. Confira o itemId do Inter e o consentimento.")
        if sum(account.get("type") == "BANK" for account in relevant) > 1 or \
                sum(account.get("type") == "CREDIT" for account in relevant) > 1:
            raise PluggyError("O Inter trouxe várias contas ou cartões. Precisamos escolher o vínculo antes de importar.")
        for account in relevant:
            if account.get("currencyCode") != "BRL" or not UUID.fullmatch(str(account.get("id", ""))):
                raise PluggyError("A Pluggy devolveu uma conta inválida ou fora de reais.")

        prepared = []
        starts_on = live_start(self.store)
        card_starts_on = card_live_start(self.store, starts_on)
        account_starts_on = account_live_start(self.store, starts_on)
        periods = []
        skipped = Counter()
        account_summaries = []
        bank_balance = None
        seen_keys = set()
        for account in relevant:
            is_card = account["type"] == "CREDIT"
            source_start = card_starts_on if is_card else account_starts_on
            source = CARD_SOURCE if is_card else ACCOUNT_SOURCE
            account_id = account["id"]
            bills = _pages("/bills?" + urlencode({"accountId": account_id}), api_key, http_json) if is_card else []
            bill_months = {}
            for bill in bills:
                if UUID.fullmatch(str(bill.get("id", ""))) and bill.get("dueDate"):
                    bill_months[bill["id"]] = _iso_date(bill["dueDate"], "uma fatura")[:7]
            transactions = _transactions(account_id, api_key, http_json)
            dates = [_iso_date(row.get("date"), "uma transação") for row in transactions
                     if is_card or row.get('status') == 'POSTED']
            dates = [value for value in dates if value <= date.today().isoformat()]
            if full_history and not is_card and dates:
                source_start = min(dates)
                account_starts_on = min(account_starts_on, source_start)
            updated_on = _iso_date(account.get("updatedAt") or datetime.now(timezone.utc).isoformat(), "a conta")
            if dates:
                from pluggy_sync import covered_start
                period_start = min(dates) if full_history and not is_card else max(
                    source_start, covered_start(self.store, source, "personal", min(dates)))
                period_end = min(date.today().isoformat(), max(updated_on, max(dates)))
                if period_start <= period_end:
                    periods.append({"source": source, "scope": "personal", "starts_on": period_start,
                                    "ends_on": period_end})
            account_summaries.append({"type": "card" if is_card else "account",
                                      "name": str(account.get("name") or ("Cartão" if is_card else "Conta"))[:80],
                                      "source": source, "received": len(transactions)})
            if not is_card and account.get("balance") is not None:
                bank_balance = {"source": source, "as_of": _iso_date(account.get("updatedAt") or
                                                                        datetime.now(timezone.utc).isoformat(), "a conta"),
                                "amount_cents": _cents(account["balance"])}
            for row in transactions:
                if row.get("status") != "POSTED" and not (is_card and row.get("status") == "PENDING"):
                    skipped["pending"] += 1
                    continue
                if row.get("currencyCode") != "BRL" or not UUID.fullmatch(str(row.get("id", ""))):
                    raise PluggyError("Uma transação veio sem ID válido ou fora de reais.")
                description = re.sub(r"[\x00-\x1f]+", " ", str(row.get("description") or "")).strip()[:120]
                if not description:
                    raise PluggyError("Uma transação veio sem descrição.")
                occurred_on = _iso_date(row.get("date"), "uma transação")
                if occurred_on < source_start:
                    skipped["historical"] += 1
                    continue
                if occurred_on > date.today().isoformat():
                    skipped["future"] += 1
                    continue
                amount = _cents(row.get("amount"))
                if amount == 0:
                    skipped["zero"] += 1
                    continue
                if row.get("type") not in ("DEBIT", "CREDIT"):
                    raise PluggyError("Uma transação veio sem tipo de entrada ou saída.")
                kind = "expense" if row["type"] == "DEBIT" else "income"
                if is_card and ((kind == "expense" and amount < 0) or
                                (kind == "income" and amount > 0)):
                    raise PluggyError("Uma transação do cartão veio com sinal inconsistente.")
                if is_card and card_payment(row, description):
                    skipped["card_payment"] += 1
                    continue
                if is_card:
                    metadata = row.get("creditCardMetadata") or {}
                    bill_id = metadata.get("billId") if isinstance(metadata, dict) else None
                    invoice_month = bill_months.get(bill_id)
                    forecast = metadata.get("billForecastDate") if isinstance(metadata, dict) else None
                    if not invoice_month and isinstance(forecast, str) and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", forecast):
                        invoice_month = forecast
                    if not invoice_month:
                        raise PluggyError("Um lançamento do cartão veio sem mês de fatura. Nada será substituído até conferir esse período.")
                    if invoice_month < starts_on[:7]:
                        skipped["historical"] += 1
                        continue
                    category, excluded = ("Estorno", False) if kind == "income" else ("Importado", False)
                else:
                    invoice_month = None
                    category, excluded = _bank_classification(row, description, kind)
                import_key = hashlib.sha256(f"pluggy|inter|{account_id}|{row['id']}".encode()).hexdigest()
                if import_key in seen_keys:
                    skipped["already_imported"] += 1
                    continue
                seen_keys.add(import_key)
                prepared.append({"kind": kind, "description": description, "category": category,
                                 "amount_cents": abs(amount), "occurred_on": occurred_on,
                                 "excluded": excluded, "source": source, "import_key": import_key,
                                 "invoice_month": invoice_month, "scope": "personal",
                                 "pending": int(row.get("status") == "PENDING")})
        if len(prepared) > MAX_TRANSACTIONS:
            raise PluggyError("Há mais de 5.000 novos lançamentos. Importe um período menor antes de sincronizar.")
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._pending = {"token": token, "expires": time.monotonic() + 600,
                             "items": prepared, "balance": bank_balance, "periods": periods, "revision":current_revision}
            self._pending["starts_on"] = starts_on
            self._pending["card_starts_on"] = card_starts_on
            self._pending['account_history_start'] = account_starts_on
            self._pending['full_history'] = full_history
        from pluggy_sync import replacement_counts
        replacements = replacement_counts(self.store, periods)
        return {"preview_token": token, "accounts": account_summaries,
                "new_count": len(prepared), "skipped": dict(skipped),
                "periods": periods, "replaced_count": replacements,
                "live_starts_on": starts_on,
                "card_live_starts_on": card_starts_on,
                "full_history": full_history,
                "account_history_start": account_starts_on,
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "bank_balance": bank_balance,
                "new_items": [{key: row[key] for key in ("kind", "description", "amount_cents",
                                                      "occurred_on", "source", "invoice_month",
                                                      "category", "excluded", "pending")}
                              for row in prepared],
                "note": f"Conta: operações disponíveis desde {account_starts_on}. Cartão: compras desde {card_starts_on}, após o histórico das faturas importadas. Pagamentos de cartão não são compras. Categorias reconhecidas são preservadas; compras pendentes são provisórias."}

    def import_preview(self, token: object) -> dict:
        with self._lock:
            pending = self._pending
            if not isinstance(token, str) or not pending or token != pending["token"] or \
                    time.monotonic() > pending["expires"]:
                raise PluggyError("A prévia expirou. Consulte o Inter novamente antes de importar.")
            self._pending = None
        from pluggy_sync import apply_inter
        return apply_inter(self.store, pending)
