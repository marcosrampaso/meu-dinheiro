import calendar
from datetime import date
from pathlib import Path
import tempfile
import unittest

from finance import FinanceStore, ValidationError
from plan_insights import history_insights, preview_history, save_history_review


class PlanInsightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FinanceStore(Path(self.directory.name) / "fixture.db")
        self.today = date(2026, 10, 14)

    def add(self, month, category, amount="100,00", description="Compra exemplo", kind="expense",
            scope="personal", invoice=None, pending=False, excluded=False, day=10):
        row = self.store.add({"kind": kind, "description": description, "category": category,
            "amount": amount, "scope": scope, "occurred_on": f"{month}-{day:02d}", "excluded": excluded})
        with self.store._connect() as c:
            c.execute("UPDATE transactions SET source=?,invoice_month=?,pending=? WHERE id=?",
                      ("Cartão exemplo" if invoice else "Conta exemplo", invoice, pending, row["id"]))
        return row

    def coverage(self, month, card=False):
        end = calendar.monthrange(int(month[:4]), int(month[5:]))[1]
        self.store.save_account_statement("Conta exemplo", "personal", {"starts_on": month + "-01",
            "ends_on": f"{month}-{end}"}, [])
        if card:
            self.store.save_invoice_coverage("Cartão exemplo", "personal", month)

    def draft(self, **changes):
        data = history_insights(self.store, today=self.today)
        return {"scope": "personal", "months": 6, "signature": data["signature"],
                "roles": {row["category"]: row["role"] for row in data["categories"]},
                "monthly_income": "1000,00", "buffer_percent": 10,
                "target_months": 6, "reserved_balance": "0", "acknowledged": True, **changes}

    def test_bill_month_excludes_payments_transfers_pending_and_other_scopes(self):
        self.add("2026-05", "Mercado", "100,00", invoice="2026-07")
        self.add("2026-07", "Pagamento de fatura", "100,00")
        self.add("2026-07", "Antecipação da fatura", "50,00")
        self.add("2026-07", "Transferência para investimento", "200,00")
        self.add("2026-07", "Transferência entre contas", "300,00")
        self.add("2026-07", "Mercado", "900,00", pending=True)
        self.add("2026-07", "Mercado", "800,00", scope="business")
        self.add("2026-10", "Mercado", "700,00")
        self.add("2026-07", "Salário", "1000,00", kind="income")
        self.add("2026-07", "Cashback", "20,00", kind="income")
        self.add("2026-07", "Estorno", "30,00", kind="income", invoice="2026-07")
        result = history_insights(self.store, today=self.today)
        self.assertEqual(result["base_months"], ["2026-07"])
        self.assertEqual(result["average_spending_cents"], 10000)
        self.assertEqual(result["observed_income_cents"], 100000)
        self.assertTrue(any("brutas" in warning for warning in result["warnings"]))
        self.assertEqual(history_insights(self.store, "business", today=self.today)["average_spending_cents"], 80000)

    def test_missing_months_are_not_zero_and_partial_base_is_explicit(self):
        self.add("2026-07", "Mercado", "100,00")
        self.add("2026-09", "Mercado", "300,00")
        result = history_insights(self.store, today=self.today)
        self.assertEqual(result["average_spending_cents"], 20000)
        self.assertEqual(result["base_months"], ["2026-07", "2026-09"])
        self.assertEqual(result["quality"], "provisional")
        self.assertIsNone(result["observed_income_cents"])
        self.assertEqual(result["categories"][0]["minimum_cents"], 10000)
        self.assertIsNone(history_insights(self.store, "business", today=self.today)["average_spending_cents"])

    def test_three_documented_months_replace_partial_samples_in_averages(self):
        for month in ["2026-07", "2026-08", "2026-09"]:
            self.coverage(month, card=True)
            self.add(month, "Mercado", "100,00", invoice=month)
            self.add(month, "Salário", "1000,00", kind="income")
        self.add("2026-06", "Compras", "9000,00", invoice="2026-06")
        result = history_insights(self.store, today=self.today)
        self.assertEqual(result["quality"], "documented")
        self.assertEqual(result["base_months"], ["2026-07", "2026-08", "2026-09"])
        self.assertEqual(result["average_spending_cents"], 10000)
        self.assertEqual([row["category"] for row in result["categories"]], ["Mercado"])

    def test_recurrence_is_evidence_not_added_to_expenses(self):
        for index, month in enumerate(["2026-07", "2026-08", "2026-09"], 1):
            self.add(month, "Assinaturas", "50,00", "Streaming exemplo")
            self.add(month, "Mercado", "20,00", "Supermercado exemplo", day=10)
            self.add(month, "Mercado", "30,00", "Supermercado exemplo", day=20)
            self.add(month, "Compras", "90,00", f"Loja exemplo {index}/3", invoice=month)
        result = history_insights(self.store, today=self.today)
        self.assertEqual(len(result["recurring"]), 2)
        self.assertEqual({row["type"] for row in result["recurring"]}, {"recurring", "installment"})
        self.assertEqual(result["average_spending_cents"], 19000)
        draft = self.draft()
        draft["roles"]["Assinaturas"] = "essential"
        draft["roles"]["Compras"] = "flexible"
        preview = preview_history(self.store, draft, self.today)
        self.assertEqual(preview["essential_cents"], 10000)
        self.assertEqual(preview["scenarios"][0]["spending_cents"], 19000)

    def test_reviewed_roles_income_margin_and_only_flexible_cuts_drive_scenarios(self):
        self.add("2026-09", "Mercado", "300,00")
        self.add("2026-09", "Lazer", "200,00")
        self.add("2026-09", "Outros", "100,00")
        result = preview_history(self.store, self.draft(), self.today)
        self.assertEqual(result["essential_cents"], 30000)
        self.assertEqual(result["unresolved_cents"], 10000)
        self.assertEqual([row["contribution_cents"] for row in result["scenarios"]], [30000, 32000, 34000])
        self.assertEqual([row["saving_cents"] for row in result["scenarios"]], [0, 2000, 4000])
        self.assertEqual(result["scenarios"][0]["contributions_needed"], 6)
        absent = preview_history(self.store, self.draft(monthly_income=""), self.today)
        self.assertIsNone(absent["scenarios"][0]["contribution_cents"])
        negative = preview_history(self.store, self.draft(monthly_income="100,00"), self.today)
        self.assertEqual(negative["scenarios"][0]["contribution_cents"], 0)
        self.assertLess(negative["scenarios"][0]["surplus_cents"], 0)

    def test_save_is_explicit_scoped_and_does_not_change_transactions_or_plan(self):
        row = self.add("2026-09", "Outros")
        draft = self.draft()
        draft["roles"]["Outros"] = "essential"
        with self.assertRaises(ValidationError):
            save_history_review(self.store, {**draft, "acknowledged": False}, self.today)
        preview_history(self.store, draft, self.today)
        before = self.store.get(row["id"])
        saved = save_history_review(self.store, draft, self.today)
        loaded = history_insights(self.store, today=self.today)
        self.assertEqual(loaded["categories"][0]["role"], "essential")
        self.assertTrue(loaded["categories"][0]["reviewed"])
        self.assertEqual(loaded["preferences"]["income_cents"], 100000)
        self.assertIsNone(history_insights(self.store, "business", today=self.today)["preferences"]["income_cents"])
        self.assertEqual(self.store.get(row["id"]), before)
        with self.store._connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM reserve_plans").fetchone()[0], 0)
        self.assertNotEqual(draft["signature"], saved["analysis"]["signature"])
        with self.assertRaisesRegex(ValidationError, "histórico mudou"):
            save_history_review(self.store, draft, self.today)

    def test_history_change_invalidates_review_and_future_pending_stays_separate(self):
        self.add("2026-09", "Mercado")
        draft = self.draft()
        self.add("2026-09", "Mercado", "50,00")
        with self.assertRaisesRegex(ValidationError, "histórico mudou"):
            save_history_review(self.store, draft, self.today)
        self.add("2026-10", "Lazer", "90,00", invoice="2026-11", pending=True)
        analysis = history_insights(self.store, today=self.today)
        self.assertEqual(analysis["average_spending_cents"], 15000)
        self.assertEqual(analysis["future_invoices"], [{"month":"2026-11","amount_cents":9000,"pending_cents":9000}])

    def test_invalid_inputs_are_rejected_without_saving(self):
        self.add("2026-09", "Mercado")
        draft = self.draft()
        for changes in [{"roles": {"Mercado": []}}, {"roles": {}}, {"months": 3.0}, {"scope": "all"},
                        {"buffer_percent": True}, {"buffer_percent": 51}, {"monthly_income": "-100"},
                        {"reserved_balance": "NaN"}, {"target_months": True}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                save_history_review(self.store, {**draft, **changes}, self.today)
        with self.store._connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM plan_history_reviews").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
