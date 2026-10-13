import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from finance import FinanceStore
from pluggy_inter import PluggyInter, _bank_classification
from pluggy_xp import PluggyError, PluggyXP


XP_ID = "11111111-1111-1111-1111-111111111111"
INTER_ID = "22222222-2222-2222-2222-222222222222"
BANK_ID = "33333333-3333-3333-3333-333333333333"
CARD_ID = "44444444-4444-4444-4444-444444444444"
BILL_ID = "55555555-5555-5555-5555-555555555555"
OLD_BILL_ID = "66666666-6666-6666-6666-666666666666"


def transaction(identifier, date, description, amount, kind, **extra):
    return {"id": identifier, "date": date, "description": description, "amount": amount,
            "type": kind, "status": "POSTED", "currencyCode": "BRL", **extra}


class PluggyInterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.store = FinanceStore(base / "financas.db")
        self.pluggy = PluggyXP(self.store, base / "pluggy-xp.json")
        self.pluggy.save_config({"client_id": "client", "client_secret": "secret",
                                 "item_id": XP_ID, "inter_item_id": INTER_ID})
        self.inter = PluggyInter(self.store, self.pluggy)

    def tearDown(self):
        self.directory.cleanup()

    def fake_http(self, method, path, payload=None, api_key=None):
        if path == "/auth":
            self.assertEqual(payload, {"clientId": "client", "clientSecret": "secret"})
            return {"apiKey": "session"}
        self.assertEqual(api_key, "session")
        if path.startswith("/accounts?"):
            return {"results": [
                {"id": BANK_ID, "type": "BANK", "name": "Conta Corrente",
                 "currencyCode": "BRL", "balance": 18.36, "updatedAt": "2026-10-13T12:00:00Z",
                 "number": "private-account-number", "taxNumber": "private-cpf"},
                {"id": CARD_ID, "type": "CREDIT", "name": "Cartão principal", "currencyCode": "BRL"},
            ], "totalPages": 1}
        if path.startswith("/bills?"):
            return {"results": [{"id": BILL_ID, "dueDate": "2026-10-20T00:00:00Z"},
                                {"id": OLD_BILL_ID, "dueDate": "2026-09-20T00:00:00Z"}],
                    "totalPages": 1}
        if path.startswith("/v2/transactions?") and BANK_ID in path:
            return {"results": [
                transaction("aaaaaaa1-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "2026-09-20T00:00:00Z",
                            "Compra no débito", -20, "DEBIT"),
                transaction("aaaaaaa2-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "2026-10-12T00:00:00Z",
                            "Mercado", -30, "DEBIT"),
                transaction("aaaaaaa3-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "2026-10-12T00:00:00Z",
                            "Pagamento fatura cartão", -40, "DEBIT"),
            ], "next": None}
        if path.startswith("/v2/transactions?") and CARD_ID in path:
            return {"results": [
                transaction("bbbbbbb1-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "2026-10-10T00:00:00Z",
                            "Restaurante", 25, "DEBIT", creditCardMetadata={"billId": BILL_ID}),
                transaction("bbbbbbb2-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "2026-09-10T00:00:00Z",
                            "Fatura antiga", 50, "DEBIT", creditCardMetadata={"billId": OLD_BILL_ID}),
                transaction("bbbbbbb3-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "2026-10-10T00:00:00Z",
                            "Pagamento recebido", -40, "CREDIT", operationType="PAGAMENTO_FATURA",
                            creditCardMetadata={"billId": BILL_ID}),
                transaction("bbbbbbb4-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "2026-10-13T00:00:00Z",
                            "Compra pendente", 99, "DEBIT", status="PENDING",
                            creditCardMetadata={"billForecastDate": "2026-10"}),
            ], "next": None}
        raise AssertionError(path)

    def test_preview_then_import_replaces_covered_statements_and_keeps_data_private(self):
        self.store.save_account_statement("Inter · conta corrente", "personal",
                                          {"starts_on": "2026-09-09", "ends_on": "2026-10-09"}, [], None)
        self.store.add_imported([{"kind": "expense", "description": "Fatura antiga",
                                  "category": "Importado", "amount_cents": 5000,
                                  "occurred_on": "2026-09-10", "excluded": False,
                                  "source": "Inter · cartão principal", "invoice_month": "2026-09",
                                  "import_key": hashlib.sha256(b"old-statement").hexdigest(),
                                  "scope": "personal"}])
        self.store.save_invoice_coverage("Inter · cartão principal", "personal", "2026-09")
        preview = self.inter.preview(self.fake_http)
        self.assertEqual(preview["new_count"], 6)
        self.assertEqual(preview["replaced_count"], 1)
        self.assertEqual(preview["skipped"], {"card_payment": 1})
        self.assertEqual(preview["bank_balance"]["amount_cents"], 1836)
        self.assertNotIn("private-cpf", json.dumps(preview))
        self.assertNotIn("private-account-number", json.dumps(preview))
        self.assertNotIn("secret", json.dumps(preview))
        with self.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
        with self.assertRaises(PluggyError):
            self.inter.import_preview("wrong-token")
        result = self.inter.import_preview(preview["preview_token"])
        self.assertEqual((result["imported"],result["replaced"],result["balance_updated"]), (6,1,True))
        self.assertTrue((self.store.path.parent / result["backup"]).exists())
        self.assertEqual(self.store.account_balance_snapshots("2026-10")[0]["amount_cents"], 1836)
        with self.store._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT description, category, excluded, invoice_month FROM transactions ORDER BY id")]
        self.assertEqual(len(rows), 6)
        self.assertIn({"description": "Pagamento fatura cartão", "category": "Pagamento de fatura",
                       "excluded": 1, "invoice_month": None}, rows)
        repeat = self.inter.preview(self.fake_http)
        self.assertEqual(repeat["replaced_count"], 6)
        self.inter.import_preview(repeat["preview_token"])
        self.assertEqual(len(self.store.list_month("2026-10")), 4)

    def test_new_card_charge_in_same_month_can_be_synced_later(self):
        first = self.inter.preview(self.fake_http)
        self.inter.import_preview(first["preview_token"])

        def updated_http(method, path, payload=None, api_key=None):
            response = self.fake_http(method, path, payload, api_key)
            if path.startswith("/v2/transactions?") and CARD_ID in path:
                response["results"].append(transaction(
                    "bbbbbbb5-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "2026-10-13T00:00:00Z",
                    "Nova compra", 12, "DEBIT", creditCardMetadata={"billId": BILL_ID}))
            return response

        second = self.inter.preview(updated_http)
        self.assertEqual(second["new_count"], 7)
        self.assertEqual(second["new_items"][-1]["description"], "Nova compra")

    def test_duplicates_are_replaced_but_categories_and_pj_survive(self):
        self.store.add_card_purchase({"bank":"inter","account":"cartão principal","invoice_month":"2026-10",
            "description":"Restaurante","category":"Alimentação","amount":"25,00","occurred_on":"2026-10-10"})
        self.store.add_imported([{"kind":"expense","description":"Restaurante","category":"Importado",
            "amount_cents":2500,"occurred_on":"2026-10-10","source":"Inter · cartão principal",
            "invoice_month":"2026-11","scope":"personal","import_key":hashlib.sha256(b"legacy").hexdigest()}])
        pj = self.store.add({"kind":"expense","description":"Empresa","category":"Outros","amount":"25,00",
                             "occurred_on":"2026-10-10","scope":"business"})
        preview = self.inter.preview(self.fake_http)
        self.inter.import_preview(preview["preview_token"])
        rows = [r for r in self.store.list_month("2026-10") if r["description"] == "Restaurante"]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["category"],"Alimentação")
        self.assertEqual(self.store.get(pj["id"])["scope"],"business")

    def test_refresh_removes_deleted_rows_and_updates_amounts(self):
        first = self.inter.preview(self.fake_http)
        self.inter.import_preview(first["preview_token"])
        def changed(method,path,payload=None,api_key=None):
            result = self.fake_http(method,path,payload,api_key)
            if path.startswith("/v2/transactions?") and BANK_ID in path:
                result["results"] = result["results"][1:]
                result["results"][0]["amount"] = -31
            return result
        second = self.inter.preview(changed)
        self.inter.import_preview(second["preview_token"])
        self.assertEqual(self.store.list_month("2026-09")[0]["description"], "Fatura antiga")
        mercado = next(r for r in self.store.list_month("2026-10") if r["description"]=="Mercado")
        self.assertEqual(mercado["amount_cents"],3100)

    def test_config_can_add_inter_without_reentering_secret(self):
        self.pluggy.delete_config()
        self.pluggy.save_config({"client_id": "client", "client_secret": "secret", "item_id": XP_ID})
        self.pluggy.save_config({"inter_item_id": INTER_ID})
        self.assertTrue(self.pluggy.configured())
        self.assertTrue(self.pluggy.inter_configured())
        self.assertEqual(self.pluggy.load_config()["client_secret"], "secret")

    def test_unknown_card_month_aborts_before_replacing(self):
        def broken(method,path,payload=None,api_key=None):
            result=self.fake_http(method,path,payload,api_key)
            if path.startswith('/v2/transactions?') and CARD_ID in path:
                result['results'][0]['creditCardMetadata']={}
            return result
        with self.assertRaises(PluggyError):
            self.inter.preview(broken)
        with self.store._connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM transactions').fetchone()[0],0)

    def test_investment_application_and_cashback_are_not_consumption(self):
        self.assertEqual(_bank_classification({"operationType":"OUTROS"},
            "Aplicação - Cdb Exemplo Banco", "expense"),("Movimento de investimento",True))
        self.assertEqual(_bank_classification({},"Cashback - Exemplo","income"),("Cashback",True))
        self.assertEqual(_bank_classification({},"Compra no débito - Cdb Restaurante","expense"),("Importado",False))


if __name__ == "__main__":
    unittest.main()
