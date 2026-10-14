from datetime import date
from pathlib import Path
import tempfile
import unittest

from finance import FinanceStore, ValidationError
from planning import save_commitment
from reserve_plan import reserve_overview, preview_reserve, save_reserve, delete_reserve


class ReservePlanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FinanceStore(Path(self.directory.name) / "test.db")
        self.today = date(2026, 1, 1)
        self.data = {"scope": "personal", "name": "Reserva exemplo", "essential_expenses": "1500,00",
                     "target_months": 3, "monthly_contribution": "500,00", "reserved_balance": "900,00",
                     "balance_as_of": "2025-12-31", "next_contribution_on": "2026-01-31"}

    def test_preview_is_not_saved_and_does_not_create_financial_transfers(self):
        result = preview_reserve(self.store, self.data, self.today)
        self.assertFalse(reserve_overview(self.store, today=self.today)["configured"])
        projection = result["projection"]
        self.assertEqual((projection["target_cents"], projection["remaining_cents"], projection["contributions_needed"]),
                         (450000, 360000, 8))
        self.assertEqual(projection["completion_on"], "2026-08-31")
        self.assertEqual(projection["points"][-1]["planned_cents"], 10000)
        self.assertEqual(projection["points"][-1]["balance_cents"], 450000)
        with self.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM investment_entries").fetchone()[0], 0)

    def test_dates_clamp_february_and_keep_original_day_for_march(self):
        result = save_reserve(self.store, self.data, self.today)
        self.assertEqual([point["on"] for point in result["projection"]["points"][1:4]],
                         ["2026-01-31", "2026-02-28", "2026-03-31"])
        updated = reserve_overview(self.store, today=date(2026, 2, 15))
        self.assertEqual(updated["projection"]["next_contribution_on"], "2026-02-28")
        self.assertEqual(updated["projection"]["completion_on"], "2026-09-30")

    def test_zero_contributions_pause_and_completed_goal_needs_no_contributions(self):
        paused = preview_reserve(self.store, {**self.data, "monthly_contribution": "0,00"}, self.today)
        self.assertEqual(paused["projection"]["status"], "paused")
        self.assertIsNone(paused["projection"]["completion_on"])
        self.assertIsNone(paused["projection"]["contributions_needed"])
        complete = preview_reserve(self.store, {**self.data, "reserved_balance": "5000,00"}, self.today)
        self.assertEqual(complete["projection"]["status"], "complete")
        self.assertEqual(complete["projection"]["progress_percent"], 100)
        self.assertEqual(complete["cash_check"]["simulated_contributions"], 0)

    def test_edit_after_february_keeps_monthly_day_31(self):
        save_reserve(self.store, self.data, self.today)
        current = reserve_overview(self.store, today=date(2026, 2, 15))
        updated = save_reserve(self.store, {**self.data, 'reserved_balance':'1200,00',
                              'next_contribution_on':current['projection']['next_contribution_on'],
                              'contribution_day':current['plan']['contribution_day']}, date(2026, 2, 15))
        self.assertEqual([point['on'] for point in updated['projection']['points'][1:4]],
                         ['2026-02-28','2026-03-31','2026-04-30'])

    def test_scope_isolation_updates_and_delete_preserve_bank_records(self):
        self.store.add({"kind": "income", "description": "Exemplo", "amount": "100,00",
                        "category": "Receita", "scope": "personal", "occurred_on": "2026-01-01"})
        save_reserve(self.store, self.data, self.today)
        save_reserve(self.store, {**self.data, "scope": "business", "reserved_balance": "100,00"}, self.today)
        save_reserve(self.store, {**self.data, "reserved_balance": "1200,00"}, self.today)
        self.assertEqual(reserve_overview(self.store, "personal", self.today)["plan"]["reserved_cents"], 120000)
        self.assertEqual(reserve_overview(self.store, "business", self.today)["plan"]["reserved_cents"], 10000)
        delete_reserve(self.store, "personal")
        self.assertFalse(reserve_overview(self.store, "personal", self.today)["configured"])
        self.assertTrue(reserve_overview(self.store, "business", self.today)["configured"])
        with self.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)

    def test_cash_check_uses_scope_and_warns_about_negative_partial_projection(self):
        self.store.save_account_balance("Conta exemplo", "personal", "2025-12-31", 100000)
        self.store.save_account_balance("Conta empresa", "business", "2025-12-31", 900000)
        save_commitment(self.store, {"scope": "personal", "kind": "expense", "description": "Conta exemplo",
                                    "amount": "600,00", "due_day": 15, "starts_month": "2026-01"})
        result = preview_reserve(self.store, self.data, self.today)["cash_check"]
        self.assertEqual(result["status"], "negative_projection")
        self.assertEqual(result["minimum_balance_cents"], -10000)
        self.assertEqual(result["simulated_contributions"], 1)
        self.assertIn("parcial", result["note"])
        missing = preview_reserve(self.store, {**self.data, "scope": "business"}, self.today)["cash_check"]
        self.assertEqual(missing["status"], "review_projection")

    def test_missing_balances_and_very_small_contributions_do_not_invent_dates(self):
        result = preview_reserve(self.store, {**self.data, "monthly_contribution": "0,01"}, self.today)
        self.assertEqual(result["cash_check"]["status"], "missing_balance")
        self.assertIsNone(result["cash_check"]["minimum_balance_cents"])
        self.assertIsNone(result["projection"]["completion_on"])
        self.assertTrue(result["projection"]["projection_truncated"])
        self.assertEqual(len(result["projection"]["points"]), 25)

    def test_invalid_money_dates_and_scopes_are_rejected_without_saving(self):
        invalid = [{"essential_expenses": "0"}, {"monthly_contribution": "-1"}, {"reserved_balance": "NaN"},
                   {"target_months": True}, {"target_months": 25}, {"scope": "all"}, {"name": " "},
                   {"balance_as_of": "2026-01-02"}, {"next_contribution_on": "2025-12-01"},
                   {"next_contribution_on": "2028-01-01"}, {"next_contribution_on": "2026-02-30"}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                save_reserve(self.store, {**self.data, **change}, self.today)
        self.assertFalse(reserve_overview(self.store, today=self.today)["configured"])


if __name__ == "__main__":
    unittest.main()
