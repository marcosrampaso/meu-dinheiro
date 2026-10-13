import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from cdi import CDIBenchmark, compound_cdi, fetch_cdi
from finance import FinanceStore, ValidationError


class CDITests(unittest.TestCase):
    def test_fetch_and_compound_daily_percent(self):
        payload = json.dumps([
            {"data": "05/10/2026", "valor": "0.050000"},
            {"data": "06/10/2026", "valor": "0.050000"},
        ]).encode()
        with patch("cdi._download", return_value=payload) as download:
            rows = fetch_cdi(date(2026, 10, 1), date(2026, 10, 7))
        self.assertIn("bcdata.sgs.12", download.call_args.args[0])
        points = compound_cdi(rows, date(2026, 10, 1), date(2026, 10, 7))
        self.assertEqual([point["date"] for point in points],
                         ["2026-10-01", "2026-10-05", "2026-10-06"])
        self.assertAlmostEqual(points[-1]["cumulative_percent"], 0.100025)

    def test_rejects_unexpected_rates_and_preserves_cached_data_on_failure(self):
        with patch("cdi._download", return_value=b'[{"data":"05/10/2026","valor":"100"}]'):
            with self.assertRaises(ValueError):
                fetch_cdi(date(2026, 10, 1), date(2026, 10, 7))
        with tempfile.TemporaryDirectory() as directory:
            store = FinanceStore(Path(directory) / "test.db")
            benchmark = CDIBenchmark(store)
            store.save_market_quotes([{"source": "bcb", "identifier": "12",
                                      "quote_date": "2026-10-05", "price": "0.05",
                                      "checked_at": "2026-10-05T12:00:00+00:00"}])
            with patch("cdi.fetch_cdi", side_effect=OSError("sem rede")):
                benchmark.refresh(date(2026, 10, 7))
            overview = benchmark.overview(1, date(2026, 10, 7))
            self.assertEqual(overview["cumulative_percent"], 0.05)
            self.assertEqual(overview["error"], "sem rede")
            with self.assertRaises(ValidationError):
                benchmark.overview(2)


if __name__ == "__main__":
    unittest.main()
