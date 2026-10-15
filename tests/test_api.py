import base64
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import FinanceHandler
from cdi import CDIBenchmark
from chatgpt_auth import ChatGPTAuth
from finance import FinanceStore
from pluggy_xp import PluggyXP
from pluggy_inter import PluggyInter
from webhook_client import WebhookClient


XP_CSV = ("Data;Hora;Descricao;Valor;Saldo\n"
          "09/10/26;10:19:25;Pix recebido de Ana Exemplo Silva;R$ 12,34;R$ 12,34\n")


class OutputBuffer(io.BytesIO):
    def close(self):
        pass


class FakeSocket:
    def __init__(self, request: bytes):
        self.input = io.BytesIO(request)
        self.output = OutputBuffer()

    def makefile(self, mode, buffering=None):
        return self.input if "r" in mode else self.output

    def sendall(self, data):
        self.output.write(data)


class FakeLocalAuth:
    def __init__(self):
        self.token = "test-session"

    def valid(self, token):
        return token == self.token and self.token is not None

    def unlock(self):
        self.token = "test-session"
        return self.token

    def lock(self, token):
        if token == self.token:
            self.token = None


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.directory.name) / "test.db")
        self.server = SimpleNamespace(store=self.store, server_port=8765)
        self.server.auth = ChatGPTAuth(Path(self.directory.name) / "chatgpt-auth.json")
        self.server.cdi = CDIBenchmark(self.store)
        self.server.pluggy = PluggyXP(self.store, Path(self.directory.name) / "pluggy-xp.json")
        self.server.pluggy_inter = PluggyInter(self.store, self.server.pluggy)
        self.server.webhooks = WebhookClient(self.store,self.server.pluggy,self.server.pluggy_inter,Path(self.directory.name)/'relay.json')
        self.server.local_auth = FakeLocalAuth()

    def tearDown(self):
        self.directory.cleanup()

    def call_api(self, path, method="GET", data=None, headers=None, authenticated=True):
        body = json.dumps(data).encode("utf-8") if data is not None else b""
        header_lines = ["Connection: close"]
        if authenticated:
            header_lines.append("X-Local-Session: test-session")
        if not headers or "Host" not in headers:
            header_lines.insert(0, "Host: 127.0.0.1:8765")
        if data is not None:
            header_lines.extend(["Content-Type: application/json", f"Content-Length: {len(body)}"])
        header_lines.extend(f"{key}: {value}" for key, value in (headers or {}).items())
        raw = (f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(header_lines) + "\r\n\r\n").encode() + body
        socket = FakeSocket(raw)
        FinanceHandler(socket, ("127.0.0.1", 50000), self.server)
        head, response_body = socket.output.getvalue().split(b"\r\n\r\n", 1)
        status = int(head.split(b" ", 2)[1])
        content = json.loads(response_body) if b"application/json" in head else response_body
        return status, content

    def test_touch_id_gate_covers_reads_and_writes(self):
        self.assertEqual(self.call_api('/api/v1/transactions/review',authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/transactions/review','POST',{},authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/calendar/1','PUT',{},authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/plans/history',authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/plans/history/preview','POST',{},authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/plans/history/review','POST',{},authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/plans/reserve',authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/plans/reserve','POST',{},authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/plans/reserve/preview','POST',{},authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/plans/reserve','DELETE',authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/pluggy/webhooks/status',authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/pluggy/webhooks/check','POST',{},authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/pluggy/webhooks/check','POST',{'manual':True},authenticated=False)[0],401)
        self.assertEqual(self.call_api('/api/v1/pluggy/webhooks/setup','POST',{},authenticated=False)[0],401)
        self.assertEqual(self.call_api("/api/v1/summary?month=2026-10", authenticated=False)[0], 401)
        self.assertEqual(self.call_api("/api/v1/investments/overview", authenticated=False)[0], 401)
        self.assertEqual(self.call_api("/api/v1/transactions", "POST", {
            "kind": "expense", "description": "Teste", "amount": "1,00",
            "occurred_on": "2026-10-13", "category": "Outros",
        }, authenticated=False)[0], 401)
        status, auth_status = self.call_api("/api/v1/local-auth/status", authenticated=False)
        self.assertEqual((status, auth_status["session_seconds"]), (200, 300))
        self.assertEqual(self.call_api("/api/v1/local-auth/unlock", "POST", {},
                                       headers={"Origin": "https://other.example"},
                                       authenticated=False)[0], 403)
        self.assertEqual(self.call_api("/api/v1/local-auth/unlock", "POST", {},
                                       authenticated=False)[1]["token"], "test-session")
        self.assertEqual(self.call_api("/api/v1/summary?month=2026-10")[0], 200)
        self.assertEqual(self.call_api("/api/v1/local-auth/lock", "POST", {})[0], 200)
        self.assertEqual(self.call_api("/api/v1/summary?month=2026-10")[0], 401)

    def test_webhook_check_forwards_manual_choice_and_validates_boolean(self):
        with patch.object(self.server.webhooks,'check',return_value={'synchronized':[],'processed':0}) as check:
            self.assertEqual(self.call_api('/api/v1/pluggy/webhooks/check','POST',{'manual':True})[0],200)
            check.assert_called_once_with(manual=True)
        self.assertEqual(self.call_api('/api/v1/pluggy/webhooks/check','POST',{'manual':'false'})[0],400)

    def test_privacy_assets_are_public_but_financial_data_stays_protected(self):
        for path in ('/privacy.js','/dashboard.css'):
            status, content = self.call_api(path, authenticated=False)
            self.assertEqual(status,200)
            self.assertTrue(content)
        self.assertEqual(self.call_api('/api/v1/summary?month=2026-01',authenticated=False)[0],401)

    def test_crud_and_summary(self):
        data = {
            "kind": "expense", "description": "Internet", "amount": "89,90",
            "category": "Casa", "occurred_on": "2026-10-09",
        }
        status, created = self.call_api("/api/v1/transactions", "POST", data)
        self.assertEqual(status, 201)
        item_id = created["id"]
        status, listing = self.call_api("/api/v1/transactions?month=2026-10")
        self.assertEqual(status, 200)
        self.assertEqual(listing["items"][0]["id"], item_id)
        status, summary = self.call_api("/api/v1/summary?month=2026-10")
        self.assertEqual(status, 200)
        self.assertEqual(summary["expense_cents"], 8990)
        status, updated = self.call_api(f"/api/v1/transactions/{item_id}", "PUT", {**data, "amount": "100,00"})
        self.assertEqual((status, updated["amount_cents"]), (200, 10000))
        status, result = self.call_api(f"/api/v1/transactions/{item_id}", "DELETE")
        self.assertEqual((status, result), (200, {"deleted": True}))
        status, _ = self.call_api(f"/api/v1/transactions/{item_id}")
        self.assertEqual(status, 404)

    def test_manual_investment_endpoints(self):
        status, flow = self.call_api("/api/v1/investments/manual/flows", "POST",
                                     {"occurred_on": "2024-01-05", "kind": "deposit", "amount": "150,00"})
        self.assertEqual((status, flow["amount_cents"]), (201, 15000))
        status, updated = self.call_api(f"/api/v1/investments/manual/flows/{flow['id']}", "PUT",
                                         {"occurred_on": "2024-01-06", "kind": "withdrawal", "amount": "25,00"})
        self.assertEqual((status, updated["kind"]), (200, "withdrawal"))
        self.assertEqual(self.call_api("/api/v1/investments/manual/snapshots", "POST",
                                       {"as_of": "2024-01-06", "total": "1.000,00"})[0], 201)
        status, overview = self.call_api("/api/v1/investments/manual")
        self.assertEqual((status, len(overview["flows"]), len(overview["snapshots"])), (200, 1, 1))
        self.assertEqual(self.call_api(f"/api/v1/investments/manual/flows/{flow['id']}", "DELETE")[0], 200)
        self.assertEqual(self.call_api("/api/v1/investments/manual/snapshots/2024-01-06", "DELETE")[0], 200)
        self.assertEqual(self.call_api("/api/v1/investments/manual")[1]["flows"], [])

    def test_pluggy_connection_endpoints_do_not_return_secret(self):
        config = {"client_id": "client", "client_secret": "secret",
                  "item_id": "12345678-1234-1234-1234-123456789abc"}
        self.assertEqual(self.call_api("/api/v1/investments/pluggy/status")[1],
                         {"configured": False, "inter_configured": False, "credentials_saved": False, "last_sync": {}})
        status, result = self.call_api("/api/v1/investments/pluggy/config", "POST", config)
        self.assertEqual((status, result), (200, {"configured": True, "inter_configured": False}))
        self.assertNotIn("secret", json.dumps(self.call_api("/api/v1/investments/pluggy/status")[1]))
        with patch.object(self.server.pluggy, "preview", return_value={"assets": [], "gross_cents": 0}):
            self.assertEqual(self.call_api("/api/v1/investments/pluggy/preview", "POST", {})[0], 200)
        self.assertEqual(self.call_api("/api/v1/investments/pluggy/config", "DELETE")[1],
                         {"configured": False, "inter_configured": False})

    def test_bad_input_and_cross_origin_write_are_rejected(self):
        status, _ = self.call_api("/api/v1/summary?month=wrong")
        self.assertEqual(status, 400)
        status, _ = self.call_api("/api/v1/transactions", "POST", {"description": "Incomplete"})
        self.assertEqual(status, 400)
        status, _ = self.call_api(
            "/api/v1/transactions", "POST", {"anything": True},
            {"Origin": "https://other.example"},
        )
        self.assertEqual(status, 403)
        status, _ = self.call_api("/api/v1/summary?month=2026-10", headers={"Host": "other.example"})
        self.assertEqual(status, 403)

    def test_cdi_and_weekly_read_endpoints(self):
        self.store.save_market_quotes([{"source": "bcb", "identifier": "12", "quote_date": "2026-10-09",
                                        "price": "0.05", "checked_at": "2026-10-09T12:00:00+00:00"}])
        status, cdi = self.call_api("/api/v1/investments/cdi?months=12")
        self.assertEqual(status, 200)
        self.assertEqual(cdi["source_url"].startswith("https://www3.bcb.gov.br/"), True)
        self.assertEqual(self.call_api("/api/v1/investments/cdi?months=2")[0], 400)
        status, weekly = self.call_api("/api/v1/weekly-summary?scope=personal")
        self.assertEqual(status, 200)
        self.assertIn("categories", weekly["period"])
        self.assertEqual(self.call_api("/api/v1/weekly-summary?scope=invalid")[0], 400)

    def test_weekly_generation_is_on_demand_and_cached(self):
        self.store.add({"kind": "expense", "description": "Exemplo", "amount": "10,00",
                        "category": "Mercado", "occurred_on": "2026-10-06"})
        status, initial = self.call_api("/api/v1/weekly-summary")
        self.assertEqual(status, 200)
        self.assertIsNone(initial["report"])
        with patch.object(self.server.auth, "luna_credentials", return_value=("test-token", "gpt-6-luna")), \
             patch("app.generate_weekly_report", return_value={"headline": "Mercado liderou",
                  "insights": ["Mercado concentrou os gastos.", "Revise a próxima semana."]}) as generate:
            status, response = self.call_api("/api/v1/weekly-summary", "POST", {"scope": "personal"})
        self.assertEqual(status, 200)
        self.assertEqual(response["report"]["headline"], "Mercado liderou")
        generate.assert_called_once()
        self.assertIsNotNone(self.call_api("/api/v1/weekly-summary")[1]["report"])

    def test_plan_history_api_previews_reviews_and_rejects_stale_or_invalid_scope(self):
        status, initial = self.call_api('/api/v1/plans/history?months=3')
        self.assertEqual(status, 200)
        self.assertIsNone(initial['average_spending_cents'])
        self.store.add({'kind':'expense','description':'Mercado exemplo','category':'Mercado',
                        'amount':'20,00','occurred_on':initial['window'][-1]+'-10'})
        analysis = self.call_api('/api/v1/plans/history?months=3')[1]
        draft = {'scope':'personal','months':3,'signature':analysis['signature'],
                 'roles':{'Mercado':'essential'},'monthly_income':'500,00','buffer_percent':10,
                 'reserved_balance':'0','target_months':3,'acknowledged':True}
        self.assertEqual(self.call_api('/api/v1/plans/history/preview','POST',draft)[1]['essential_cents'],2000)
        self.assertIsNone(self.call_api('/api/v1/plans/history?months=3')[1]['preferences']['income_cents'])
        self.assertEqual(self.call_api('/api/v1/plans/history/review','POST',draft,
                                      headers={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.call_api('/api/v1/plans/history/review','POST',draft)[0],200)
        self.assertEqual(self.call_api('/api/v1/plans/history?months=3')[1]['preferences']['income_cents'],50000)
        self.assertFalse(self.call_api('/api/v1/plans/reserve')[1]['configured'])
        self.assertEqual(self.call_api('/api/v1/plans/history/review','POST',draft)[0],400)
        self.assertEqual(self.call_api('/api/v1/plans/history?scope=all')[0],400)
        self.assertEqual(self.call_api('/api/v1/plans/history?months=5')[0],400)

    def test_manual_classification_and_expected_income_routes(self):
        first=self.store.add({'kind':'expense','description':'Loja exemplo','category':'Outros','amount':'20,00','occurred_on':'2026-09-10'})
        status,review=self.call_api('/api/v1/transactions/review?category=Outros')
        self.assertEqual((status,review['total']),(200,1))
        correction={'scope':'personal','items':[{'id':first['id'],'category':'Compras','signature':review['items'][0]['signature']}]}
        self.assertEqual(self.call_api('/api/v1/transactions/review','POST',correction,headers={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.call_api('/api/v1/transactions/review','POST',correction)[1]['applied'],1)
        self.assertEqual(self.call_api('/api/v1/transactions/review','POST',correction)[0],400)
        expected={'scope':'personal','kind':'income','description':'Receita esperada','amount':'1000,00','due_day':15,'starts_month':'2026-10','ends_month':'2026-12'}
        created=self.call_api('/api/v1/calendar','POST',expected)[1]
        self.assertEqual(self.call_api('/api/v1/calendar?scope=personal&month=2026-11')[1]['expected_income_cents'],100000)
        self.assertEqual(self.call_api('/api/v1/calendar/'+str(created['id']),'PUT',{**expected,'amount':'1500,00'})[0],200)
        self.assertEqual(self.call_api('/api/v1/calendar?scope=personal&month=2027-01')[1]['expected_income_cents'],0)

    def test_planning_and_performance_routes(self):
        today = date.today().isoformat()
        draft = {'scope':'personal','name':'Reserva exemplo','essential_expenses':'1500,00',
                 'target_months':6,'monthly_contribution':'500,00','reserved_balance':'0,00',
                 'balance_as_of':today,'next_contribution_on':today}
        self.assertFalse(self.call_api('/api/v1/plans/reserve')[1]['configured'])
        self.assertEqual(self.call_api('/api/v1/plans/reserve/preview','POST',draft)[0],200)
        self.assertFalse(self.call_api('/api/v1/plans/reserve')[1]['configured'])
        self.assertEqual(self.call_api('/api/v1/plans/reserve','POST',draft,
                                       headers={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.call_api('/api/v1/plans/reserve','POST',draft)[1]['projection']['target_cents'],900000)
        self.assertTrue(self.call_api('/api/v1/plans/reserve')[1]['configured'])
        self.assertFalse(self.call_api('/api/v1/plans/reserve?scope=business')[1]['configured'])
        self.assertEqual(self.call_api('/api/v1/plans/reserve?scope=all')[0],400)
        self.assertEqual(self.call_api('/api/v1/plans/reserve','DELETE')[0],200)
        self.assertFalse(self.call_api('/api/v1/plans/reserve')[1]['configured'])
        status, health = self.call_api("/api/v1/data-health?month=2026-10")
        self.assertEqual(status, 200)
        self.assertTrue(health["has_gaps"])
        status, budget = self.call_api("/api/v1/budgets", "POST", {
            "scope": "personal", "category": "Mercado", "limit": "200,00"})
        self.assertEqual((status, budget["limit_cents"]), (201, 20000))
        self.assertEqual(self.call_api("/api/v1/budgets?month=2026-10")[1]["budgets"][0]["category"], "Mercado")
        status, planned = self.call_api("/api/v1/calendar", "POST", {
            "scope": "personal", "kind": "expense", "description": "Conta exemplo",
            "amount": "30,00", "due_day": 20, "starts_month": "2026-10"})
        self.assertEqual(status, 201)
        self.assertEqual(self.call_api("/api/v1/calendar")[0], 200)
        self.assertEqual(self.call_api(f"/api/v1/calendar/{planned['id']}", "DELETE")[0], 200)
        self.assertEqual(self.call_api("/api/v1/budgets?scope=personal&category=Mercado", "DELETE")[0], 200)
        status, performance = self.call_api("/api/v1/investments/performance")
        self.assertEqual((status, performance["status"]), (200, "needs_snapshots"))
        self.assertEqual(self.call_api("/api/v1/investments/performance?basis=pluggy")[1]['basis'], 'pluggy')
        self.assertEqual(self.call_api("/api/v1/investments/performance?basis=invalid")[0], 400)

    def test_xp_import_preview_and_commit_keep_cash_flow_separate(self):
        payload = {"filename": "xp.csv", "content": XP_CSV}
        status, preview = self.call_api("/api/v1/investments/imports/preview", "POST", payload)
        self.assertEqual((status, preview["count"], preview["matches"]), (200, 1, []))
        status, result = self.call_api("/api/v1/investments/imports", "POST", payload)
        self.assertEqual((status, result["imported"]), (201, 1))
        status, result = self.call_api("/api/v1/investments/imports", "POST", payload)
        self.assertEqual((status, result["skipped"]), (201, 1))
        self.assertEqual(self.store.summary("2026-10")["income_cents"], 0)
        status, overview = self.call_api("/api/v1/investments/overview?month=2026-10&months=3")
        self.assertEqual((status, overview["total_deposited_cents"]), (200, 1234))
        self.assertEqual(self.call_api("/api/v1/investments/overview?months=25")[0], 400)

    def test_xp_csv_cannot_double_a_pluggy_period(self):
        with self.store._connect() as connection:
            connection.execute("INSERT INTO authoritative_periods(source,scope,starts_on,ends_on) VALUES ('XP','personal','2026-10-01','2026-10-13')")
        payload = {"filename":"xp.csv","content":XP_CSV}
        self.assertEqual(self.call_api("/api/v1/investments/imports/preview","POST",payload)[0],400)
        self.assertEqual(self.call_api("/api/v1/investments/imports","POST",payload)[0],400)
        with self.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM investment_entries").fetchone()[0],0)

    def test_investment_news_requires_position_then_caches_manual_research(self):
        self.assertEqual(self.call_api("/api/v1/investments/news", "POST", {})[0], 400)
        self.store.import_xp_positions({
            "as_of": "2026-10-13", "total_cents": 1100, "assets_cents": 1100,
            "receivables_cents": 0, "cash_cents": 0,
            "assets": [{"name": "ABCD11", "category": "Ações", "value_cents": 1100,
                        "cost_cents": 1000, "quantity": "1", "net_cents": None}],
        })
        status, initial = self.call_api("/api/v1/investments/news")
        self.assertEqual(status, 200)
        self.assertIsNone(initial["report"])
        self.assertEqual(initial["position"]["assets"], ["ABCD11"])
        research = {"assets": [{"asset": "ABCD11", "headline": "Resultado",
                                "source_url": "https://www.b3.com.br/fato",
                                "summary": "Resumo", "published_on": "2026-10-13",
                                "short_term": "Cenário", "medium_term": "Cenário",
                                "long_term": "Cenário", "risk": "Risco"}],
                    "opportunities": []}
        with patch.object(self.server.auth, "luna_credentials", return_value=("fake-token", "gpt-6-luna")), \
                patch("app.research_assets", return_value=research) as researcher:
            status, refreshed = self.call_api("/api/v1/investments/news", "POST", {})
        self.assertEqual(status, 200)
        researcher.assert_called_once_with(["ABCD11"], "fake-token", "gpt-6-luna")
        self.assertEqual(refreshed["report"]["assets"][0]["headline"], "Resultado")
        self.assertEqual(self.call_api("/api/v1/investments/news")[1]["report"]["model"], "gpt-6-luna")

    def test_manual_card_purchase_appears_in_invoice_without_cash_expense(self):
        data = {"bank": "inter", "account": "principal", "invoice_month": "2026-10",
                "occurred_on": "2026-10-09", "description": "Mercado", "amount": "23,50",
                "category": "Mercado"}
        status, created = self.call_api("/api/v1/card-purchases", "POST", data)
        self.assertEqual(status, 201)
        self.assertEqual(created["invoice_month"], "2026-10")
        status, summary = self.call_api("/api/v1/summary?month=2026-10")
        self.assertEqual((status, summary["expense_cents"], summary["card_purchases_cents"]),
                         (200, 0, 2350))
        status, invoices = self.call_api("/api/v1/invoices")
        self.assertEqual((status, invoices["items"][0]["expense_cents"]), (200, 2350))

    def test_home_page_is_served(self):
        status, body = self.call_api("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Meu Dinheiro", body)
        self.assertEqual(self.call_api("/.env")[0], 404)

    def test_chatgpt_login_route_starts_local_callback_without_api_key(self):
        with patch("app.webbrowser.open", return_value=True) as browser:
            status, result = self.call_api("/api/v1/auth/chatgpt/start", "POST", {})
        self.assertEqual(status, 200)
        self.assertTrue(result["opened"])
        self.assertIn("redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fauth%2Fcallback",
                      result["authorization_url"])
        browser.assert_called_once_with(result["authorization_url"])
        self.assertFalse(self.call_api("/api/v1/categorization/status")[1]["connected"])
        self.assertEqual(self.call_api("/auth/callback?state=wrong&code=secret")[0], 303)

    def test_import_inspect_preview_commit(self):
        statement = "Data;Descrição;Valor\n09/10/2026;Café;12,50\n"
        status, inspected = self.call_api(
            "/api/v1/imports/inspect", "POST",
            {"filename": "fatura.csv", "content": statement},
        )
        self.assertEqual(status, 200)
        payload = {
            "filename": "fatura.csv", "content": statement, "bank": "inter",
            "mode": "card", "account": "cartão", "mapping": inspected["suggested_mapping"],
        }
        status, preview = self.call_api("/api/v1/imports/preview", "POST", payload)
        self.assertEqual((status, preview["count"]), (200, 1))
        status, committed = self.call_api("/api/v1/imports", "POST", payload)
        self.assertEqual((status, committed["imported"]), (201, 1))
        status, repeated = self.call_api("/api/v1/imports", "POST", payload)
        self.assertEqual((status, repeated["skipped"]), (201, 1))

    def test_pdf_invoice_endpoints(self):
        pdf_path = Path(__file__).parent / "fixtures" / "sample_statement.pdf"
        content_base64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
        status, inspected = self.call_api(
            "/api/v1/imports/inspect", "POST",
            {"filename": "bb-2026-10.pdf", "content_base64": content_base64},
        )
        self.assertEqual((status, inspected["count"]), (200, 2))
        payload = {
            "filename": "bb-2026-10.pdf", "content_base64": content_base64,
            "bank": "bb", "mode": "card", "account": "principal",
            "statement_month": "2026-10",
        }
        status, preview = self.call_api("/api/v1/imports/preview", "POST", payload)
        self.assertEqual((status, preview["count"]), (200, 2))
        status, committed = self.call_api("/api/v1/imports", "POST", payload)
        self.assertEqual((status, committed["imported"]), (201, 2))
        with self.store._connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT invoice_month FROM invoice_coverage WHERE source = ?",
                ("Banco do Brasil · principal",)
            ).fetchone()[0], "2026-10")
        status, history = self.call_api("/api/v1/invoices")
        self.assertEqual(status, 200)
        self.assertEqual(history["items"][0]["expense_cents"], 17345)
        status, details = self.call_api(
            "/api/v1/invoices/2026-10?source=Banco%20do%20Brasil%20%C2%B7%20principal"
        )
        self.assertEqual((status, len(details["items"])), (200, 2))

    def test_pdf_rejects_month_already_imported_from_connector(self):
        self.store.add_imported([{
            "kind": "expense", "description": "Compra conectada", "category": "Importado",
            "amount_cents": 1000, "occurred_on": "2026-10-05", "excluded": False,
            "source": "Banco do Brasil · principal", "invoice_month": "2026-10",
            "import_key": "a" * 64, "scope": "personal",
        }])
        pdf_path = Path(__file__).parent / "fixtures" / "sample_statement.pdf"
        payload = {"filename": "bb-2026-10.pdf",
                   "content_base64": base64.b64encode(pdf_path.read_bytes()).decode("ascii"),
                   "bank": "bb", "mode": "card", "account": "principal",
                   "statement_month": "2026-10"}
        status, response = self.call_api("/api/v1/imports/preview", "POST", payload)
        self.assertEqual(status, 400)
        self.assertIn("duplicar gastos", response["error"])

    def test_scope_query_filters_personal_and_business(self):
        base = {"kind": "expense", "description": "Teste", "amount": "10,00",
                "category": "Outros", "occurred_on": "2026-10-09"}
        self.call_api("/api/v1/transactions", "POST", {**base, "scope": "personal"})
        self.call_api("/api/v1/transactions", "POST", {**base, "scope": "business"})
        for scope, expected in (("personal", 1), ("business", 1), ("all", 2)):
            status, result = self.call_api(f"/api/v1/transactions?month=2026-10&scope={scope}")
            self.assertEqual((status, len(result["items"])), (200, expected))
        status, _ = self.call_api("/api/v1/summary?month=2026-10&scope=invalid")
        self.assertEqual(status, 400)

    def test_account_import_updates_dynamic_chart_data(self):
        statement = ("Período ;09/09/2026 a 09/10/2026\nSaldo ;18,36\n"
                     "Data Lançamento;Histórico;Descrição;Valor;Saldo\n"
                     "09/10/2026;PIX recebido;Cliente;100,00;18,36\n"
                     "08/10/2026;Compra;Mercado;-20,00;-81,64\n")
        status, inspected = self.call_api("/api/v1/imports/inspect", "POST",
                                          {"filename": "extrato.csv", "content": statement})
        self.assertEqual(status, 200)
        payload = {"filename": "extrato.csv", "content": statement, "bank": "inter",
                   "mode": "account", "account": "conta", "scope": "personal",
                   "mapping": inspected["suggested_mapping"]}
        self.assertEqual(self.call_api("/api/v1/imports", "POST", payload)[0], 201)
        status, chart = self.call_api("/api/v1/analytics?month=2026-10&scope=personal&months=2")
        self.assertEqual(status, 200)
        self.assertEqual([row["coverage"] for row in chart["cashflow"]], ["partial", "partial"])
        self.assertEqual(chart["cashflow"][1]["income_cents"], 10000)
        self.assertEqual(len(chart["balance_series"][0]["points"]), 2)
        self.assertEqual(self.call_api("/api/v1/analytics?month=2026-10&months=30")[0], 400)

    def test_purchase_categories_can_show_month_or_full_history(self):
        for occurred_on, category, amount in (("2026-09-09", "Mercado", "10,00"),
                                              ("2026-10-09", "Alimentação", "20,00")):
            self.store.add({"kind": "expense", "description": category, "amount": amount,
                            "category": category, "occurred_on": occurred_on})
        status, month = self.call_api("/api/v1/purchase-categories?scope=personal&month=2026-10")
        self.assertEqual((status, month["purchase_total_cents"]), (200, 2000))
        status, history = self.call_api("/api/v1/purchase-categories?scope=personal")
        self.assertEqual((status, history["purchase_total_cents"]), (200, 3000))
        self.assertEqual(self.call_api("/api/v1/purchase-categories?month=2026-13")[0], 400)

    def test_card_history_preview_flag_and_provider_invoice_overlap_guard(self):
        with patch.object(self.server.pluggy_inter,'preview',return_value={'full_card_history':True}) as preview:
            status,_ = self.call_api('/api/v1/accounts/pluggy/inter/preview','POST',{'full_card_history':True})
        self.assertEqual(status,200)
        preview.assert_called_once_with(full_history=False,full_card_history=True)
        with self.store._connect() as c:
            c.execute("INSERT INTO invoice_coverage(source,scope,invoice_month,provider) VALUES ('Inter · cartão principal','personal','2026-08','pluggy')")
        items=[{'source':'Inter · cartão principal','scope':'personal','invoice_month':'2026-08','occurred_on':'2026-05-10'}]
        handler=object.__new__(FinanceHandler); handler.server=self.server
        from finance import ValidationError
        with self.assertRaisesRegex(ValidationError,'fatura já é atualizada'):
            handler._check_card_import_overlap({'mode':'card'},items)

    def test_local_knn_suggestions_need_no_chatgpt_and_never_modify_until_review(self):
        for amount,category in [('28','Tabaco'),('35','Tabaco'),('150','Transporte'),('180','Transporte')]:
            self.store.add({'kind':'expense','description':'Autoposto exemplo','amount':amount,
                'category':category,'occurred_on':'2026-10-10'})
        row=self.store.add({'kind':'expense','description':'Autoposto exemplo','amount':'32',
            'category':'Importado','occurred_on':'2026-10-10'})
        with patch.object(self.server.auth,'inference_credentials') as credentials, patch('app.suggest_categories') as model:
            status,data=self.call_api('/api/v1/categorization/suggest','POST',{'engine':'local'})
        self.assertEqual(status,200)
        credentials.assert_not_called(); model.assert_not_called()
        self.assertEqual(data['items'][0]['suggested_category'],'Tabaco')
        self.assertEqual(data['items'][0]['suggestion_source'],'knn')
        self.assertEqual(self.store.get(row['id'])['category'],'Importado')
        self.assertEqual(self.call_api('/api/v1/categorization/suggest','POST',{'engine':'unknown'})[0],400)

    def test_categorization_suggest_and_review_apply(self):
        self.call_api("/api/v1/transactions", "POST", {
            "kind": "expense", "description": "Mercado", "amount": "25,00",
            "category": "Importado", "occurred_on": "2026-10-09",
        })
        self.server.auth.data["accounts"]["oaiapp_test"] = {
            "access_token": "test-token", "scopes": ["chatgpt.tokens.use.direct"],
            "email": "teste@exemplo.com", "model": "gpt-6-luna",
        }
        self.server.auth.data["active"] = "oaiapp_test"
        status, info = self.call_api("/api/v1/categorization/status?month=2026-10")
        self.assertEqual((status, info["uncategorized_count"], info["connected"]), (200, 1, True))
        with patch.object(self.server.auth, "inference_credentials", return_value=("test-token", "gpt-6-luna")):
            with patch("app.suggest_categories", return_value=[{"id": 1, "category": "Mercado"}]) as suggest:
                status, suggestions = self.call_api("/api/v1/categorization/suggest", "POST",
                                                     {"scope": "personal", "month": "2026-10"})
                self.assertEqual(suggest.call_args.args[1:], ("test-token", "gpt-6-luna"))
        self.assertEqual((status, suggestions["items"][0]["suggested_category"]), (200, "Mercado"))
        status, saved = self.call_api("/api/v1/categorization/apply", "POST", {
            "scope": "personal", "items": [{"id": 1, "category": "Alimentação"}],
        })
        self.assertEqual((status, saved["applied"]), (200, 1))
        self.assertEqual(self.store.summary("2026-10")["purchase_categories"],
                         [{"category": "Alimentação", "amount_cents": 2500}])
        self.assertEqual(self.call_api("/api/v1/categorization/status?month=2026-10")[1]["uncategorized_count"], 0)

    def test_categorization_type_filters_status_and_suggestions(self):
        account = self.store.add({"kind": "expense", "description": "Débito", "amount": "25,00",
                                  "category": "Importado", "occurred_on": "2026-10-09"})
        self.store.add_card_purchase({"bank": "inter", "account": "principal", "scope": "personal",
                                      "invoice_month": "2026-11", "occurred_on": "2026-10-09",
                                      "description": "Crédito", "amount": "50,00", "category": "Importado"})
        self.store.add({"kind": "expense", "description": "PJ", "amount": "10,00",
                        "category": "Importado", "occurred_on": "2026-10-09", "scope": "business"})
        status, info = self.call_api(
            "/api/v1/categorization/status?scope=personal&month=2026-10&payment_type=account")
        self.assertEqual((status, info["uncategorized_count"]), (200, 1))
        self.assertEqual(info["pending_by_scope"]["business"]["account"], 1)
        with patch.object(self.server.auth, "inference_credentials", return_value=("test-token", "gpt-6-luna")):
            with patch("app.suggest_categories", return_value=[{"id": account["id"], "category": "Outros"}]):
                status, result = self.call_api("/api/v1/categorization/suggest", "POST", {
                    "scope": "personal", "month": "2026-10", "payment_type": "account"})
        self.assertEqual((status, [item["id"] for item in result["items"]]), (200, [account["id"]]))
        self.assertEqual(self.call_api(
            "/api/v1/categorization/status?payment_type=invalid")[0], 400)

    def test_categorization_never_uses_old_api_key_without_chatgpt_login(self):
        self.call_api("/api/v1/transactions", "POST", {
            "kind": "expense", "description": "Mercado", "amount": "25,00",
            "category": "Importado", "occurred_on": "2026-10-09",
        })
        with patch.dict(os.environ, {"OPENAI_API_KEY": "old-test-key"}):
            with patch("app.suggest_categories") as suggest:
                status, result = self.call_api("/api/v1/categorization/suggest", "POST", {})
        self.assertEqual(status, 503)
        self.assertIn("Conecte o ChatGPT", result["error"])
        suggest.assert_not_called()

    def test_categorization_reuses_approved_choice_and_sends_only_unknown_to_model(self):
        approved = self.store.add({"kind": "expense", "description": "RESTAURANTE EXEMPLO",
                                   "amount": "20,00", "category": "Importado", "occurred_on": "2026-10-09"})
        self.store.apply_purchase_categories([{"id": approved["id"], "category": "Alimentação"}])
        remembered = self.store.add({"kind": "expense", "description": "restaurante-exemplo",
                                     "amount": "21,00", "category": "Importado", "occurred_on": "2026-10-10"})
        unknown = self.store.add({"kind": "expense", "description": "Outra Loja",
                                  "amount": "22,00", "category": "Importado", "occurred_on": "2026-10-11"})
        with patch.object(self.server.auth, "inference_credentials", return_value=("test-token", "gpt-6-luna")):
            with patch("app.suggest_categories", return_value=[{"id": unknown["id"],
                                                                   "category": "Compras"}]) as suggest:
                status, result = self.call_api("/api/v1/categorization/suggest", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in suggest.call_args.args[0]], [unknown["id"]])
        by_id = {item["id"]: item for item in result["items"]}
        self.assertEqual((by_id[remembered["id"]]["suggested_category"],
                          by_id[remembered["id"]]["suggestion_source"]), ("Alimentação", "learned"))
        self.assertEqual((by_id[unknown["id"]]["suggested_category"],
                          by_id[unknown["id"]]["suggestion_source"]), ("Compras", "model"))

    def test_fully_learned_batch_uses_no_chatgpt_call(self):
        first = self.store.add({"kind": "expense", "description": "Livraria Exemplo",
                                "amount": "20,00", "category": "Importado", "occurred_on": "2026-10-09"})
        self.store.apply_purchase_categories([{"id": first["id"], "category": "Educação"}])
        self.store.add({"kind": "expense", "description": "LIVRARIA EXEMPLO",
                        "amount": "30,00", "category": "Importado", "occurred_on": "2026-10-10"})
        with patch.object(self.server.auth, "inference_credentials") as credentials:
            with patch("app.suggest_categories") as suggest:
                status, result = self.call_api("/api/v1/categorization/suggest", "POST", {})
        self.assertEqual((status, result["items"][0]["suggested_category"]), (200, "Educação"))
        credentials.assert_not_called()
        suggest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
