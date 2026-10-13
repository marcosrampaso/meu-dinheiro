import tempfile
import unittest
import sqlite3
from pathlib import Path

from finance import FinanceStore, ValidationError
from manual_investments import (delete_manual_flow, delete_manual_snapshot, manual_overview,
                                save_manual_flow, save_manual_snapshot)
from performance import performance_overview


class ManualInvestmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.directory.name) / "finance.db")

    def tearDown(self):
        self.directory.cleanup()

    def snapshot(self, day, cents):
        self.store.import_xp_positions({"as_of": day, "total_cents": cents,
                                        "assets_cents": cents, "receivables_cents": 0, "cash_cents": 0,
                                        "assets": [{"name": "Exemplo", "category": "Fundos de Investimentos",
                                                    "value_cents": cents, "cost_cents": cents,
                                                    "quantity": None, "net_cents": None}]})

    def test_manual_flow_can_be_edited_and_duplicate_csv_deposit_is_counted_once(self):
        self.snapshot("2024-01-01", 100000)
        self.snapshot("2024-01-11", 120000)
        with self.store._connect() as connection:
            connection.execute("""INSERT INTO investment_entries
                (import_key, occurred_at, description, amount_cents, balance_cents)
                VALUES (?, '2024-01-06T12:00:00', 'Pix recebido de Exemplo', 10000, 0)""",
                               ("a" * 64,))
        saved = save_manual_flow(self.store, {"occurred_on": "2024-01-06", "kind": "deposit", "amount": "100,00"})
        self.assertTrue(manual_overview(self.store)["flows"][0]["matched_import"])
        self.assertEqual(self.store.investment_overview("2024-01", 1)["total_deposited_cents"], 10000)
        self.store.save_market_quotes([{"source": "bcb", "identifier": "12", "quote_date": day,
                                        "price": "0.05", "checked_at": "2024-01-12T00:00:00Z"}
                                       for day in ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
                                                   "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11")])
        self.assertEqual(performance_overview(self.store)["intervals"][0]["external_flow_cents"], 10000)
        save_manual_flow(self.store, {"occurred_on": "2024-01-07", "kind": "deposit", "amount": "150,00"}, saved["id"])
        self.assertFalse(manual_overview(self.store)["flows"][0]["matched_import"])
        self.assertEqual(self.store.investment_overview("2024-01", 1)["total_deposited_cents"], 25000)
        self.assertEqual(performance_overview(self.store)["intervals"][0]["external_flow_cents"], 25000)
        delete_manual_flow(self.store, saved["id"])
        self.assertEqual(self.store.investment_overview("2024-01", 1)["total_deposited_cents"], 10000)

    def test_manual_total_overrides_comparison_only_and_is_editable(self):
        self.snapshot("2024-01-01", 100000)
        self.snapshot("2024-01-11", 120000)
        save_manual_snapshot(self.store, {"as_of": "2024-01-11", "total": "1.300,00"})
        self.assertEqual(self.store.investment_overview("2024-01", 1)["latest_total"]["total_cents"], 130000)
        self.assertEqual(self.store.investment_overview("2024-01", 1)["position"]["total_cents"], 120000)
        save_manual_snapshot(self.store, {"as_of": "2024-01-11", "total": "1.350,00"})
        self.assertEqual(manual_overview(self.store)["snapshots"][0]["total_cents"], 135000)
        self.store.save_market_quotes([{"source": "bcb", "identifier": "12", "quote_date": day,
                                        "price": "0.05", "checked_at": "2024-01-12T00:00:00Z"}
                                       for day in ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
                                                   "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11")])
        self.assertEqual(performance_overview(self.store)["portfolio_percent"], 35.0)
        delete_manual_snapshot(self.store, "2024-01-11")
        self.assertEqual(self.store.investment_overview("2024-01", 1)["latest_total"]["total_cents"], 120000)

    def test_invalid_manual_amount_and_date_are_rejected(self):
        with self.assertRaises(ValidationError):
            save_manual_flow(self.store, {"occurred_on": "2024-02-30", "kind": "deposit", "amount": "10,00"})
        with self.assertRaises(ValidationError):
            save_manual_snapshot(self.store, {"as_of": "2024-01-10", "total": "0"})

    def test_asset_dropdown_and_internal_history_do_not_inflate_xp_deposits(self):
        self.snapshot("2024-01-01", 100000)
        self.snapshot("2024-01-11", 110000)
        asset = manual_overview(self.store)["assets"][0]
        self.assertEqual(asset["name"], "Exemplo")
        saved = save_manual_flow(self.store, {"occurred_on": "2024-01-05", "kind": "deposit",
                                                    "amount": "150,00", "asset_id": asset["id"],
                                                    "external": False})
        overview = manual_overview(self.store)
        self.assertEqual(overview["flows"][0]["asset_name"], "Exemplo")
        self.assertFalse(overview["flows"][0]["external"])
        self.assertEqual(overview["asset_totals"][0]["deposited_cents"], 15000)
        self.assertEqual(self.store.investment_overview("2024-01", 1)["total_deposited_cents"], 0)
        self.store.save_market_quotes([{"source": "bcb", "identifier": "12", "quote_date": day,
                                        "price": "0.05", "checked_at": "2024-01-12T00:00:00Z"}
                                       for day in ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
                                                   "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11")])
        self.assertEqual(performance_overview(self.store)["intervals"][0]["external_flow_cents"], 0)
        save_manual_flow(self.store, {"occurred_on": "2024-01-05", "kind": "deposit",
                                           "amount": "150,00", "asset_id": asset["id"],
                                           "external": True}, saved["id"])
        self.assertEqual(self.store.investment_overview("2024-01", 1)["total_deposited_cents"], 15000)
        self.assertEqual(performance_overview(self.store)["intervals"][0]["external_flow_cents"], 15000)
        with self.store._connect() as connection:
            connection.execute("""INSERT INTO investment_entries
                (import_key, occurred_at, description, amount_cents, balance_cents)
                VALUES (?, '2024-01-05T12:00:00', 'Pix recebido de Exemplo', 15000, 0)""",
                               ("b" * 64,))
        self.assertTrue(manual_overview(self.store)["flows"][0]["matched_import"])
        self.assertEqual(self.store.investment_overview("2024-01", 1)["total_deposited_cents"], 15000)
        self.assertEqual(performance_overview(self.store)["intervals"][0]["external_flow_cents"], 15000)
        with self.assertRaises(ValidationError):
            save_manual_flow(self.store, {"occurred_on": "2024-01-05", "kind": "deposit",
                                               "amount": "10,00", "asset_id": 99999, "external": False})

    def test_existing_manual_flows_keep_external_meaning_after_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            with sqlite3.connect(path) as connection:
                connection.execute("""CREATE TABLE investment_manual_flows (
                    id INTEGER PRIMARY KEY, occurred_on TEXT NOT NULL,
                    kind TEXT NOT NULL, amount_cents INTEGER NOT NULL)""")
                connection.execute("""INSERT INTO investment_manual_flows
                    (occurred_on, kind, amount_cents) VALUES ('2024-01-05', 'deposit', 10000)""")
            migrated = FinanceStore(path)
            flow = manual_overview(migrated)["flows"][0]
            self.assertTrue(flow["external"])
            self.assertIsNone(flow["asset_id"])


if __name__ == "__main__":
    unittest.main()
