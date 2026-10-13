"""Daily CDI benchmark from the Banco Central SGS series 12."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from finance import FinanceStore, ValidationError
from market_quotes import INTERVAL_SECONDS, _download


SOURCE_URL = "https://www3.bcb.gov.br/sgspub/consultarvalores/consultarValoresSeries.do?method=consultarSeries&series=12"
API_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"


def fetch_cdi(start: date, end: date) -> list[dict]:
    if start > end or (end - start).days > 410:
        raise ValueError("Intervalo do CDI inválido.")
    query = urlencode({"formato": "json", "dataInicial": start.strftime("%d/%m/%Y"),
                       "dataFinal": end.strftime("%d/%m/%Y")})
    rows = json.loads(_download(f"{API_URL}?{query}", limit=100_000))
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValueError("Resposta do CDI inesperada.")
    result = {}
    for row in rows:
        try:
            day = datetime.strptime(row["data"], "%d/%m/%Y").date()
            rate = Decimal(row["valor"].replace(",", "."))
        except (KeyError, TypeError, ValueError, InvalidOperation, AttributeError) as exc:
            raise ValueError("Dados do CDI inválidos.") from exc
        if not start <= day <= end or not rate.is_finite() or not 0 <= rate < 1 or day in result:
            raise ValueError("Dados do CDI inválidos.")
        result[day] = {"source": "bcb", "identifier": "12", "quote_date": day.isoformat(),
                       "price": str(rate)}
    return [result[day] for day in sorted(result)]


def _months_before(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    month += 1
    last_day = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    return date(year, month, min(day.day, last_day.day))


def compound_cdi(rows: list[dict], start: date, end: date) -> list[dict]:
    factor = Decimal(1)
    points = [{"date": start.isoformat(), "cumulative_percent": 0.0}]
    for row in sorted(rows, key=lambda item: item["quote_date"]):
        day = date.fromisoformat(row["quote_date"])
        if not start <= day <= end:
            continue
        factor *= 1 + Decimal(row["price"]) / 100
        points.append({"date": day.isoformat(),
                       "cumulative_percent": round(float((factor - 1) * 100), 6)})
    return points


class CDIBenchmark:
    def __init__(self, store: FinanceStore):
        self.store = store
        self._checked_at: str | None = None
        self._error: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="cdi-benchmark", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            self.refresh()
            threading.Event().wait(INTERVAL_SECONDS)

    def refresh(self, today: date | None = None) -> None:
        today = today or date.today()
        try:
            recent_start = today - timedelta(days=400)
            rates = fetch_cdi(recent_start, today)
            if not rates:
                raise ValueError("O Banco Central não retornou taxas do CDI.")
            checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.store.save_market_quotes([{**row, "checked_at": checked} for row in rates])
            with self.store._connect() as connection:
                earliest_position = connection.execute(
                    "SELECT MIN(as_of) FROM investment_snapshots").fetchone()[0]
                oldest_rate = connection.execute("""SELECT MIN(quote_date) FROM market_quotes
                    WHERE source = 'bcb' AND identifier = '12'""").fetchone()[0]
            if earliest_position:
                first = date.fromisoformat(earliest_position)
                if first < recent_start and (oldest_rate is None or
                                             date.fromisoformat(oldest_rate) > first + timedelta(days=7)):
                    chunk_start = first
                    historical_end = recent_start - timedelta(days=1)
                    while chunk_start <= historical_end:
                        chunk_end = min(chunk_start + timedelta(days=400), historical_end)
                        older = fetch_cdi(chunk_start, chunk_end)
                        self.store.save_market_quotes([{**row, "checked_at": checked} for row in older])
                        chunk_start = chunk_end + timedelta(days=1)
            self._checked_at = checked
            self._error = None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._error = str(exc)

    def overview(self, months: int, today: date | None = None) -> dict:
        if months not in (1, 3, 6, 12):
            raise ValidationError("Escolha 1, 3, 6 ou 12 meses para o CDI.")
        today = today or date.today()
        start = _months_before(today, months)
        cached = self.store.market_quotes("bcb", "12")
        points = compound_cdi(cached, start, today)
        latest = points[-1]["date"] if len(points) > 1 else None
        return {"period_months": months, "starts_on": start.isoformat(),
                "latest_on": latest, "cumulative_percent": points[-1]["cumulative_percent"] if latest else None,
                "points": points if latest else [], "checked_at": self._checked_at,
                "stale": bool(latest and (today - date.fromisoformat(latest)).days > 7),
                "error": self._error, "source_url": SOURCE_URL}
