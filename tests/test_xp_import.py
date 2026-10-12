import tempfile
import unittest
from pathlib import Path

from finance import FinanceStore, ValidationError
from xp_import import parse_xp_statement


XP_CSV = """Data;Hora;Descricao;Valor;Saldo
09/10/26;10:19:40;Transferência enviada para a conta investimento;-R$ 123,45;R$ 0,00
09/10/26;10:19:25;Pix recebido de Ana Exemplo Silva;R$ 123,45;R$ 123,45
07/10/26;11:42:11;Pix recebido de Ana Exemplo Silva;R$ 75,00;R$ 75,00
"""


class XpImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.directory.name) / "financas.db")

    def tearDown(self):
        self.directory.cleanup()

    def test_import_matches_unique_inter_pix_without_adding_cash_flow(self):
        target = self.store.add({"kind": "expense", "description": "Ana Exemplo Silva",
                                 "category": "Importado", "amount": "123,45",
                                 "occurred_on": "2026-10-09", "scope": "business"})
        with self.store._connect() as connection:
            connection.execute("UPDATE transactions SET source = 'Inter · conta PJ' WHERE id = ?", (target["id"],))
        self.store.add({"kind": "expense", "description": "Ana Exemplo Silva",
                        "category": "Importado", "amount": "200,00",
                        "occurred_on": "2026-10-09", "scope": "business"})
        entries = parse_xp_statement("xp.csv", XP_CSV)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["amount_cents"], -12345)
        self.assertEqual(entries[0]["balance_cents"], 0)
        self.assertEqual(len(self.store.preview_xp_statement(entries)["matches"]), 1)
        self.assertEqual(self.store.import_xp_statement(entries),
                         {"imported": 3, "skipped": 0, "matched_transfers": 1})
        self.assertEqual(self.store.import_xp_statement(entries),
                         {"imported": 0, "skipped": 3, "matched_transfers": 0})
        self.assertEqual(self.store.get(target["id"])["category"], "Transferência para investimento")
        self.assertEqual(self.store.summary("2026-10", "all")["income_cents"], 0)
        self.assertEqual(self.store.summary("2026-10", "all")["expense_cents"], 20000)
        self.assertIsNone(self.store.summary("2026-10", "all")["cash_balance_cents"])
        overview = self.store.investment_overview("2026-10", 3)
        self.assertEqual(overview["total_deposited_cents"], 19845)
        self.assertEqual(overview["matched_count"], 1)
        self.assertEqual(overview["matched_cents"], 12345)
        self.assertEqual([month["deposits_cents"] for month in overview["months"]],
                         [None, None, 19845])
        self.assertEqual(overview["months"][-1]["cumulative_cents"], 19845)
        self.assertEqual(overview["movement_count"], 3)

    def test_investment_overview_accumulates_recorded_deposits_by_month(self):
        entries = parse_xp_statement("xp.csv", """Data;Hora;Descricao;Valor;Saldo
18/08/26;12:00:00;Pix recebido de Ana Exemplo Silva;R$ 100,00;R$ 100,00
01/09/26;12:00:00;Pix recebido de Ana Exemplo Silva;R$ 200,00;R$ 200,00
02/10/26;12:00:00;Pix recebido de Ana Exemplo Silva;R$ 300,00;R$ 300,00
02/10/26;12:01:00;Transferência enviada para conta investimento;-R$ 300,00;R$ 0,00
""")
        self.store.import_xp_statement(entries)
        overview = self.store.investment_overview("2026-10", 3)
        self.assertEqual([row["deposits_cents"] for row in overview["months"]],
                         [10000, 20000, 30000])
        self.assertEqual([row["cumulative_cents"] for row in overview["months"]],
                         [10000, 30000, 60000])
        self.assertEqual(overview["total_deposited_cents"], 60000)
        self.assertEqual(overview["period_deposited_cents"], 60000)
        self.assertEqual(self.store.investment_overview("2026-09", 2)["total_deposited_cents"], 30000)
        self.assertEqual(self.store.summary("2026-10")["income_cents"], 0)

    def test_ambiguous_matches_are_not_changed(self):
        for _ in range(2):
            target = self.store.add({"kind": "expense", "description": "Ana Exemplo Silva",
                                     "category": "Importado", "amount": "123,45",
                                     "occurred_on": "2026-10-09", "scope": "business"})
            with self.store._connect() as connection:
                connection.execute("UPDATE transactions SET source = 'Inter · conta PJ' WHERE id = ?", (target["id"],))
        entries = parse_xp_statement("xp.csv", XP_CSV)
        self.assertEqual(self.store.preview_xp_statement(entries)["matches"], [])
        self.assertEqual(self.store.import_xp_statement(entries)["matched_transfers"], 0)

    def test_rejects_other_csv_layout(self):
        with self.assertRaises(ValidationError):
            parse_xp_statement("other.csv", "Data;Valor\n09/10/26;10,00\n")


if __name__ == "__main__":
    unittest.main()
