import tempfile
import unittest
from datetime import date
from pathlib import Path

from finance import FinanceStore, ValidationError
from planning import (budget_overview, calendar_overview, data_health, delete_budget,
                      delete_commitment, save_budget, save_commitment,
                      save_card_due_rule, delete_card_due_rule)


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.directory.name) / "test.db")

    def tearDown(self):
        self.directory.cleanup()

    def test_budget_counts_purchases_by_date_and_scope_without_card_payment(self):
        save_budget(self.store, {"scope": "personal", "category": "Mercado", "limit": "100,00"})
        for category, scope, amount, excluded in [
            ("Mercado", "personal", "40,00", False),
            ("Mercado", "business", "90,00", False),
            ("Pagamento de fatura", "personal", "40,00", False),
            ("Mercado", "personal", "20,00", True),
        ]:
            self.store.add({"kind": "expense", "description": "Exemplo", "category": category,
                            "scope": scope, "amount": amount, "occurred_on": "2026-10-03",
                            "excluded": excluded})
        data = budget_overview(self.store, "2026-10", "personal")
        self.assertEqual((data["budgets"][0]["spent_cents"], data["budgets"][0]["limit_cents"]),
                         (4000, 10000))
        delete_budget(self.store, "personal", "Mercado")
        self.assertEqual(budget_overview(self.store, "2026-10", "personal")["budgets"], [])
        with self.assertRaises(ValidationError):
            save_budget(self.store, {"scope": "personal", "category": "Mercado", "limit": "0"})

    def test_health_merges_overlapping_coverage_and_flags_manual_invoice(self):
        self.store.save_account_statement("Conta exemplo", "personal",
                                          {"starts_on": "2026-10-01", "ends_on": "2026-10-05"}, [])
        self.store.save_account_statement("Conta exemplo", "personal",
                                          {"starts_on": "2026-10-05", "ends_on": "2026-10-09"}, [])
        self.store.add_card_purchase({"bank": "inter", "account": "cartão teste", "scope": "personal",
                                      "invoice_month": "2026-10", "occurred_on": "2026-10-04",
                                      "description": "Loja", "amount": "10,00", "category": "Compras"})
        health = data_health(self.store, "2026-10", "personal", date(2026, 10, 13))
        self.assertEqual((health["accounts"][0]["covered_days"], health["accounts"][0]["expected_days"]),
                         (9, 13))
        self.assertEqual(health["invoices"][0]["status"], "manual")
        self.assertTrue(health["has_gaps"])

    def test_calendar_clamps_month_end_and_projects_from_last_balance(self):
        self.store.save_account_balance("Conta exemplo", "personal", "2026-01-30", 10000)
        outgoing = save_commitment(self.store, {"scope": "personal", "kind": "expense",
                                                "description": "Fatura", "amount": "30,00", "due_day": 31,
                                                "starts_month": "2026-02"})
        save_commitment(self.store, {"scope": "personal", "kind": "income",
                                     "description": "Receita prevista", "amount": "50,00", "due_day": 10,
                                     "starts_month": "2026-02"})
        overview = calendar_overview(self.store, "personal", date(2026, 2, 1), days=35)
        self.assertEqual([item["due_on"] for item in overview["events"]],
                         ["2026-02-10", "2026-02-28"])
        self.assertEqual(overview["projected_balance_cents"], 12000)
        delete_commitment(self.store, outgoing["id"])
        self.assertEqual(len(calendar_overview(self.store, "personal", date(2026, 2, 1))["commitments"]), 1)

    def test_card_due_rule_uses_registered_invoice_amount_and_flags_partial(self):
        self.store.add_card_purchase({"bank": "inter", "account": "cartão teste", "scope": "personal",
                                      "invoice_month": "2026-10", "occurred_on": "2026-10-04",
                                      "description": "Loja", "amount": "21,00", "category": "Compras"})
        rule = save_card_due_rule(self.store, {"source": "Inter · cartão teste", "scope": "personal",
                                               "due_day": 5, "month_offset": 1})
        overview = calendar_overview(self.store, "personal", date(2026, 10, 13))
        event = next(item for item in overview["events"] if item["source_type"] == "card")
        self.assertEqual((event["due_on"], event["amount_cents"], event["partial"]),
                         ("2026-11-05", 2100, True))
        delete_card_due_rule(self.store, rule["id"])
        self.assertFalse(calendar_overview(self.store, "personal", date(2026, 10, 13))["events"])


if __name__ == "__main__":
    unittest.main()
