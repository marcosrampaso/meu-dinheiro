import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path

from finance import FinanceStore, ValidationError
from importers import (account_balance_from_csv, account_daily_balances_from_csv,
                       account_period_from_csv, inspect_import, prepare_import)
from pdf_import import inter_statement_totals, statement_rows


CARD_CSV = "Data;Descrição;Valor\n09/10/2026;Mercado;123,45\n10/10/2026;Estorno;R$ -20,00\n"
ACCOUNT_CSV = "Data;Histórico;Débito;Crédito\n09/10/2026;Aluguel;1.200,00;\n10/10/2026;Salário;;3.000,00\n"
OFX = """OFXHEADER:100
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN><DTPOSTED>20261009<TRNAMT>-50.00<FITID>fit-1<NAME>PIX ENVIADO</STMTTRN>
<STMTTRN><DTPOSTED>20261010<TRNAMT>200.00<FITID>fit-2<NAME>PIX RECEBIDO</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""
ACCOUNT_WITH_PREAMBLE = """Extrato Conta Corrente
Conta ;000000
Período ;09/09/2026 a 09/10/2026
Saldo ;18,36

Data Lançamento;Histórico;Descrição;Valor;Saldo
09/10/2026;Pagamento efetuado;Pagamento de fatura;-100,00;18,36
08/10/2026;Compra no débito;Mercado;-25,00;118,36
07/10/2026;Aplicação;CDB;-40,00;143,36
"""


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.directory.name) / "finance.db")

    def tearDown(self):
        self.directory.cleanup()

    def test_inter_card_csv_preview_and_reimport_deduplication(self):
        inspected = inspect_import("fatura.csv", CARD_CSV)
        self.assertEqual(inspected["count"], 2)
        mapping = inspected["suggested_mapping"]
        items = prepare_import({
            "filename": "fatura.csv", "content": CARD_CSV, "bank": "inter",
            "mode": "card", "account": "cartão principal", "mapping": mapping,
        })
        self.assertEqual([(item["kind"], item["amount_cents"]) for item in items], [
            ("expense", 12345), ("income", 2000),
        ])
        self.assertEqual(self.store.add_imported(items), {"imported": 2, "skipped": 0})
        self.assertEqual(self.store.add_imported(items), {"imported": 0, "skipped": 2})
        self.assertEqual(self.store.summary("2026-10")["balance_cents"], -10345)

    def test_account_csv_separate_debit_credit_and_excluded_bill_payment(self):
        mapping = inspect_import("conta.csv", ACCOUNT_CSV)["suggested_mapping"]
        items = prepare_import({
            "filename": "conta.csv", "content": ACCOUNT_CSV, "bank": "bb",
            "mode": "account", "account": "conta principal", "mapping": mapping,
        })
        self.assertEqual([(item["kind"], item["amount_cents"]) for item in items], [
            ("expense", 120000), ("income", 300000),
        ])
        self.store.add_imported(items)
        first = self.store.list_month("2026-10")[-1]
        self.store.update(first["id"], {
            "kind": "expense", "description": "Pagamento de fatura", "amount": "1200,00",
            "category": "Transferência", "occurred_on": "2026-10-09", "excluded": True,
        })
        summary = self.store.summary("2026-10")
        self.assertEqual(summary["expense_cents"], 0)
        self.assertEqual(summary["income_cents"], 300000)

    def test_account_csv_skips_preamble_and_excludes_card_bill_payment(self):
        inspected = inspect_import("extrato.csv", ACCOUNT_WITH_PREAMBLE)
        self.assertEqual(inspected["count"], 3)
        self.assertEqual(inspected["account_balance"], {"as_of": "2026-10-09", "amount_cents": 1836})
        self.assertEqual(inspected["suggested_mapping"]["description"], 2)
        items = prepare_import({
            "filename": "extrato.csv", "content": ACCOUNT_WITH_PREAMBLE,
            "bank": "inter", "mode": "account", "account": "conta",
            "mapping": inspected["suggested_mapping"],
        })
        self.assertEqual([item["excluded"] for item in items], [True, False, True])
        self.store.add_imported(items)
        summary = self.store.summary("2026-10")
        self.assertEqual(summary["expense_cents"], 12500)
        self.assertEqual(summary["card_payment_cents"], 10000)
        self.store.save_account_balance(items[0]["source"], "personal", **account_balance_from_csv(ACCOUNT_WITH_PREAMBLE))
        self.assertEqual(self.store.summary("2026-10")["cash_balance_cents"], 1836)

    def test_csv_statement_period_and_daily_balances(self):
        content = ACCOUNT_WITH_PREAMBLE + "07/10/2026;Aplicação;Outro;-10,00;153,36\n"
        self.assertEqual(account_period_from_csv(content),
                         {"starts_on": "2026-09-09", "ends_on": "2026-10-09"})
        self.assertEqual(account_daily_balances_from_csv(content), [
            {"as_of": "2026-10-07", "amount_cents": 14336},
            {"as_of": "2026-10-08", "amount_cents": 11836},
            {"as_of": "2026-10-09", "amount_cents": 1836},
        ])
        self.assertEqual(account_daily_balances_from_csv(ACCOUNT_CSV), [])

    def test_business_import_is_separate_from_personal(self):
        inspected = inspect_import("extrato.csv", ACCOUNT_WITH_PREAMBLE)
        payload = {
            "filename": "extrato.csv", "content": ACCOUNT_WITH_PREAMBLE,
            "bank": "inter", "mode": "account", "account": "conta",
            "mapping": inspected["suggested_mapping"],
        }
        personal = prepare_import(payload)
        business = prepare_import({**payload, "scope": "business"})
        self.assertEqual(self.store.add_imported(personal)["imported"], 3)
        self.assertEqual(self.store.add_imported(business)["imported"], 3)
        self.assertEqual(self.store.add_imported(business)["skipped"], 3)
        self.assertEqual(self.store.summary("2026-10")["expense_cents"], 12500)
        self.assertEqual(self.store.summary("2026-10", "business")["expense_cents"], 12500)

    def test_cashback_is_not_counted_as_salary_or_business_revenue(self):
        content = "Data;Histórico;Descrição;Valor\n09/10/2026;Cashback;Bônus;3,00\n"
        inspected = inspect_import("extrato.csv", content)
        items = prepare_import({"filename": "extrato.csv", "content": content, "bank": "inter",
                                "mode": "account", "account": "conta", "mapping": inspected["suggested_mapping"]})
        self.assertTrue(items[0]["excluded"])
        self.assertEqual(items[0]["category"], "Cashback")

    def test_explicit_xp_destination_is_investment_but_own_name_is_ambiguous(self):
        content = ("Data;Histórico;Descrição;Valor\n"
                   "09/10/2026;Pix enviado;XP Investimentos CCTVM;-250,00\n"
                   "09/10/2026;Pix enviado;Titular Exemplo;-350,00\n"
                   "09/10/2026;Pix enviado;Banco XP S.A.;-75,00\n")
        inspected = inspect_import("conta.csv", content)
        items = prepare_import({"filename": "conta.csv", "content": content,
                                "bank": "inter", "mode": "account", "account": "conta PJ",
                                "scope": "business", "mapping": inspected["suggested_mapping"]})
        self.assertEqual([item["category"] for item in items],
                         ["Transferência para investimento", "Importado", "Transferência para investimento"])
        self.assertEqual([item["excluded"] for item in items], [True, False, True])

    def test_returned_pix_pair_is_excluded_without_treating_it_as_revenue(self):
        content = ("Data;Histórico;Descrição;Valor\n"
                   "21/09/2026;Pix enviado;Loja Exemplo;-200,00\n"
                   "21/09/2026;Pix enviado devolvido;Loja Exemplo;200,00\n"
                   "21/09/2026;Pix enviado;Titular Exemplo;-200,00\n")
        inspected = inspect_import("conta.csv", content)
        items = prepare_import({"filename": "conta.csv", "content": content,
                                "bank": "inter", "mode": "account", "account": "conta PJ",
                                "scope": "business", "mapping": inspected["suggested_mapping"]})
        self.assertEqual([item["category"] for item in items],
                         ["Estorno de Pix", "Estorno de Pix", "Importado"])
        self.store.add_imported([{**item, "category": "Importado", "excluded": False} for item in items])
        self.assertEqual(self.store.summary("2026-09", "business")["income_cents"], 20000)
        self.assertEqual(self.store.add_imported(items), {"imported": 0, "skipped": 3})
        self.assertEqual(self.store.summary("2026-09", "business")["income_cents"], 0)
        self.assertEqual(self.store.summary("2026-09", "business")["expense_cents"], 20000)

    def test_ofx_parses_transactions_and_uses_ids_for_deduplication(self):
        self.assertEqual(inspect_import("extrato.ofx", OFX)["count"], 2)
        items = prepare_import({
            "filename": "extrato.ofx", "content": OFX, "bank": "bb",
            "mode": "account", "account": "conta", "mapping": {},
        })
        self.assertEqual([(item["kind"], item["amount_cents"]) for item in items], [
            ("expense", 5000), ("income", 20000),
        ])
        self.assertEqual(self.store.add_imported(items)["imported"], 2)

    def test_expense_sign_can_be_changed_after_preview(self):
        mapping = inspect_import("fatura.csv", CARD_CSV)["suggested_mapping"]
        items = prepare_import({
            "filename": "fatura.csv", "content": CARD_CSV, "bank": "inter",
            "mode": "card", "expense_sign": "negative", "account": "cartão",
            "mapping": mapping,
        })
        self.assertEqual([item["kind"] for item in items], ["income", "expense"])

    def test_invalid_rows_abort_entire_import(self):
        invalid = CARD_CSV + "31/02/2026;Erro;10,00\n"
        mapping = inspect_import("fatura.csv", invalid)["suggested_mapping"]
        with self.assertRaises(ValidationError):
            prepare_import({
                "filename": "fatura.csv", "content": invalid, "bank": "inter",
                "mode": "card", "account": "cartão", "mapping": mapping,
            })
        self.assertEqual(self.store.list_month("2026-10"), [])

    def test_pdf_statement_import_and_invoice_history(self):
        pdf_path = Path(__file__).parent / "fixtures" / "sample_statement.pdf"
        content_base64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
        inspected = inspect_import("inter-2026-10.pdf", content_base64=content_base64)
        self.assertEqual(inspected["count"], 2)
        self.assertEqual(inspected["statement_month"], "2026-10")
        payload = {
            "filename": "inter-2026-10.pdf", "content_base64": content_base64,
            "bank": "inter", "mode": "card", "account": "principal",
            "statement_month": "2026-10",
        }
        items = prepare_import(payload)
        self.assertEqual([item["occurred_on"] for item in items], ["2026-10-09", "2026-09-10"])
        self.assertEqual(self.store.add_imported(items), {"imported": 2, "skipped": 0})
        self.assertEqual(self.store.add_imported(items), {"imported": 0, "skipped": 2})
        history = self.store.invoice_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["invoice_month"], "2026-10")
        self.assertEqual(history[0]["expense_cents"], 17345)

    def test_pdf_requires_real_bytes_and_invoice_month(self):
        with self.assertRaises(ValidationError):
            inspect_import("fatura.pdf", content_base64="not base64")
        pdf_path = Path(__file__).parent / "fixtures" / "sample_statement.pdf"
        content_base64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
        with self.assertRaises(ValidationError):
            prepare_import({
                "filename": "fatura.pdf", "content_base64": content_base64,
                "bank": "inter", "mode": "card", "account": "principal",
            })

    def test_inter_layout_reconciles_and_excludes_advance_from_monthly_summary(self):
        text = """Fatura 06/2026
