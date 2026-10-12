import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import FinanceHandler
from finance import FinanceStore


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


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.directory.name) / "test.db")
        self.server = SimpleNamespace(store=self.store, server_port=8765)

    def tearDown(self):
        self.directory.cleanup()

    def call_api(self, path, method="GET", data=None, headers=None):
        body = json.dumps(data).encode("utf-8") if data is not None else b""
        header_lines = ["Connection: close"]
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
        status, history = self.call_api("/api/v1/invoices")
        self.assertEqual(status, 200)
        self.assertEqual(history["items"][0]["expense_cents"], 17345)
        status, details = self.call_api(
            "/api/v1/invoices/2026-10?source=Banco%20do%20Brasil%20%C2%B7%20principal"
        )
        self.assertEqual((status, len(details["items"])), (200, 2))

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

    def test_categorization_suggest_and_review_apply(self):
        self.call_api("/api/v1/transactions", "POST", {
            "kind": "expense", "description": "Mercado", "amount": "25,00",
            "category": "Importado", "occurred_on": "2026-10-09",
        })
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            status, info = self.call_api("/api/v1/categorization/status?month=2026-10")
            self.assertEqual((status, info["uncategorized_count"], info["configured"]), (200, 1, True))
            with patch("app.suggest_categories", return_value=[{"id": 1, "category": "Mercado"}]):
                status, suggestions = self.call_api("/api/v1/categorization/suggest", "POST",
                                                     {"scope": "personal", "month": "2026-10"})
            self.assertEqual((status, suggestions["items"][0]["suggested_category"]), (200, "Mercado"))
        status, saved = self.call_api("/api/v1/categorization/apply", "POST", {
            "scope": "personal", "items": [{"id": 1, "category": "Alimentação"}],
        })
        self.assertEqual((status, saved["applied"]), (200, 1))
        self.assertEqual(self.store.summary("2026-10")["purchase_categories"],
                         [{"category": "Alimentação", "amount_cents": 2500}])
        self.assertEqual(self.call_api("/api/v1/categorization/status?month=2026-10")[1]["uncategorized_count"], 0)


if __name__ == "__main__":
    unittest.main()
