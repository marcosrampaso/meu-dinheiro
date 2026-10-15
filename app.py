"""Local web interface and versioned JSON API for personal finances."""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from chatgpt_auth import ChatGPTAuth, ChatGPTAuthError
from finance import FinanceStore, NotFoundError, PURCHASE_CATEGORIES, ValidationError, validate_month, validate_scope
from categorizer import CategorizationError, suggest_categories
from importers import (account_balance_from_csv, account_daily_balances_from_csv,
                       account_period_from_csv, inspect_import, prepare_import)
from xp_import import parse_xp_statement
from xp_positions import parse_xp_positions
from investment_news import ResearchError, research_assets, personalize_report
from market_quotes import MarketQuotes
from cdi import CDIBenchmark
from weekly import WeeklyError, weekly_context, saved_weekly_report, save_weekly_report, generate_weekly_report
from planning import (budget_overview, save_budget, delete_budget, data_health,
                      calendar_overview, save_commitment, delete_commitment,
                      save_card_due_rule, delete_card_due_rule)
from performance import performance_overview, classify_flow
from manual_investments import (manual_overview, save_manual_flow, delete_manual_flow,
                                save_manual_snapshot, delete_manual_snapshot)
from pluggy_xp import PluggyXP, PluggyError
from pluggy_inter import PluggyInter
from local_auth import LocalAuth, LocalAuthError, SESSION_SECONDS
from webhook_client import WebhookClient, RelayError
from reserve_plan import reserve_overview, preview_reserve, save_reserve, delete_reserve
from plan_insights import history_insights, preview_history, save_history_review
from transaction_review import review_overview, apply_review


BASE_DIR = Path(__file__).resolve().parent
STATIC_FILES = {
    "/": (BASE_DIR / "static" / "index.html", "text/html; charset=utf-8"),
    "/styles.css": (BASE_DIR / "static" / "styles.css", "text/css; charset=utf-8"),
    "/dashboard.css": (BASE_DIR / "static" / "dashboard.css", "text/css; charset=utf-8"),
    "/privacy.js": (BASE_DIR / "static" / "privacy.js", "text/javascript; charset=utf-8"),
    "/plans.js": (BASE_DIR / "static" / "plans.js", "text/javascript; charset=utf-8"),
    "/review.js": (BASE_DIR / "static" / "review.js", "text/javascript; charset=utf-8"),
    "/auth.js": (BASE_DIR / "static" / "auth.js", "text/javascript; charset=utf-8"),
    "/app.js": (BASE_DIR / "static" / "app.js", "text/javascript; charset=utf-8"),
    "/import.js": (BASE_DIR / "static" / "import.js", "text/javascript; charset=utf-8"),
    "/xp-import.js": (BASE_DIR / "static" / "xp-import.js", "text/javascript; charset=utf-8"),
    "/xp-positions.js": (BASE_DIR / "static" / "xp-positions.js", "text/javascript; charset=utf-8"),
    "/investments.js": (BASE_DIR / "static" / "investments.js", "text/javascript; charset=utf-8"),
    "/news.js": (BASE_DIR / "static" / "news.js", "text/javascript; charset=utf-8"),
    "/weekly.js": (BASE_DIR / "static" / "weekly.js", "text/javascript; charset=utf-8"),
    "/planning.js": (BASE_DIR / "static" / "planning.js", "text/javascript; charset=utf-8"),
    "/performance.js": (BASE_DIR / "static" / "performance.js", "text/javascript; charset=utf-8"),
    "/manual-investments.js": (BASE_DIR / "static" / "manual-investments.js", "text/javascript; charset=utf-8"),
    "/pluggy-xp.js": (BASE_DIR / "static" / "pluggy-xp.js", "text/javascript; charset=utf-8"),
    "/webhooks.js": (BASE_DIR / "static" / "webhooks.js", "text/javascript; charset=utf-8"),
    "/charts.js": (BASE_DIR / "static" / "charts.js", "text/javascript; charset=utf-8"),
    "/categories.js": (BASE_DIR / "static" / "categories.js", "text/javascript; charset=utf-8"),
    "/manual-card.js": (BASE_DIR / "static" / "manual-card.js", "text/javascript; charset=utf-8"),
}
API_PREFIX = "/api/v1"