05 de mai. MERCADO CENTRAL - R$ 10,00
06 de mai. LOJA DE LIVROS - R$ 5,00
07 de mai. CREDITO NO CARTAO + R$ 2,00
08 de mai. PAGAMENTO DA FATURA + R$ 30,00
Despesas do mês R$ 15,00
Valor antecipado R$ 2,00
Fatura atual R$ 13,00
"""
        rows = statement_rows(text)
        self.assertEqual(len(rows), 3)
        totals = inter_statement_totals(text)
        self.assertEqual(totals, {"expenses_cents": 1500, "advanced_cents": 200, "invoice_cents": 1300})
        from unittest.mock import patch
        with patch("importers.extract_pdf_text", return_value=text):
            inspected = inspect_import("fatura-inter-2026-06.pdf", content_base64="mock")
            self.assertTrue(inspected["reconciliation"]["matches"])
            items = prepare_import({
                "filename": "fatura-inter-2026-06.pdf", "content_base64": "mock",
                "bank": "inter", "mode": "card", "account": "principal",
                "statement_month": "2026-06",
            })
        self.assertEqual([item["occurred_on"] for item in items], ["2026-05-05", "2026-05-06", "2026-05-07"])
        self.assertTrue(items[2]["excluded"])
        self.store.add_imported(items)
        self.assertEqual(self.store.summary("2026-05")["income_cents"], 0)
        invoice = self.store.invoice_history()[0]
        self.assertEqual(invoice["expense_cents"] - invoice["credit_cents"], 1300)

    def test_existing_database_gets_new_columns(self):
        path = Path(self.directory.name) / "old.db"
        with sqlite3.connect(path) as connection:
            connection.execute("""CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                description TEXT NOT NULL, category TEXT NOT NULL,
                amount_cents INTEGER NOT NULL, occurred_on TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            connection.execute(
                "INSERT INTO transactions (kind, description, category, amount_cents, occurred_on) "
                "VALUES ('expense', 'Antigo', 'Casa', 1000, '2026-10-01')"
            )
        migrated = FinanceStore(path)
        self.assertTrue(path.with_name(path.name + ".before-import.bak").exists())
        item = migrated.list_month("2026-10")[0]
        self.assertEqual(item["source"], "Manual")
        self.assertEqual(item["excluded"], 0)
        self.assertIsNone(item["invoice_month"])
        self.assertEqual(item["scope"], "personal")
        self.assertEqual(migrated.summary("2026-10")["expense_cents"], 1000)

    def test_previous_import_database_gets_scope_and_backup(self):
        path = Path(self.directory.name) / "previous.db"
        with sqlite3.connect(path) as connection:
            connection.execute("""CREATE TABLE transactions (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, description TEXT NOT NULL,
                category TEXT NOT NULL, amount_cents INTEGER NOT NULL,
                occurred_on TEXT NOT NULL, excluded INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'Manual', import_key TEXT,
                invoice_month TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            connection.execute("""INSERT INTO transactions
                (kind, description, category, amount_cents, occurred_on)
                VALUES ('expense', 'Antigo', 'Casa', 1000, '2026-10-01')""")
        migrated = FinanceStore(path)
        self.assertTrue(path.with_name(path.name + ".before-scope.bak").exists())
        self.assertEqual(migrated.list_month("2026-10")[0]["scope"], "personal")


if __name__ == "__main__":
    unittest.main()
