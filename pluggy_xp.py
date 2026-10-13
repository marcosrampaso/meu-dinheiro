"""Read-only XP investment preview through a personal Meu Pluggy connection."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
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