class FinanceHTTPServer(ThreadingHTTPServer):
    # Browsers fetch many scripts together; allow room for parallel connections.
    request_queue_size = 64

    def __init__(self, address: tuple[str, int], store: FinanceStore):
        self.local_auth = LocalAuth(BASE_DIR / "touchid.swift", store.path.parent)
        super().__init__(address, FinanceHandler)
        self.store = store
        self.auth = ChatGPTAuth(BASE_DIR / "data" / "chatgpt-auth.json")
        self.market = MarketQuotes(store, store.path.parent / "brapi-token")
        self.pluggy = PluggyXP(store, store.path.parent / "pluggy-xp.json")
        self.pluggy_inter = PluggyInter(store, self.pluggy)
        self.webhooks = WebhookClient(store,self.pluggy,self.pluggy_inter,store.path.parent/'pluggy-relay.json')
        self.market.start()
        self.cdi = CDIBenchmark(store)
        self.cdi.start()


class FinanceHandler(BaseHTTPRequestHandler):
    server: FinanceHTTPServer

    def log_request(self, code="-", size="-") -> None:
        # OAuth callback query strings contain authorization codes.
        self.log_message('"%s %s %s" %s %s', self.command, urlsplit(self.path).path,
                         self.request_version, code, size)

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
            path = urlsplit(self.path).path
            if path.startswith(API_PREFIX) and path not in (
                API_PREFIX + "/local-auth/status", API_PREFIX + "/local-auth/unlock",
                API_PREFIX + "/local-auth/password-login"
            ) and not self.server.local_auth.valid(self.headers.get("X-Local-Session")):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Desbloqueie o aplicativo para continuar."})
                return
            action()
        except ValidationError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except ChatGPTAuthError as exc:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
        except CategorizationError as exc:
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except ResearchError as exc:
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except WeeklyError as exc:
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except PluggyError as exc:
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except RelayError as exc:
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except LocalAuthError as exc:
            self._json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
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

    def _check_card_import_overlap(self, data: dict, items: list[dict]) -> None:
        with self.server.store._connect() as connection:
            for item in items:
                if item.get('invoice_month') and connection.execute(
                    "SELECT 1 FROM invoice_coverage WHERE source=? AND scope=? AND invoice_month=? AND provider='pluggy'",
                    (item['source'],item.get('scope','personal'),item['invoice_month'])).fetchone():
                    raise ValidationError('Essa fatura já é atualizada pela Pluggy. Sincronize nas Configurações para evitar duplicação.')
                if connection.execute("""SELECT 1 FROM authoritative_periods
                    WHERE source=? AND scope=? AND ? BETWEEN starts_on AND ends_on LIMIT 1""",
                    (item["source"], item.get("scope", "personal"), item["occurred_on"])).fetchone():
                    raise ValidationError("Esse período já é atualizado pela Pluggy. Sincronize a conexão nas Configurações para evitar duplicar o extrato.")
        if items and data.get("mode") == "card" and data.get("filename", "").lower().endswith(".pdf"):
            first = items[0]
            if self.server.store.has_uncovered_invoice_imports(
                first["source"], first["scope"], first["invoice_month"]
            ):
                raise ValidationError(
                    "Essa fatura já tem lançamentos trazidos por outra integração. "
                    "Importar o PDF agora pode duplicar gastos; confira o histórico antes."
                )

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
        if path == API_PREFIX + "/local-auth/status":
            status_method = getattr(self.server.local_auth, "status", None)
            self._json(HTTPStatus.OK, status_method() if status_method else {
                "touch_id_available": True, "password_configured": False,
                "session_seconds": SESSION_SECONDS})
            return
        if path == API_PREFIX + "/local-auth/session":
            self._json(HTTPStatus.OK, self.server.local_auth.session_info(
                self.headers.get("X-Local-Session")))
            return
        if path == "/auth/callback":
            try:
                self.server.auth.complete(query)
                self._redirect("/?chatgpt=" +
                               ("connected" if self.server.auth.status()["connected"] else "error") +
                               "#configuracoes")
            except ChatGPTAuthError as exc:
                self.server.auth.last_error = str(exc)
                self._redirect("/?chatgpt=error#configuracoes")
            return
        if path in STATIC_FILES:
            file_path, content_type = STATIC_FILES[path]
            body = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == API_PREFIX + "/summary":
            self._json(HTTPStatus.OK, self.server.store.summary(self._month(query), self._scope(query)))
            return
        if path == API_PREFIX + "/purchase-categories":
            self._json(HTTPStatus.OK, self.server.store.purchase_categories(
                self._scope(query), query.get("month", [None])[0], query.get("payment_type", ["all"])[0]))
            return
        if path == API_PREFIX + "/analytics":
            raw_months = query.get("months", ["6"])[0]
            if raw_months != 'all' and not raw_months.isdecimal():
                raise ValidationError("Escolha de 1 a 24 meses para os gráficos.")
            self._json(HTTPStatus.OK, self.server.store.analytics(
                self._month(query), self._scope(query), 'all' if raw_months == 'all' else int(raw_months), query.get("payment_type", ["all"])[0]))
            return
        if path == API_PREFIX + "/data-health":
            self._json(HTTPStatus.OK, data_health(self.server.store, self._month(query), self._scope(query)))
            return
        if path == API_PREFIX + "/budgets":
            self._json(HTTPStatus.OK, budget_overview(self.server.store, self._month(query), self._scope(query)))
            return
        if path == API_PREFIX + "/calendar":
            self._json(HTTPStatus.OK, calendar_overview(self.server.store, self._scope(query),
                                                      reference_month=query.get("month", [None])[0]))
            return
        if path == API_PREFIX + "/plans/reserve":
            self._json(HTTPStatus.OK, reserve_overview(self.server.store, self._scope(query, allow_all=False)))
            return
        if path == API_PREFIX + "/plans/history":
            months = query.get("months", ["6"])[0]
            if months not in ("3", "6"):
                raise ValidationError("Escolha os últimos 3 ou 6 meses encerrados.")
            self._json(HTTPStatus.OK, history_insights(self.server.store, self._scope(query, allow_all=False), int(months)))
            return
        if path == API_PREFIX + "/transactions/review":
            included = query.get("include_excluded", ["false"])[0]
            offset = query.get("offset", ["0"])[0]
            if included not in ("true", "false") or not offset.isdecimal():
                raise ValidationError("Filtro de revisão inválido.")
            self._json(HTTPStatus.OK, review_overview(self.server.store, self._scope(query),
                month=query.get("month", [None])[0], category=query.get("category", [""])[0],
                search=query.get("search", [""])[0], kind=query.get("kind", ["expense"])[0],
                payment_type=query.get("payment_type", ["all"])[0], include_excluded=included == "true",
                merchant=query.get("merchant", [""])[0], offset=int(offset)))
            return
        if path == API_PREFIX + "/investments/overview":
            raw_months = query.get("months", ["6"])[0]
            if not raw_months.isdecimal():
                raise ValidationError("Escolha de 1 a 24 meses para os investimentos.")
            self._json(HTTPStatus.OK, self.server.store.investment_overview(
                self._month(query), int(raw_months)))
            return
        if path == API_PREFIX + "/investments/market-returns":
            self._json(HTTPStatus.OK, self.server.market.overview())
            return
        if path == API_PREFIX + "/investments/cdi":
            raw_months = query.get("months", ["6"])[0]
            if not raw_months.isdecimal():
                raise ValidationError("Escolha 1, 3, 6 ou 12 meses para o CDI.")
            self._json(HTTPStatus.OK, self.server.cdi.overview(int(raw_months)))
            return
        if path == API_PREFIX + "/investments/performance":
            self._json(HTTPStatus.OK, performance_overview(self.server.store, query.get("basis", ["auto"])[0]))
            return
        if path == API_PREFIX + "/investments/manual":
            self._json(HTTPStatus.OK, manual_overview(self.server.store))
            return
        if path == API_PREFIX + '/pluggy/webhooks/status':
            self._json(HTTPStatus.OK,self.server.webhooks.status())
            return
        if path == API_PREFIX + "/investments/pluggy/status":
            with self.server.store._connect() as connection:
                syncs = {row["source"]: row["updated"] for row in connection.execute(
                    "SELECT source,MAX(checked_at) updated FROM authoritative_periods GROUP BY source")}
            self._json(HTTPStatus.OK, {"configured": self.server.pluggy.configured(),
                                       "inter_configured": self.server.pluggy.inter_configured(),
                                       "credentials_saved": self.server.pluggy.config_path.is_file(),
                                       "last_sync": {source: value.replace(" ", "T") + "Z" for source,value in syncs.items()}})
            return
        if path == API_PREFIX + "/weekly-summary":
            context = weekly_context(self.server.store, self._scope(query))
            self._json(HTTPStatus.OK, {"period": {key: value for key, value in context.items() if key != "signature"},
                                       "report": saved_weekly_report(self.server.store, context),
                                       "chatgpt_connected": self.server.auth.status()["connected"]})
            return
        if path == API_PREFIX + "/investments/news":
            use_finances = query.get('use_finances',['true'])[0]
            if use_finances not in ('true','false'):
                raise ValidationError('Escolha se deseja usar os totais pessoais na pesquisa.')
            self._json(HTTPStatus.OK, {**self.server.store.investment_news(use_finances == 'true'),
                                       "chatgpt_connected": self.server.auth.status()["connected"]})
            return
        if path == API_PREFIX + "/transactions":
            self._json(HTTPStatus.OK, {"items": self.server.store.list_month(self._month(query), self._scope(query), query.get("payment_type", ["all"])[0])})
            return
        if path == API_PREFIX + "/invoices":
            self._json(HTTPStatus.OK, {"items": self.server.store.invoice_history(self._scope(query))})
            return
        if path == API_PREFIX + "/categorization/status":
            month = query.get("month", [None])[0]
            payment_type = query.get("payment_type", ["all"])[0]
            _, count = self.server.store.uncategorized_purchases(
                self._scope(query), month, payment_type=payment_type)
            self._json(HTTPStatus.OK, {**self.server.auth.status(),
                                       "uncategorized_count": count,
                                       "pending_by_scope": self.server.store.uncategorized_breakdown(month),
                                       "categories": self.server.store.purchase_category_choices(self._scope(query))})
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
        if path == API_PREFIX + "/local-auth/unlock":
            self._read_json()
            self._json(HTTPStatus.OK, {"token": self.server.local_auth.unlock(),
                                       "session_seconds": SESSION_SECONDS})
            return
        if path == API_PREFIX + "/local-auth/password-login":
            data = self._read_json()
            self._json(HTTPStatus.OK, {"token": self.server.local_auth.unlock_password(data.get("password")),
                                       "session_seconds": SESSION_SECONDS})
            return
        if path == API_PREFIX + "/local-auth/password":
            data = self._read_json()
            self._json(HTTPStatus.OK, self.server.local_auth.configure_password(
                self.headers.get("X-Local-Session"), data.get("password"),
                data.get("current_password"), remove=data.get("remove") is True))
            return
        if path == API_PREFIX + "/local-auth/activity":
            data = self._read_json()
            state = self.server.local_auth.record_activity(self.headers.get("X-Local-Session"), data.get("idle_for_ms", 0))
            self._json(HTTPStatus.OK if state else HTTPStatus.UNAUTHORIZED,
                       state or {"error": "Sessão expirada. Desbloqueie o aplicativo para continuar."})
            return
        if path == API_PREFIX + "/local-auth/lock":
            self._read_json()
            self.server.local_auth.lock(self.headers.get("X-Local-Session"))
            self._json(HTTPStatus.OK, {"locked": True})
            return
        if path == API_PREFIX + "/auth/chatgpt/start":
            data = self._read_json()
            client_id = data.get("client_id")
            if client_id is not None and not isinstance(client_id, str):
                raise ValidationError("Conta ChatGPT inválida.")
            url = self.server.auth.start(self.server.server_port,
                                         new_account=data.get("new_account") is True,
                                         client_id=client_id)
            opened = webbrowser.open(url)
            self._json(HTTPStatus.OK, {"opened": opened, "authorization_url": url})
            return
        if path == API_PREFIX + "/auth/chatgpt/disconnect":
            self.server.auth.disconnect()
            self._json(HTTPStatus.OK, self.server.auth.status())
            return
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
        if path == API_PREFIX + "/investments/imports/preview":
            data = self._read_json(3_000_000)
            entries = parse_xp_statement(data.get("filename"), data.get("content"))
            self._check_card_import_overlap(data, [{**e, "source": "XP"} for e in entries])
            self._json(HTTPStatus.OK, self.server.store.preview_xp_statement(entries))
            return
        if path == API_PREFIX + "/investments/imports":
            data = self._read_json(3_000_000)
            entries = parse_xp_statement(data.get("filename"), data.get("content"))
            self._check_card_import_overlap(data, [{**e, "source": "XP"} for e in entries])
            self._json(HTTPStatus.CREATED, self.server.store.import_xp_statement(entries))
            return
        if path == API_PREFIX + "/investments/positions/preview":
            data = self._read_json(3_000_000)
            snapshot = parse_xp_positions(data.get("filename"), data.get("content_base64"))
            self._json(HTTPStatus.OK, self.server.store.preview_xp_positions(snapshot))
            return
        if path == API_PREFIX + "/investments/positions":
            data = self._read_json(3_000_000)
            snapshot = parse_xp_positions(data.get("filename"), data.get("content_base64"))
            self._json(HTTPStatus.CREATED, self.server.store.import_xp_positions(snapshot))
            self.server.market.wake()
            self.server.cdi.wake()
            return
        if path == API_PREFIX + "/investments/manual/flows":
            self._json(HTTPStatus.CREATED, save_manual_flow(self.server.store, self._read_json()))
            return
        if path == API_PREFIX + "/investments/manual/snapshots":
            self._json(HTTPStatus.CREATED, save_manual_snapshot(self.server.store, self._read_json()))
            self.server.cdi.wake()
            return
        if path == API_PREFIX + "/investments/pluggy/config":
            self.server.pluggy.save_config(self._read_json())
            self.server.pluggy.clear_preview()
            self.server.pluggy_inter.clear_preview()
            self._json(HTTPStatus.OK, {"configured": self.server.pluggy.configured(),
                                       "inter_configured": self.server.pluggy.inter_configured()})
            return
        if path == API_PREFIX + "/investments/pluggy/preview":
            self._json(HTTPStatus.OK, self.server.pluggy.preview())
            return
        if path == API_PREFIX + '/pluggy/webhooks/setup':
            self._read_json()
            self._json(HTTPStatus.OK,self.server.webhooks.generate_setup())
            return
        if path == API_PREFIX + '/pluggy/webhooks/config':
            self.server.webhooks.save_config(self._read_json())
            self._json(HTTPStatus.OK,self.server.webhooks.status())
            return
        if path == API_PREFIX + '/pluggy/webhooks/register':
            self._read_json()
            self._json(HTTPStatus.OK,self.server.webhooks.register())
            return
        if path == API_PREFIX + '/pluggy/webhooks/check':
            data = self._read_json()
            result = self.server.webhooks.check(manual=data.get('manual', False))
            if result['synchronized']:
                self.server.market.wake()
                self.server.cdi.wake()
            self._json(HTTPStatus.OK,result)
            return
        if path == API_PREFIX + "/investments/pluggy/sync-preview":
            self._json(HTTPStatus.OK, self.server.pluggy.preview_sync())
            return
        if path == API_PREFIX + "/investments/pluggy/import":
            self._json(HTTPStatus.CREATED, self.server.pluggy.import_preview(self._read_json().get("preview_token")))
            self.server.market.wake()
            self.server.cdi.wake()
            return
        if path == API_PREFIX + "/accounts/pluggy/inter/preview":
            data = self._read_json()
            self._json(HTTPStatus.OK, self.server.pluggy_inter.preview(full_history=data.get('full_history', False),
                full_card_history=data.get('full_card_history', False)))
            return
        if path == API_PREFIX + "/accounts/pluggy/inter/import":
            self._json(HTTPStatus.CREATED, self.server.pluggy_inter.import_preview(
                self._read_json().get("preview_token")))
            return
        if path == API_PREFIX + "/investments/brapi-token":
            self.server.market.save_token(self._read_json().get("token"))
            self._json(HTTPStatus.OK, {"configured": True})
            return
        if path == API_PREFIX + "/investments/news":
            data = self._read_json()
            use_finances = data.get('use_finances',True)
            if not isinstance(use_finances,bool):
                raise ValidationError('Escolha se deseja usar os totais pessoais na pesquisa.')
            context = self.server.store.investment_news_context(use_finances)
            if context is None:
                raise ValidationError("Importe uma posição detalhada da XP antes da pesquisa.")
            token, model = self.server.auth.research_credentials(data.get('model','gpt-6.1-sol'))
            report = research_assets(context["assets"], token, model)
            if use_finances:
                report['personalization'] = personalize_report(report,context['financial_context'],token,model)
            report.update({'schema_version':2,'uses_finances':use_finances,'financial_context':context['financial_context']})
            generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.server.store.save_investment_news(context, report, generated_at, model)
            self._json(HTTPStatus.OK, {**self.server.store.investment_news(use_finances),
                                       "chatgpt_connected": True})
            return
        if path == API_PREFIX + "/weekly-summary":
            data = self._read_json()
            scope = validate_scope(data.get("scope", "personal"), allow_all=True)
            context = weekly_context(self.server.store, scope)
            if not context["current_total_cents"]:
                raise ValidationError("Ainda não há gastos registrados na última semana concluída.")
            token, model = self.server.auth.luna_credentials()
            report = generate_weekly_report(context, token, model)
            save_weekly_report(self.server.store, context, report)
            self._json(HTTPStatus.OK, {"period": {key: value for key, value in context.items() if key != "signature"},
                                       "report": saved_weekly_report(self.server.store, context),
                                       "chatgpt_connected": True})
            return
        if path == API_PREFIX + "/budgets":
            self._json(HTTPStatus.CREATED, save_budget(self.server.store, self._read_json()))
            return
        if path == API_PREFIX + "/plans/reserve/preview":
            self._json(HTTPStatus.OK, preview_reserve(self.server.store, self._read_json()))
            return
        if path == API_PREFIX + "/plans/history/preview":
            self._json(HTTPStatus.OK, preview_history(self.server.store, self._read_json()))
            return
        if path == API_PREFIX + "/plans/history/review":
            self._json(HTTPStatus.OK, save_history_review(self.server.store, self._read_json()))
            return
        if path == API_PREFIX + "/transactions/review":
            self._json(HTTPStatus.OK, apply_review(self.server.store, self._read_json()))
            return
        if path == API_PREFIX + "/plans/reserve":
            self._json(HTTPStatus.OK, save_reserve(self.server.store, self._read_json()))
            return
        if path == API_PREFIX + "/calendar":
            self._json(HTTPStatus.CREATED, save_commitment(self.server.store, self._read_json()))
            return
        if path == API_PREFIX + "/calendar/cards":
            self._json(HTTPStatus.CREATED, save_card_due_rule(self.server.store, self._read_json()))
            return
        if path == API_PREFIX + "/imports/preview":
            data = self._read_json(8_000_000)
            items = prepare_import(data)
            self._check_card_import_overlap(data, items)
            self._json(HTTPStatus.OK, {"count": len(items), "sample": items[:5]})
            return
        if path == API_PREFIX + "/imports":
            data = self._read_json(8_000_000)
            items = prepare_import(data)
            self._check_card_import_overlap(data, items)
            is_account_csv = data.get("mode") == "account" and data.get("filename", "").lower().endswith(".csv")
            balance = account_balance_from_csv(data.get("content")) if is_account_csv else None
            period = account_period_from_csv(data.get("content")) if is_account_csv else None
            daily_balances = account_daily_balances_from_csv(data.get("content")) if is_account_csv else []
            result = self.server.store.add_imported(items)
            if is_account_csv:
                self.server.store.save_account_statement(items[0]["source"], items[0]["scope"],
                                                         period, daily_balances, balance)
                result["matched_transfers"] = self.server.store.reconcile_internal_transfers()
            elif data.get("mode") == "card" and data.get("filename", "").lower().endswith(".pdf"):
                self.server.store.save_invoice_coverage(items[0]["source"], items[0]["scope"],
                                                        items[0]["invoice_month"])
            self._json(HTTPStatus.CREATED, result)
            return
        if path == API_PREFIX + "/categorization/suggest":
            data = self._read_json()
            rows, count = self.server.store.uncategorized_purchases(
                data.get("scope", "personal"), data.get("month"),
                payment_type=data.get("payment_type", "all"))
            engine = data.get('engine', 'hybrid')
            if engine not in ('local', 'hybrid'):
                raise ValidationError('Escolha aprendizado local ou local com ChatGPT.')
            learned = self.server.store.local_category_suggestions(rows)
            unknown = [row for row in rows if row["id"] not in learned]
            token, model = self.server.auth.inference_credentials() if unknown and engine == 'hybrid' else ("", None)
            suggestions = suggest_categories(unknown, token, model) if unknown and engine == 'hybrid' else []
            by_id = {item["id"]: item["category"] for item in suggestions}
            self._json(HTTPStatus.OK, {"items": [
                {**row, "suggested_category": learned[row['id']]['category'] if row['id'] in learned else by_id.get(row['id']),
                 "suggestion_source": learned[row['id']]['source'] if row['id'] in learned else 'model' if row['id'] in by_id else 'unresolved',
                 "suggestion_reason": learned[row['id']]['reason'] if row['id'] in learned else
                    'Confira a sugestão do ChatGPT.' if row['id'] in by_id else 'Sem exemplos próximos suficientes. Escolha uma categoria para ensinar.'}
                for row in rows],
                                       "remaining_count": count - len(rows), "model": model})
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
        if path.startswith(API_PREFIX + "/calendar/"):
            raw_id = path.removeprefix(API_PREFIX + "/calendar/")
            if not raw_id.isdecimal():
                raise ValidationError("Compromisso inválido.")
            self._json(HTTPStatus.OK, save_commitment(self.server.store, self._read_json(), commitment_id=int(raw_id)))
            return
        if path.startswith(API_PREFIX + "/investments/flows/"):
            import_key = path.removeprefix(API_PREFIX + "/investments/flows/")
            classify_flow(self.server.store, import_key, self._read_json().get("classification"))
            self._json(HTTPStatus.OK, {"classification_saved": True})
            return
        if path.startswith(API_PREFIX + "/investments/manual/flows/"):
            raw_id = path.removeprefix(API_PREFIX + "/investments/manual/flows/")
            if not raw_id.isdecimal():
                raise ValidationError("Aporte ou resgate inválido.")
            self._json(HTTPStatus.OK, save_manual_flow(self.server.store, self._read_json(), int(raw_id)))
            return
        transaction_id = self._transaction_id(path)
        if transaction_id is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Rota não encontrada."})
            return
        self._json(HTTPStatus.OK, self.server.store.update(transaction_id, self._read_json()))

    def _delete(self) -> None:
        if not self._require_same_origin():
            return
        path, query = self._route()
        if path == API_PREFIX + '/pluggy/webhooks/config':
            self.server.webhooks.delete_config()
            self._json(HTTPStatus.OK,{'configured':False})
            return
        if path == API_PREFIX + "/budgets":
            delete_budget(self.server.store, self._scope(query, allow_all=False), query.get("category", [""])[0])
            self._json(HTTPStatus.OK, {"deleted": True})
            return
        if path == API_PREFIX + "/plans/reserve":
            delete_reserve(self.server.store, self._scope(query, allow_all=False))
            self._json(HTTPStatus.OK, {"deleted": True})
            return
        if path.startswith(API_PREFIX + "/calendar/"):
            raw_id = path.removeprefix(API_PREFIX + "/calendar/")
            if raw_id.startswith("cards/"):
                raw_rule_id = raw_id.removeprefix("cards/")
                if not raw_rule_id.isdecimal():
                    raise ValidationError("Vencimento de cartão inválido.")
                delete_card_due_rule(self.server.store, int(raw_rule_id))
                self._json(HTTPStatus.OK, {"deleted": True})
                return
            if not raw_id.isdecimal():
                raise ValidationError("Compromisso inválido.")
            delete_commitment(self.server.store, int(raw_id))
            self._json(HTTPStatus.OK, {"deleted": True})
            return
        if path == API_PREFIX + "/investments/brapi-token":
            self.server.market.delete_token()
            self._json(HTTPStatus.OK, {"configured": False})
            return
        if path == API_PREFIX + "/investments/pluggy/config":
            self.server.pluggy.delete_config()
            self.server.pluggy.clear_preview()
            self.server.pluggy_inter.clear_preview()
            self._json(HTTPStatus.OK, {"configured": False, "inter_configured": False})
            return
        if path.startswith(API_PREFIX + "/investments/manual/flows/"):
            raw_id = path.removeprefix(API_PREFIX + "/investments/manual/flows/")
            if not raw_id.isdecimal():
                raise ValidationError("Aporte ou resgate inválido.")
            delete_manual_flow(self.server.store, int(raw_id))
            self._json(HTTPStatus.OK, {"deleted": True})
            return
        if path.startswith(API_PREFIX + "/investments/manual/snapshots/"):
            as_of = path.removeprefix(API_PREFIX + "/investments/manual/snapshots/")
            delete_manual_snapshot(self.server.store, as_of)
            self._json(HTTPStatus.OK, {"deleted": True})
            return
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
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", "0")
        self.end_headers()


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
