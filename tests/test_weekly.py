import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from finance import FinanceStore
from weekly import WeeklyError, generate_weekly_report, saved_weekly_report, save_weekly_report, weekly_context


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.directory.name) / "test.db")

    def tearDown(self):
        self.directory.cleanup()

    def test_aggregates_completed_week_by_scope_without_internal_transfers(self):
        for occurred_on, amount, category, scope, excluded in [
            ("2026-10-05", "30,00", "Mercado", "personal", False),
            ("2026-10-06", "10,00", "Importado", "personal", False),
            ("2026-09-28", "20,00", "Mercado", "personal", False),
            ("2026-10-05", "100,00", "Outros", "business", False),
            ("2026-10-05", "500,00", "Outros", "personal", True),
            ("2026-10-12", "60,00", "Lazer", "personal", False),
        ]:
            self.store.add({"kind": "expense", "description": "Exemplo", "amount": amount,
                            "category": category, "occurred_on": occurred_on,
                            "scope": scope, "excluded": excluded})
        context = weekly_context(self.store, "personal", date(2026, 10, 13))
        self.assertEqual((context["starts_on"], context["ends_on"]), ("2026-10-05", "2026-10-11"))
        self.assertEqual((context["current_total_cents"], context["previous_total_cents"]), (4000, 2000))
        self.assertEqual(context["categories"][0]["category"], "Mercado")
        self.assertEqual(weekly_context(self.store, "all", date(2026, 10, 13))["current_total_cents"], 14000)
        report = {"headline": "Mercado liderou", "insights": ["Mercado foi o maior gasto.", "Revise os não categorizados."]}
        save_weekly_report(self.store, context, report)
        self.assertEqual(saved_weekly_report(self.store, context)["headline"], "Mercado liderou")
        self.store.add({"kind": "expense", "description": "Outro", "amount": "1,00",
                        "category": "Lazer", "occurred_on": "2026-10-07"})
        self.assertIsNone(saved_weekly_report(self.store, weekly_context(self.store, "personal", date(2026, 10, 13))))

    def test_rejects_model_numbers_not_supported_by_display(self):
        context = {"starts_on": "2026-10-05", "ends_on": "2026-10-11",
                   "current_total_cents": 1000, "previous_total_cents": 500, "categories": []}
        payload = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"headline": "Gastou 90% mais",
                "insights": ["Mercado subiu.", "Observe lazer."]})}]}]}
        with patch("weekly.urlopen") as open_request:
            open_request.return_value.__enter__.return_value = []
            with patch("weekly._read_completed_response", return_value=(payload, "")):
                with self.assertRaises(WeeklyError):
                    generate_weekly_report(context, "token", "gpt-6-luna")


if __name__ == "__main__":
    unittest.main()
