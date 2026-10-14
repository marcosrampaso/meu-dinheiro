import tempfile
import unittest
from pathlib import Path

from finance import FinanceStore
from performance import classify_flow, performance_overview


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.directory.name) / "test.db")

    def tearDown(self):
        self.directory.cleanup()

    def _snapshot(self, as_of, cents):
        self.store.import_xp_positions({"as_of": as_of, "total_cents": cents,
                                        "assets_cents": cents, "receivables_cents": 0, "cash_cents": 0,
                                        "assets": [{"name": "FUNDO EXEMPLO", "category": "Fundos de Investimentos",
                                                    "value_cents": cents, "cost_cents": cents,
                                                    "quantity": None, "net_cents": None}]})

    def test_dietz_adjusts_external_deposit_and_does_not_count_internal_allocation(self):
        self._snapshot("2026-10-01", 100000)
        self.assertEqual(performance_overview(self.store)["status"], "needs_snapshots")
        self._snapshot("2026-10-11", 120000)
        with self.store._connect() as connection:
            for key, day, description, amount in [
                ("a" * 64, "2026-10-06", "Pix recebido de Exemplo", 10000),
                ("b" * 64, "2026-10-07", "Transferência enviada para conta investimento", -5000),
            ]:
                connection.execute("""INSERT INTO investment_entries
                    (import_key, occurred_at, description, amount_cents, balance_cents)
                    VALUES (?, ?, ?, ?, 0)""", (key, day + "T12:00:00", description, amount))
        self.store.save_market_quotes([{"source": "bcb", "identifier": "12", "quote_date": day,
                                        "price": "0.05", "checked_at": "2026-10-12T00:00:00Z"}
                                       for day in ("2026-10-02", "2026-10-05", "2026-10-06",
                                                   "2026-10-07", "2026-10-08", "2026-10-09")])
        result = performance_overview(self.store)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["intervals"][0]["external_flow_cents"], 10000)
        self.assertAlmostEqual(result["portfolio_percent"], 9.5238, places=3)
        self.assertAlmostEqual(result["cdi_percent"], 0.3, places=2)

    def test_ambiguous_transfer_blocks_comparison_until_classified(self):
        self._snapshot("2026-10-01", 100000)
        self._snapshot("2026-10-11", 100000)
        with self.store._connect() as connection:
            connection.execute("""INSERT INTO investment_entries
                (import_key, occurred_at, description, amount_cents, balance_cents)
                VALUES (?, '2026-10-06T12:00:00', 'Transferência para outra conta', -1000, 0)""",
                               ("c" * 64,))
        self.assertEqual(performance_overview(self.store)["status"], "review_flows")
        classify_flow(self.store, "c" * 64, "internal")
        result = performance_overview(self.store)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["benchmark_status"], "needs_cdi")
        self.assertIsNone(result["cdi_percent"])


if __name__ == "__main__":
    unittest.main()
