import json
import os
import tempfile
import unittest
from pathlib import Path

from finance import FinanceStore, ValidationError
from pluggy_xp import PluggyError, PluggyXP


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


if __name__ == "__main__":
    unittest.main()
