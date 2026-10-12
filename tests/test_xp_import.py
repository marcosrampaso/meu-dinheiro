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
