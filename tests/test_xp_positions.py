import base64
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from finance import FinanceStore, ValidationError
from xp_positions import parse_xp_positions


def sample_xlsx(total="R$ 32,00"):
    rows = [
        {"F": "Conta: 123 | 13/10/2026, 10:25"},
        {},
        {"A": "Carteira", "B": "Total investido"},
        {"A": total, "C": "R$ 0,00"},
        {"A": "Ações"},
        {"A": "ABCD11", "B": "R$ 21,00", "E": "R$ 10,00", "G": "2"},
        {"A": "Fundos de Investimentos"},
        {"A": "Fundo Exemplo", "C": "R$ 8,00", "E": "R$ 7,00", "F": "R$ 7,90"},
        {"A": "Fundos Imobiliários"},
        {"A": "EFGH11", "B": "R$ 2,00", "D": "R$ 1,50", "E": "R$ 2,00", "F": "1"},
        {"A": "Dividendos, proventos e outras distribuições"},
        {"A": "Proventos"},
        {"A": "EFGH11", "D": "R$ 1,00"},
    ]
    xml_rows = []
    for number, row in enumerate(rows, 1):
        cells = "".join(
            f'<c r="{column}{number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            for column, value in row.items()
        )
        xml_rows.append(f'<row r="{number}">{cells}</row>')
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + '</sheetData></worksheet>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", xml)
        archive.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
    return base64.b64encode(output.getvalue()).decode()


class XpPositionTests(unittest.TestCase):
    def test_snapshot_reconciles_assets_and_receivable_without_double_counting(self):
        snapshot = parse_xp_positions("PosicaoDetalhada.xlsx", sample_xlsx())
        self.assertEqual(snapshot["total_cents"], 3200)
        self.assertEqual(snapshot["assets_cents"], 3100)
        self.assertEqual(snapshot["receivables_cents"], 100)
        self.assertEqual(snapshot["assets"][0]["cost_cents"], 2000)
        with tempfile.TemporaryDirectory() as directory:
            store = FinanceStore(Path(directory) / "finance.db")
            store.import_xp_positions(snapshot)
            self.assertTrue(store.import_xp_positions(snapshot)["replaced"])
            current = store.investment_overview("2026-10", 3)
            self.assertEqual(current["position"]["total_cents"], 3200)
            self.assertEqual(len(current["position"]["assets"]), 3)
            self.assertEqual(current["total_deposited_cents"], 0)
            self.assertIsNone(store.investment_overview("2026-09", 3)["position"])

    def test_mismatched_total_is_rejected(self):
        with self.assertRaises(ValidationError):
            parse_xp_positions("PosicaoDetalhada.xlsx", sample_xlsx("R$ 31,00"))


if __name__ == "__main__":
    unittest.main()
