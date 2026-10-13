"""Public market quotes and cautious estimates for an imported XP position."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from finance import FinanceStore, ValidationError


INTERVAL_SECONDS = 3 * 60 * 60
CVM_URL = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{month}.zip"
BRAPI_URL = "https://brapi.dev/api/v2/stocks/quote?symbols={symbol}"
CVM_SOURCE_URL = "https://dados.cvm.gov.br/dataset/fi-doc-inf_diario"
BRAPI_SOURCE_URL = "https://brapi.dev/docs"
CNPJ_PATTERN = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
TICKER_PATTERN = re.compile(r"[A-Z]{4}\d{1,2}")


def asset_source(name: str, category: str, fund_cnpjs: dict[str, str]) -> tuple[str, str] | None:
    if category == "Fundos de Investimentos":
        identifier = fund_cnpjs.get(name.casefold())
        return ("cvm", identifier) if identifier else None
    symbol = name.strip().upper()
    if category in ("Ações", "Fundos Imobiliários") and TICKER_PATTERN.fullmatch(symbol):
        return ("brapi", symbol)
    return None


def _positive_decimal(value: object) -> Decimal:
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Cotação inválida.") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("Cotação inválida.")
    return number


def _cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _download(url: str, headers: dict[str, str] | None = None, limit: int = 20_000_000) -> bytes:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, file, code, message, headers, new_url):
            return None

    request = urllib.request.Request(url, headers={"User-Agent": "MeuDinheiro/1.0", **(headers or {})})
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(request, timeout=25) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Resposta de cotações muito grande.")
    return payload


def fetch_cvm(month: str, identifiers: set[str], today: date | None = None) -> list[dict]:
    """Read only target CNPJs from the CVM daily NAV archive for one month."""
    if not re.fullmatch(r"\d{6}", month):
        raise ValueError("Mês inválido.")
    today = today or date.today()
    payload = _download(CVM_URL.format(month=month))
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        candidates = [item for item in archive.infolist() if item.filename.lower().endswith(".csv")]
        if len(candidates) != 1 or candidates[0].file_size > 80_000_000:
            raise ValueError("Arquivo de cotas da CVM inesperado.")
        with archive.open(candidates[0]) as binary:
            reader = csv.DictReader(io.TextIOWrapper(binary, encoding="utf-8-sig"), delimiter=";")
            required = {"CNPJ_FUNDO_CLASSE", "DT_COMPTC", "VL_QUOTA"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("Formato de cotas da CVM mudou.")
            quotes = {}
            for row in reader:
                identifier = row["CNPJ_FUNDO_CLASSE"]
                if identifier not in identifiers or row.get("ID_SUBCLASSE"):
                    continue
                quote_date = date.fromisoformat(row["DT_COMPTC"])
                if quote_date > today or quote_date.strftime("%Y%m") != month:
                    continue
                price = _positive_decimal(row["VL_QUOTA"])
                quotes[(identifier, quote_date.isoformat())] = {
                    "source": "cvm", "identifier": identifier,
                    "quote_date": quote_date.isoformat(), "price": str(price),
                }
    return list(quotes.values())


def fetch_brapi(symbol: str, token: str, today: date | None = None) -> dict:
    if not TICKER_PATTERN.fullmatch(symbol):
        raise ValueError("Ativo não suportado.")
    payload = json.loads(_download(BRAPI_URL.format(symbol=symbol),
                                   {"Authorization": f"Bearer {token}"}, 1_000_000))
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise ValueError("Resposta da brapi sem cotação única.")
    item = results[0]
    data = item.get("data", item)
    if item.get("requestedSymbol", item.get("symbol")) != symbol or data.get("symbol", symbol) != symbol:
        raise ValueError("A brapi devolveu outro ativo.")
    price = _positive_decimal(data.get("regularMarketPrice"))
    raw_time = data.get("regularMarketTime")
    if isinstance(raw_time, (int, float)):
        quoted = datetime.fromtimestamp(raw_time, timezone.utc)
    elif isinstance(raw_time, str):
        quoted = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        if quoted.tzinfo is None:
            raise ValueError("Horário da cotação sem fuso.")
    else:
        raise ValueError("Cotação sem horário de mercado.")
    quote_date = quoted.astimezone(ZoneInfo("America/Sao_Paulo")).date()
    if quote_date > (today or date.today()) or quote_date < (today or date.today()) - timedelta(days=14):
        raise ValueError("Data da cotação fora do intervalo esperado.")
    return {"source": "brapi", "identifier": symbol,
            "quote_date": quote_date.isoformat(), "price": str(price)}


class MarketQuotes:
    def __init__(self, store: FinanceStore, token_path: Path):
        self.store = store
        self.token_path = token_path
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._checked_at: str | None = None
        self._errors: dict[str, str] = {}
        self._thread: threading.Thread | None = None

    def fund_cnpjs(self) -> dict[str, str]:
        path = self.store.path.parent / "market-assets.json"
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            values = raw.get("fund_cnpjs", {})
            if not isinstance(values, dict):
                return {}
            return {name.casefold(): cnpj for name, cnpj in values.items()
                    if isinstance(name, str) and isinstance(cnpj, str)
                    and CNPJ_PATTERN.fullmatch(cnpj)}
        except (OSError, UnicodeError, ValueError, AttributeError):
            return {}

    def token_configured(self) -> bool:
        return self.token_path.is_file() and bool(self.token_path.read_text(encoding="utf-8").strip())

    def save_token(self, token: object) -> None:
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._-]{10,512}", token):
            raise ValidationError("Token da brapi inválido.")
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.token_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(token)
            os.replace(temporary, self.token_path)
        finally:
            temporary.unlink(missing_ok=True)
        self._wake.set()

    def delete_token(self) -> None:
        self.token_path.unlink(missing_ok=True)
        self._wake.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="market-quotes", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while True:
            self.refresh()
            self._wake.wait(INTERVAL_SECONDS)
            self._wake.clear()

    def refresh(self) -> None:
        position = self.store.latest_investment_position()
        errors: dict[str, str] = {}
        if position:
            today = date.today()
            fund_cnpjs = self.fund_cnpjs()
            fund_ids = {identifier for asset in position["assets"]
                        if (source := asset_source(asset["name"], asset["category"], fund_cnpjs)) and source[0] == "cvm"
                        for identifier in [source[1]]}
            months = {today.strftime("%Y%m"), position["as_of"][:7].replace("-", "")}
            if fund_ids:
                for month in months:
                    try:
                        quotes = fetch_cvm(month, fund_ids, today)
                        checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
                        self.store.save_market_quotes([{**quote, "checked_at": checked} for quote in quotes])
                        if not quotes:
                            errors["cvm"] = "CVM sem cotas para os fundos neste mês."
                    except (ValueError, OSError, urllib.error.URLError, zipfile.BadZipFile) as exc:
                        errors["cvm"] = f"Não foi possível consultar as cotas da CVM: {exc}"
            token = self.token_path.read_text(encoding="utf-8").strip() if self.token_configured() else None
            if token:
                for symbol in sorted({source[1] for asset in position["assets"]
                                      if (source := asset_source(asset["name"], asset["category"], fund_cnpjs)) and source[0] == "brapi"}):
                    try:
                        quote = fetch_brapi(symbol, token, today)
                        self.store.save_market_quotes([{**quote, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}])
                    except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                        errors[symbol] = f"Cotação indisponível para {symbol}: {exc}"
        with self._lock:
            self._checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._errors = errors

    def overview(self) -> dict:
        position = self.store.latest_investment_position()
        with self._lock:
            checked_at, errors = self._checked_at, dict(self._errors)
        result = {"as_of": position["as_of"] if position else None,
                  "checked_at": checked_at, "interval_hours": 3,
                  "brapi_token_configured": self.token_configured(), "errors": errors,
                  "assets": []}
        if not position:
            return result
        snapshot_date = date.fromisoformat(position["as_of"])
        fund_cnpjs = self.fund_cnpjs()
        for asset in position["assets"]:
            source = asset_source(asset["name"], asset["category"], fund_cnpjs)
            row = {"name": asset["name"], "snapshot_value_cents": asset["value_cents"],
                   "cost_cents": asset["cost_cents"], "estimated_value_cents": None,
                   "gain_cents": None, "quote_date": None, "source": source[0] if source else None,
                   "source_url": CVM_SOURCE_URL if source and source[0] == "cvm" else BRAPI_SOURCE_URL if source else None,
                   "status": "Informe o CNPJ deste fundo em data/market-assets.json." if asset["category"] == "Fundos de Investimentos" else "Ativo ainda sem fonte de cotação configurada."}
            if source:
                quotes = self.store.market_quotes(*source)
                latest = quotes[0] if quotes else None
                if source[0] == "brapi":
                    if not result["brapi_token_configured"]:
                        row["status"] = "Adicione o token gratuito da brapi."
                    elif latest and latest["quote_date"] >= position["as_of"] and (date.today() - date.fromisoformat(latest["quote_date"])).days <= 14:
                        try:
                            quantity = _positive_decimal(asset["quantity"])
                            row["estimated_value_cents"] = _cents(quantity * _positive_decimal(latest["price"]))
                            row["quote_date"] = latest["quote_date"]
                            row["status"] = "Estimativa por preço de mercado; proventos não incluídos."
                        except ValueError:
                            row["status"] = "Quantidade inválida na posição da XP."
                    else:
                        row["status"] = errors.get(source[1], "Aguardando cotação da brapi desde a posição importada.")
                else:
                    baseline = next((quote for quote in quotes if quote["quote_date"] <= position["as_of"]), None)
                    if (baseline and (snapshot_date - date.fromisoformat(baseline["quote_date"])).days <= 7
                            and latest and latest["quote_date"] > position["as_of"]
                            and (date.today() - date.fromisoformat(latest["quote_date"])).days <= 7):
                        ratio = _positive_decimal(latest["price"]) / _positive_decimal(baseline["price"])
                        row["estimated_value_cents"] = _cents(Decimal(asset["value_cents"]) / 100 * ratio)
                        row["quote_date"] = latest["quote_date"]
                        row["base_quote_date"] = baseline["quote_date"]
                        row["status"] = "Estimativa por variação da cota CVM; pressupõe quantidade constante."
                    else:
                        row["status"] = errors.get("cvm", "Aguardando nova cota CVM após a posição importada.")
            if row["estimated_value_cents"] is not None:
                row["gain_cents"] = row["estimated_value_cents"] - asset["cost_cents"]
            result["assets"].append(row)
        result["complete"] = bool(result["assets"]) and all(a["estimated_value_cents"] is not None for a in result["assets"])
        if result["complete"]:
            result["estimated_assets_cents"] = sum(a["estimated_value_cents"] for a in result["assets"])
            result["estimated_gain_cents"] = sum(a["gain_cents"] for a in result["assets"])
        return result
