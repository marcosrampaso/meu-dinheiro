"""Local web interface and versioned JSON API for personal finances."""

from __future__ import annotations

import argparse
import json
import os
import threading
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from finance import FinanceStore, NotFoundError, PURCHASE_CATEGORIES, ValidationError, validate_month, validate_scope
from categorizer import CategorizationError, MissingAPIKey, MODEL, suggest_categories
from importers import (account_balance_from_csv, account_daily_balances_from_csv,
                       account_period_from_csv, inspect_import, prepare_import)


BASE_DIR = Path(__file__).resolve().parent
STATIC_FILES = {
    "/": (BASE_DIR / "static" / "index.html", "text/html; charset=utf-8"),
    "/styles.css": (BASE_DIR / "static" / "styles.css", "text/css; charset=utf-8"),
    "/app.js": (BASE_DIR / "static" / "app.js", "text/javascript; charset=utf-8"),
    "/import.js": (BASE_DIR / "static" / "import.js", "text/javascript; charset=utf-8"),
    "/charts.js": (BASE_DIR / "static" / "charts.js", "text/javascript; charset=utf-8"),
    "/categories.js": (BASE_DIR / "static" / "categories.js", "text/javascript; charset=utf-8"),
    "/manual-card.js": (BASE_DIR / "static" / "manual-card.js", "text/javascript; charset=utf-8"),
}
API_PREFIX = "/api/v1"


class FinanceHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], store: FinanceStore):
        super().__init__(address, FinanceHandler)
        self.store = store


