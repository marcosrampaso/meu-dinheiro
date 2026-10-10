import tempfile
import unittest
from pathlib import Path

from finance import FinanceStore, NotFoundError, ValidationError, parse_amount


def transaction(**changes):
    data = {
        "kind": "expense",
        "description": "Mercado",
        "amount": "123,45",
        "category": "Alimentação",
        "occurred_on": "2026-10-09",
    }
    data.update(changes)
    return data


class FinanceStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.directory.name) / "finance.db")

    def tearDown(self):
        self.directory.cleanup()

    def test_amount_accepts_brazilian_format_and_rejects_invalid_values(self):
        self.assertEqual(parse_amount("R$ 1.234,56"), 123456)
        self.assertEqual(parse_amount("12.50"), 1250)
        for value in ("0", "-1", "abc", "1,234", "1.234.56"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_amount(value)

    def test_create_update_delete_and_month_boundaries(self):
        first = self.store.add(transaction())
        self.store.add(transaction(description="Setembro", occurred_on="2026-09-30"))
        self.store.add(transaction(description="Novembro", occurred_on="2026-11-01"))
        self.assertEqual([item["id"] for item in self.store.list_month("2026-10")], [first["id"]])
        updated = self.store.update(first["id"], transaction(amount="30,00", category="Casa"))
        self.assertEqual(updated["amount_cents"], 3000)
        self.assertEqual(updated["category"], "Casa")
        self.store.delete(first["id"])
        self.assertEqual(self.store.list_month("2026-10"), [])
        with self.assertRaises(NotFoundError):
            self.store.get(first["id"])

    def test_summary_calculates_income_expenses_balance_and_categories(self):
        self.store.add(transaction(kind="income", description="Salário", amount="3000,00", category="Salário"))
        self.store.add(transaction(amount="100,00"))
        self.store.add(transaction(description="Ônibus", amount="25,00", category="Transporte"))
        self.store.add(transaction(description="Padaria", amount="20,00"))
        summary = self.store.summary("2026-10")
        self.assertEqual(summary["income_cents"], 300000)
        self.assertEqual(summary["expense_cents"], 14500)
        self.assertEqual(summary["balance_cents"], 285500)
        self.assertEqual(summary["expense_categories"], [
            {"category": "Alimentação", "amount_cents": 12000},
            {"category": "Transporte", "amount_cents": 2500},
        ])

    def test_personal_and_business_summaries_stay_separate(self):
        self.store.add(transaction(amount="10,00", scope="personal"))
        business = self.store.add(transaction(amount="30,00", scope="business"))
        self.assertEqual(self.store.summary("2026-10")["expense_cents"], 1000)
        self.assertEqual(self.store.summary("2026-10", "business")["expense_cents"], 3000)
        self.assertEqual(self.store.summary("2026-10", "all")["expense_cents"], 4000)
        self.assertEqual(len(self.store.list_month("2026-10", "business")), 1)
        self.assertEqual(self.store.update(business["id"], transaction(amount="40,00"))["scope"], "business")
        self.assertEqual(self.store.summary("2026-10", "business")["expense_cents"], 4000)

    def test_matching_pj_to_personal_transfer_counts_once(self):
        self.store.add(transaction(kind="income", scope="business", amount="7500,00", description="Cliente"))
        self.store.add(transaction(kind="expense", scope="business", amount="4100,00",
                                   category="Importado", description="123 ANA EXEMPLO SILVA"))
        self.store.add(transaction(kind="income", scope="personal", amount="4100,00",
                                   category="Importado", description="999 ANA EXEMPLO SILVA"))
        self.assertEqual(self.store.reconcile_internal_transfers(), 1)
        self.assertEqual(self.store.reconcile_internal_transfers(), 0)
        self.assertEqual(self.store.summary("2026-10", "all")["income_cents"], 750000)
        self.assertEqual(self.store.summary("2026-10", "all")["expense_cents"], 0)
        self.assertEqual(self.store.summary("2026-10", "personal")["income_cents"], 0)

    def test_balance_snapshots_do_not_claim_a_future_balance_for_past_months(self):
        self.store.save_account_balance("Inter · conta corrente", "personal", "2026-10-07", 1836)
        self.assertIsNone(self.store.summary("2026-09")["cash_balance_cents"])
        self.assertEqual(self.store.summary("2026-10")["cash_balance_cents"], 1836)

    def test_card_purchase_and_bill_payment_are_shown_without_double_counting_cash_outflow(self):
        items = [
            {"kind": "expense", "description": "Compra", "category": "Importado",
             "amount_cents": 10000, "occurred_on": "2026-10-08", "excluded": False,
             "source": "Inter · cartão principal", "import_key": "a" * 64,
             "invoice_month": "2026-10", "scope": "personal"},
            {"kind": "expense", "description": "Pagamento de fatura",
             "category": "Pagamento de fatura", "amount_cents": 10000,
             "occurred_on": "2026-10-09", "excluded": True,
             "source": "Inter · conta corrente", "import_key": "b" * 64,
             "invoice_month": None, "scope": "personal"},
        ]
        self.store.add_imported(items)
        summary = self.store.summary("2026-10")
        self.assertEqual(summary["expense_cents"], 10000)
        self.assertEqual(summary["card_payment_cents"], 10000)
        self.assertEqual(summary["card_purchases_cents"], 10000)

    def test_rejects_invalid_fields_and_month(self):
        for data in (
            transaction(kind="other"),
            transaction(description=" "),
            transaction(category=" "),
            transaction(description=None),
            transaction(occurred_on="2026-02-30"),
            transaction(amount="0"),
        ):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                self.store.add(data)
        with self.assertRaises(ValidationError):
            self.store.list_month("2026-13")

    def test_analytics_marks_missing_and_partial_months_and_separates_accounts(self):
        self.store.add(transaction(kind="income", description="Cliente", amount="7500,00",
                                   category="Receita", scope="business"))
        self.store.save_account_statement(
            "Inter · conta PJ", "business",
            {"starts_on": "2026-10-01", "ends_on": "2026-10-09"},
            [{"as_of": "2026-10-09", "amount_cents": 93261}],
        )
        self.store.save_account_statement(
            "Inter · conta corrente", "personal",
            {"starts_on": "2026-09-09", "ends_on": "2026-10-09"},
            [{"as_of": "2026-10-07", "amount_cents": 1836}],
        )
        chart = self.store.analytics("2026-11", "all", 3)
        self.assertEqual([row["coverage"] for row in chart["cashflow"]],
                         ["partial", "partial", "none"])
        self.assertEqual(chart["cashflow"][1]["income_cents"], 750000)
        self.assertIsNone(chart["cashflow"][2]["income_cents"])
        self.assertEqual(len(chart["balance_series"]), 2)
        self.assertEqual(self.store.analytics("2026-10", "personal", 1)["cashflow"][0]["income_cents"], 0)
        with self.assertRaises(ValidationError):
            self.store.analytics("2026-10", "all", 25)

    def test_reviewed_categories_include_card_purchases_without_recounting_bill_payment(self):
        account = self.store.add(transaction(category="Importado", amount="30,00"))
        self.store.add(transaction(category="Pagamento de fatura", amount="80,00", excluded=True))
        self.store.add_imported([{
            "kind": "expense", "description": "Restaurante", "category": "Importado",
            "amount_cents": 10000, "occurred_on": "2026-10-09", "excluded": False,
            "source": "Inter · cartão", "import_key": "c" * 64,
            "invoice_month": "2026-10", "scope": "personal",
        }])
        rows, count = self.store.uncategorized_purchases("personal", "2026-10")
        self.assertEqual(count, 2)
        self.assertEqual(self.store.summary("2026-10")["purchase_total_cents"], 13000)
        changes = [{"id": row["id"], "category": "Alimentação" if row["id"] != account["id"] else "Mercado"}
                   for row in rows]
        self.assertEqual(self.store.apply_purchase_categories(changes), {"applied": 2, "skipped": 0})
        self.assertEqual(self.store.apply_purchase_categories(changes), {"applied": 0, "skipped": 2})
        summary = self.store.summary("2026-10")
        self.assertEqual(summary["expense_cents"], 11000)
        self.assertEqual(summary["card_purchases_cents"], 10000)
        self.assertEqual(summary["purchase_categories"], [
            {"category": "Alimentação", "amount_cents": 10000},
            {"category": "Mercado", "amount_cents": 3000},
        ])
        with self.assertRaises(ValidationError):
            self.store.apply_purchase_categories([{"id": account["id"], "category": "Inexistente"}])


if __name__ == "__main__":
    unittest.main()
