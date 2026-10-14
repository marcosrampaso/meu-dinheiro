import json
import os
import tempfile
import unittest
import hashlib
from pathlib import Path

from finance import FinanceStore, ValidationError
from pluggy_xp import PluggyError, PluggyXP
from performance import performance_overview


ITEM_ID = "12345678-1234-1234-1234-123456789abc"


class PluggyXPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.integration = PluggyXP(FinanceStore(base / "test.db"), base / "data" / "pluggy-xp.json")
        self.integration.save_config({"client_id": "test-client", "client_secret": "test-secret",
                                      "item_id": ITEM_ID})

    def tearDown(self):
        self.directory.cleanup()

    def test_read_only_preview_paginates_and_keeps_credentials_private(self):
        calls = []

        def fake_http(method, path, payload=None, api_key=None):
            calls.append((method, path, payload, api_key))
            if path == "/auth":
                return {"apiKey": "session-key"}
            if "page=1" in path:
                return {"results": [{"name": "Fundo Exemplo", "type": "MUTUAL_FUND",
                                     "currencyCode": "BRL", "amount": 120.455,
                                     "balance": 119.40, "date": "2026-10-12T00:00:00Z"}],
                        "totalPages": 2}
            return {"results": [{"name": "ACAO11", "type": "EQUITY",
                                 "currencyCode": "BRL", "amount": 80.00,
                                 "balance": 80.00}], "totalPages": 2}

        result = self.integration.preview(fake_http)
        self.assertEqual((len(result["assets"]), result["gross_cents"], result["net_cents"]),
                         (2, 20046, 19940))
        self.assertEqual(calls[0][2]["clientSecret"], "test-secret")
        self.assertTrue(all(call[3] == "session-key" for call in calls[1:]))
        self.assertNotIn("test-secret", json.dumps(result))
        self.assertEqual(os.stat(self.integration.config_path).st_mode & 0o777, 0o600)
        with self.integration.store._connect() as connection:
            self.assertFalse(list(connection.execute("SELECT * FROM investment_snapshots")))

    def test_invalid_or_incomplete_payload_does_not_change_portfolio(self):
        with self.assertRaises(ValidationError):
            self.integration.save_config({"client_id": "a", "client_secret": "b", "item_id": "wrong"})
        with self.assertRaises(PluggyError):
            self.integration.preview(lambda method, path, payload=None, api_key=None:
                                     {"apiKey": "key"} if path == "/auth" else
                                     {"results": [{"name": "Incomplete", "currencyCode": "BRL",
                                                   "balance": 50}], "totalPages": 1})
        with self.integration.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM investment_snapshots").fetchone()[0], 0)

    def sync_http(self, method, path, payload=None, api_key=None):
        if path == "/auth":
            return {"apiKey":"key"}
        if path.startswith("/investments?"):
            return {"results":[{"name":"Fundo Exemplo","type":"MUTUAL_FUND","currencyCode":"BRL",
                                "amount":120,"balance":119,"date":"2026-10-12"}], "totalPages":1}
        if path.startswith("/accounts?"):
            return {"results":[{"id":ITEM_ID,"type":"BANK","currencyCode":"BRL","balance":10,
                                 "updatedAt":"2026-10-13"}], "totalPages":1}
        if path.startswith("/v2/transactions?"):
            return {"results":[{"id":"22222222-2222-2222-2222-222222222222","date":"2026-10-05",
                                "type":"CREDIT","amount":100,"currencyCode":"BRL","status":"POSTED",
                                "description":"Pix recebido de Ana Exemplo","operationType":"PIX"}],"next":None}
        raise AssertionError(path)

    def test_sync_replaces_legacy_entries_and_marks_incomplete_portfolio(self):
        store = self.integration.store
        legacy = {"import_key":hashlib.sha256(b"legacy").hexdigest(),"occurred_at":"2026-10-05T08:00:00",
                  "occurred_on":"2026-10-05","description":"Pix recebido de Ana Exemplo",
                  "amount_cents":10000,"balance_cents":10000}
        store.import_xp_statement([legacy])
        preview = self.integration.preview_sync(self.sync_http)
        self.assertEqual((preview["count"],preview["replaced_count"]),(1,1))
        with store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM investment_entries").fetchone()[0],1)
        with self.assertRaises(PluggyError):
            self.integration.import_preview("wrong")
        result = self.integration.import_preview(preview["preview_token"])
        self.assertEqual((result["imported"],result["replaced"]),(1,1))
        position = store.latest_investment_position()
        self.assertEqual((position["provider"],position["complete"],position["total_cents"]),("pluggy",0,13000))
        self.assertEqual(position["assets"][0]["cost_known"],0)
        self.assertEqual(performance_overview(store)["snapshot_count"],1)
        self.assertTrue(result['performance_saved'])
        second = self.integration.preview_sync(self.sync_http)
        self.integration.import_preview(second["preview_token"])
        with store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM investment_entries").fetchone()[0],1)
        self.assertNotIn("test-secret",json.dumps(preview))

    def test_sync_failure_rolls_back_all_data_and_keeps_backup(self):
        from pluggy_sync import apply_xp
        preview = self.integration.preview_sync(self.sync_http)
        pending = self.integration._sync_pending
        pending["snapshot"]["assets"].append(pending["snapshot"]["assets"][0])
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            apply_xp(self.integration.store,pending)
        with self.integration.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM investment_entries").fetchone()[0],0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM investment_snapshots").fetchone()[0],0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM investment_observations").fetchone()[0],0)
        self.assertTrue(list(self.integration.store.path.parent.glob("*.bak")))

    def test_old_manual_preview_cannot_overwrite_a_new_webhook_sync(self):
        old = self.integration.preview_sync(self.sync_http)
        automatic = PluggyXP(self.integration.store,self.integration.config_path)
        current = automatic.preview_sync(self.sync_http)
        automatic.import_preview(current['preview_token'])
        with self.assertRaisesRegex(PluggyError,'após esta prévia'):
            self.integration.import_preview(old['preview_token'])
        with self.integration.store._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM investment_entries').fetchone()[0],1)


if __name__ == "__main__":
    unittest.main()