class FinanceHandler(BaseHTTPRequestHandler):
    server: FinanceHTTPServer

    def do_GET(self) -> None:
        self._handle(self._get)

    def do_POST(self) -> None:
        self._handle(self._post)

    def do_PUT(self) -> None:
        self._handle(self._put)

    def do_DELETE(self) -> None:
        self._handle(self._delete)

    def _handle(self, action) -> None:
        try:
            if self.headers.get("Host") not in (
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            ):
                self._json(HTTPStatus.FORBIDDEN, {"error": "Host não permitido."})
                return
            action()
        except ValidationError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except MissingAPIKey as exc:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
        except CategorizationError as exc:
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except NotFoundError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _route(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlsplit(self.path)
        return parsed.path, parse_qs(parsed.query)

    def _month(self, query: dict[str, list[str]]) -> str:
        return validate_month(query.get("month", [date.today().strftime("%Y-%m")])[0])

    def _scope(self, query: dict[str, list[str]], allow_all: bool = True) -> str:
        return validate_scope(query.get("scope", ["personal"])[0], allow_all=allow_all)

    def _transaction_id(self, path: str) -> int | None:
        prefix = API_PREFIX + "/transactions/"
        if not path.startswith(prefix):
            return None
        remainder = path[len(prefix):]
        if not remainder.isdecimal() or int(remainder) <= 0:
            raise ValidationError("ID de lançamento inválido.")
        return int(remainder)

    def _read_json(self, max_bytes: int = 16_384) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValidationError("Envie JSON com Content-Type application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValidationError("Tamanho da requisição inválido.") from exc
        if length <= 0 or length > max_bytes:
            raise ValidationError("A requisição é muito grande.")
        try:
            data = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("JSON inválido.") from exc
        if not isinstance(data, dict):
            raise ValidationError("Envie um objeto JSON.")
        return data

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return origin in (f"http://{host}", f"https://{host}")

    def _require_same_origin(self) -> bool:
        if self._same_origin():
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": "Origem não permitida."})
        return False

    def _get(self) -> None:
        path, query = self._route()
        if path in STATIC_FILES:
            file_path, content_type = STATIC_FILES[path]
            body = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == API_PREFIX + "/summary":
            self._json(HTTPStatus.OK, self.server.store.summary(self._month(query), self._scope(query)))
            return
        if path == API_PREFIX + "/analytics":
            raw_months = query.get("months", ["6"])[0]
            if not raw_months.isdecimal():
                raise ValidationError("Escolha de 1 a 24 meses para os gráficos.")
            self._json(HTTPStatus.OK, self.server.store.analytics(
                self._month(query), self._scope(query), int(raw_months)))
            return
        if path == API_PREFIX + "/transactions":
            self._json(HTTPStatus.OK, {"items": self.server.store.list_month(self._month(query), self._scope(query))})
            return
        if path == API_PREFIX + "/invoices":
            self._json(HTTPStatus.OK, {"items": self.server.store.invoice_history(self._scope(query))})
            return
        if path == API_PREFIX + "/categorization/status":
            month = query.get("month", [None])[0]
            _, count = self.server.store.uncategorized_purchases(self._scope(query), month)
            self._json(HTTPStatus.OK, {"configured": bool(os.environ.get("OPENAI_API_KEY")),
                                       "model": MODEL, "uncategorized_count": count,
                                       "categories": PURCHASE_CATEGORIES})
            return
        if path.startswith(API_PREFIX + "/invoices/"):
            self._json(HTTPStatus.OK, {"items": self.server.store.invoice_items(
                path.removeprefix(API_PREFIX + "/invoices/"),
                query.get("source", [""])[0],
                self._scope(query, allow_all=False),
            )})
            return
        transaction_id = self._transaction_id(path)
        if transaction_id is not None:
            self._json(HTTPStatus.OK, self.server.store.get(transaction_id))
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Rota não encontrada."})

    def _post(self) -> None:
        if not self._require_same_origin():
            return
        path, _ = self._route()
        if path == API_PREFIX + "/card-purchases":
            self._json(HTTPStatus.CREATED, self.server.store.add_card_purchase(self._read_json()))
            return
        if path == API_PREFIX + "/imports/inspect":
            data = self._read_json(8_000_000)
            self._json(HTTPStatus.OK, inspect_import(
                data.get("filename"), data.get("content"),
                data.get("content_base64"), data.get("pdf_password", ""),
            ))
            return
        if path == API_PREFIX + "/imports/preview":
            items = prepare_import(self._read_json(8_000_000))
            self._json(HTTPStatus.OK, {"count": len(items), "sample": items[:5]})
            return
        if path == API_PREFIX + "/imports":
            data = self._read_json(8_000_000)
            items = prepare_import(data)
            is_account_csv = data.get("mode") == "account" and data.get("filename", "").lower().endswith(".csv")
            balance = account_balance_from_csv(data.get("content")) if is_account_csv else None
            period = account_period_from_csv(data.get("content")) if is_account_csv else None
            daily_balances = account_daily_balances_from_csv(data.get("content")) if is_account_csv else []
            result = self.server.store.add_imported(items)
            if is_account_csv:
                self.server.store.save_account_statement(items[0]["source"], items[0]["scope"],
                                                         period, daily_balances, balance)
                result["matched_transfers"] = self.server.store.reconcile_internal_transfers()
            self._json(HTTPStatus.CREATED, result)
            return
        if path == API_PREFIX + "/categorization/suggest":
            data = self._read_json()
            rows, count = self.server.store.uncategorized_purchases(
                data.get("scope", "personal"), data.get("month"))
            suggestions = suggest_categories(rows) if rows else []
            by_id = {item["id"]: item["category"] for item in suggestions}
            self._json(HTTPStatus.OK, {"items": [{**row, "suggested_category": by_id[row["id"]]}
                                                  for row in rows],
                                       "remaining_count": count - len(rows), "model": MODEL})
            return
        if path == API_PREFIX + "/categorization/apply":
            data = self._read_json()
            self._json(HTTPStatus.OK, self.server.store.apply_purchase_categories(
                data.get("items"), data.get("scope", "personal")))
            return
        if path == API_PREFIX + "/transactions":
            self._json(HTTPStatus.CREATED, self.server.store.add(self._read_json()))
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Rota não encontrada."})

    def _put(self) -> None:
        if not self._require_same_origin():
            return
        path, _ = self._route()
        transaction_id = self._transaction_id(path)
        if transaction_id is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Rota não encontrada."})
            return
        self._json(HTTPStatus.OK, self.server.store.update(transaction_id, self._read_json()))

    def _delete(self) -> None:
        if not self._require_same_origin():
            return
        path, _ = self._route()
        transaction_id = self._transaction_id(path)
        if transaction_id is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Rota não encontrada."})
            return
        self.server.store.delete(transaction_id)
        self._json(HTTPStatus.OK, {"deleted": True})

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Finanças pessoais no navegador")
    parser.add_argument("--port", type=int, default=8765, help="Porta local (padrão: 8765)")
    parser.add_argument("--db", type=Path, default=BASE_DIR / "data" / "financas.db")
    parser.add_argument("--no-browser", action="store_true", help="Não abrir o navegador automaticamente")
    args = parser.parse_args()
    store = FinanceStore(args.db)
    with FinanceHTTPServer(("127.0.0.1", args.port), store) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"Finanças disponível em {url}")
        print(f"Dados salvos em {args.db.resolve()}")
        print("Pressione Ctrl+C para encerrar.")
        if not args.no_browser:
            threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nEncerrado.")


if __name__ == "__main__":
    main()
