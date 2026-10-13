"""Read-only XP investment preview through a personal Meu Pluggy connection."""

from __future__ import annotations

import json
import os
import re
import tempfile
import hashlib
import secrets
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from finance import FinanceStore, ValidationError


API_BASE = "https://api.pluggy.ai"
UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")


class PluggyError(Exception):
    """An authentication, coverage, or remote service problem."""


def _http_json(method: str, path: str, payload: dict | None = None, api_key: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["X-API-KEY"] = api_key
    request = Request(API_BASE + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise PluggyError("A Pluggy recusou as credenciais ou o acesso a este item. Confira o Meu Pluggy.") from exc
        if exc.code == 429:
            raise PluggyError("Limite de consultas da Pluggy atingido. Tente novamente mais tarde.") from exc
        raise PluggyError(f"A Pluggy não respondeu à consulta (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise PluggyError("Não foi possível consultar a Pluggy agora. Confira a conexão e tente novamente.") from exc
    if not isinstance(result, dict):
        raise PluggyError("A Pluggy devolveu um formato inesperado.")
    return result


def _cents(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise PluggyError("Um investimento veio sem valor; a carteira não foi aplicada.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PluggyError("Um investimento veio com valor inválido; a carteira não foi aplicada.") from exc
    if not amount.is_finite() or amount < 0:
        raise PluggyError("Um investimento veio com valor inválido; a carteira não foi aplicada.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _fetch_assets(config: dict, http_json=_http_json) -> list[dict]:
    auth = http_json("POST", "/auth", {"clientId": config["client_id"],
                                         "clientSecret": config["client_secret"]})
    api_key = auth.get("apiKey")
    if not isinstance(api_key, str) or not api_key:
        raise PluggyError("A Pluggy não devolveu uma chave de sessão válida.")
    assets = []
    for page in range(1, 21):
        path = "/investments?" + urlencode({"itemId": config["item_id"], "page": page, "pageSize": 100})
        response = http_json("GET", path, api_key=api_key)
        rows = response.get("results")
        pages = response.get("totalPages")
        if not isinstance(rows, list) or not isinstance(pages, int) or not 1 <= pages <= 20:
            raise PluggyError("A Pluggy devolveu uma lista incompleta ou inesperada.")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise PluggyError("A Pluggy devolveu um investimento incompleto.")
            if row.get("currencyCode") != "BRL":
                raise PluggyError("Há um ativo fora de reais; confira a carteira antes de sincronizar.")
            if row.get("status") == "TOTAL_WITHDRAWAL":
                continue
            assets.append({"name": row["name"].strip(), "type": row.get("type"),
                           "subtype": row.get("subtype"), "code": row.get("code"),
                           "gross_cents": _cents(row.get("amount")),
                           "net_cents": _cents(row.get("balance")),
                           "quantity": row.get("quantity"), "quote_date": row.get("date")})
        if page >= pages:
            break
    if not assets:
        raise PluggyError("Nenhum investimento veio desse item. Confira se ele corresponde à XP e se o consentimento inclui investimentos.")
    return assets


class PluggyXP:
    def __init__(self, store: FinanceStore, config_path: Path):
        self.store = store
        self.config_path = config_path
        self._sync_lock = threading.Lock()
        self._sync_pending = None

    def clear_preview(self):
        with self._sync_lock:
            self._sync_pending = None

    def configured(self) -> bool:
        try:
            return bool(self.load_config().get("item_id"))
        except PluggyError:
            return False

    def inter_configured(self) -> bool:
        try:
            return bool(self.load_config().get("inter_item_id"))
        except PluggyError:
            return False

    def load_config(self) -> dict:
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PluggyError("Configure o Meu Pluggy antes de consultar os dados.") from exc
        if not isinstance(config, dict) or not config.get("client_id") or not config.get("client_secret"):
            raise PluggyError("As credenciais da Pluggy estão incompletas.")
        return config

    def save_config(self, data: dict) -> None:
        if not isinstance(data, dict):
            raise ValidationError("Configuração da Pluggy inválida.")
        try:
            previous = self.load_config()
        except PluggyError:
            previous = {}
        values = {key: data.get(key, "") for key in ("client_id", "client_secret", "item_id", "inter_item_id")}
        if any(not isinstance(value, str) or len(value) > 300 for value in values.values()):
            raise ValidationError("Informe credenciais e itemIds válidos.")
        values = {key: value.strip() or previous.get(key, "") for key, value in values.items()}
        if not values["client_id"] or not values["client_secret"] or not (values["item_id"] or values["inter_item_id"]):
            raise ValidationError("Informe Client ID, Client Secret e ao menos um itemId.")
        if any(value and not UUID.fullmatch(value) for value in (values["item_id"], values["inter_item_id"])):
            raise ValidationError("Os itemIds devem ser UUIDs válidos.")
        if values["item_id"] and values["item_id"] == values["inter_item_id"]:
            raise ValidationError("XP e Inter precisam ter itemIds diferentes.")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".pluggy-xp-", dir=self.config_path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(values, output)
            os.replace(temporary, self.config_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def delete_config(self) -> None:
        self.config_path.unlink(missing_ok=True)

    def preview(self, http_json=_http_json) -> dict:
        config = self.load_config()
        if not config.get("item_id"):
            raise PluggyError("Informe o itemId da XP nas Configurações.")
        assets = _fetch_assets(config, http_json)
        with self.store._connect() as connection:
            current = [dict(row) for row in connection.execute("""SELECT name, category, value_cents
                FROM investment_positions WHERE as_of = (SELECT MAX(as_of) FROM investment_snapshots)
                ORDER BY value_cents DESC""")]
        return {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "assets": assets, "gross_cents": sum(row["gross_cents"] for row in assets),
                "net_cents": sum(row["net_cents"] for row in assets),
                "existing_assets": current,
                "note": "Prévia dos ativos. Saldo disponível, proventos e cobertura precisam ser conferidos antes da sincronização automática."}

    def preview_sync(self, http_json=_http_json) -> dict:
        from pluggy_inter import _pages, _transactions, _iso_date, _cents as signed_cents
        from manual_investments import imported_flow_kind
        config = self.load_config()
        if not config.get("item_id"):
            raise PluggyError("Configure o itemId da XP.")
        auth = http_json("POST", "/auth", {"clientId": config["client_id"], "clientSecret": config["client_secret"]})
        api_key = auth.get("apiKey")
        if not isinstance(api_key, str) or not api_key:
            raise PluggyError("A Pluggy não devolveu uma chave válida.")
        raw_assets = _pages("/investments?" + urlencode({"itemId": config["item_id"]}), api_key, http_json)
        accounts = _pages("/accounts?" + urlencode({"itemId": config["item_id"]}), api_key, http_json)
        previous = self.store.latest_investment_position()
        previous_assets = {a["name"]: a for a in (previous or {}).get("assets", [])}
        assets = []
        for row in raw_assets:
            if row.get("status") == "TOTAL_WITHDRAWAL":
                continue
            if row.get("currencyCode") != "BRL" or not isinstance(row.get("name"), str) or not row["name"].strip():
                raise PluggyError("Um ativo da XP veio incompleto ou fora de reais.")
            category = "Fundos Imobiliários" if row.get("subtype") == "REAL_ESTATE_FUND" else \
                       "Fundos de Investimentos" if row.get("type") == "MUTUAL_FUND" else \
                       "Ações" if row.get("type") == "EQUITY" else None
            if category is None:
                raise PluggyError("A XP trouxe uma classe de ativo ainda não suportada. Nada será substituído.")
            name = row["name"].strip()
            old = previous_assets.get(name)
            quantity = str(row["quantity"]) if row.get("quantity") is not None else None
            if quantity is not None:
                try:
                    parsed_quantity = Decimal(quantity)
                    if not parsed_quantity.is_finite() or parsed_quantity < 0:
                        raise ValueError
                except (InvalidOperation, ValueError) as exc:
                    raise PluggyError("Um ativo veio com quantidade inválida.") from exc
            cost = _cents(row["amountOriginal"]) if row.get("amountOriginal") is not None else None
            if cost is None and old and old.get("cost_known", 1):
                old_quantity = old.get("quantity")
                if ((old_quantity is not None and quantity is not None and Decimal(old_quantity) == Decimal(quantity)) or
                        (old_quantity is None and previous["as_of"] == date.today().isoformat())):
                    cost = old["cost_cents"]
            assets.append({"name": name, "category": category, "value_cents": _cents(row.get("amount")),
                           "cost_cents": cost if cost is not None else 0, "cost_known": int(cost is not None),
                           "quantity": quantity, "net_cents": _cents(row["balance"]) if row.get("balance") is not None else None,
                           "quote_date": _iso_date(row["date"], "a cota") if row.get("date") else None})
        if not assets or len({a["name"] for a in assets}) != len(assets):
            raise PluggyError("A carteira veio vazia ou com ativos repetidos. Nada será substituído.")
        entries, dates, cash, updated_dates = [], [], 0, []
        seen = set()
        for account in accounts:
            if account.get("type") != "BANK":
                continue
            if account.get("currencyCode") != "BRL" or not UUID.fullmatch(str(account.get("id", ""))):
                raise PluggyError("A XP trouxe uma conta inválida.")
            cash += signed_cents(account.get("balance"))
            updated_dates.append(_iso_date(account.get("updatedAt") or datetime.now(timezone.utc).isoformat(), "a conta XP"))
            for row in _transactions(account["id"], api_key, http_json):
                occurred_on = _iso_date(row.get("date"), "uma movimentação XP")
                dates.append(occurred_on)
                if row.get("status") != "POSTED" or occurred_on > date.today().isoformat():
                    continue
                if row.get("currencyCode") != "BRL" or row.get("type") not in ("CREDIT", "DEBIT") or not UUID.fullmatch(str(row.get("id", ""))):
                    raise PluggyError("Uma movimentação XP veio incompleta.")
                amount = abs(signed_cents(row.get("amount"))) * (1 if row["type"] == "CREDIT" else -1)
                if not amount:
                    continue
                description = re.sub(r"[\x00-\x1f]+", " ", str(row.get("description") or "")).strip()[:240]
                if not description:
                    raise PluggyError("Uma movimentação XP veio sem descrição.")
                key = hashlib.sha256(f"pluggy|xp|{account['id']}|{row['id']}".encode()).hexdigest()
                if key in seen:
                    continue
                seen.add(key)
                classification = imported_flow_kind(description, amount)
                plain = description.casefold()
                if row.get("operationType") in ("PIX", "TED") and not any(word in plain for word in ("conta investimento", "aplica", "resgate")):
                    classification = "external"
                entries.append({"import_key": key, "occurred_at": occurred_on + "T12:00:00",
                                "occurred_on": occurred_on, "description": description, "amount_cents": amount,
                                "balance_cents": signed_cents(row["balance"]) if row.get("balance") is not None else 0,
                                "classification": classification if classification in ("external", "internal") else None})
        if not any(a.get("type") == "BANK" for a in accounts):
            raise PluggyError("A XP não trouxe as contas para conferir o saldo e as movimentações.")
        from pluggy_sync import covered_start
        period = {"starts_on": covered_start(self.store, "XP", "personal", min(dates)),
                  "ends_on": min(date.today().isoformat(), max(updated_dates))} if dates else None
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        snapshot = {"as_of": date.today().isoformat(), "assets_cents": sum(a["value_cents"] for a in assets),
                    "cash_cents": cash, "checked_at": checked_at, "assets": assets}
        snapshot["total_cents"] = snapshot["assets_cents"] + cash
        with self.store._connect() as connection:
            replaced = connection.execute("SELECT COUNT(*) FROM investment_entries WHERE source='XP' AND substr(occurred_at,1,10) BETWEEN ? AND ?",
                (period["starts_on"],period["ends_on"])).fetchone()[0] if period else 0
        token = secrets.token_urlsafe(24)
        with self._sync_lock:
            self._sync_pending = {"token": token, "expires": time.monotonic()+600, "entries": entries,
                                  "snapshot": snapshot, "period": period}
        return {"preview_token": token, "checked_at": checked_at, "period": period, "count": len(entries),
                "replaced_count": replaced, "snapshot": snapshot,
                "note": "A Pluggy substitui as movimentações nas datas acima e atualiza os valores dos ativos. Custos ausentes usam a referência local quando possível. Proventos a receber não foram fornecidos: o patrimônio mostrado é parcial e não entra no comparador com o CDI."}

    def import_preview(self, token):
        with self._sync_lock:
            pending = self._sync_pending
            if not isinstance(token, str) or not pending or token != pending["token"] or time.monotonic() > pending["expires"]:
                raise PluggyError("A prévia XP expirou. Consulte novamente.")
            self._sync_pending = None
        from pluggy_sync import apply_xp
        return apply_xp(self.store, pending)
