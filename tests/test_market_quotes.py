import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from finance import FinanceStore, ValidationError
from market_quotes import MarketQuotes, asset_source, fetch_brapi, fetch_cvm


class MarketQuotesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store = FinanceStore(root / "financas.db")
        self.market = MarketQuotes(self.store, root / "brapi-token")
        (root / "market-assets.json").write_text(json.dumps({"fund_cnpjs": {
            "Fundo Alpha FIRF RL": "12.345.678/0001-90",
            "Fundo Beta FIA RL": "98.765.432/0001-10",
        }}), encoding="utf-8")
        self.store.import_xp_positions({
            "as_of": "2026-10-13", "total_cents": 39000, "assets_cents": 39000,
            "receivables_cents": 0, "cash_cents": 0,
            "assets": [
                {"name": "Fundo Alpha FIRF RL", "category": "Fundos de Investimentos",
                 "value_cents": 10000, "cost_cents": 9500, "quantity": None, "net_cents": 9900},
                {"name": "Fundo Beta FIA RL", "category": "Fundos de Investimentos",
                 "value_cents": 10000, "cost_cents": 10000, "quantity": None, "net_cents": 9900},
                {"name": "ABCD11", "category": "Ações", "value_cents": 14000,
                 "cost_cents": 13000, "quantity": "14", "net_cents": None},
                {"name": "EFGH11", "category": "Fundos Imobiliários", "value_cents": 5000,
                 "cost_cents": 4900, "quantity": "2", "net_cents": None},
            ],
        })

    def tearDown(self):
        self.directory.cleanup()

    def test_source_mapping_and_cvm_csv_parsing(self):
        self.assertEqual(asset_source("ABCD11", "Ações", self.market.fund_cnpjs()), ("brapi", "ABCD11"))
        self.assertEqual(asset_source("Fundo Beta FIA RL", "Fundos de Investimentos", self.market.fund_cnpjs()),
                         ("cvm", "98.765.432/0001-10"))
        csv_text = ("TP_FUNDO_CLASSE;CNPJ_FUNDO_CLASSE;ID_SUBCLASSE;DT_COMPTC;VL_QUOTA\n"
                    "FI;12.345.678/0001-90;;2026-10-09;1.32543341\n"
                    "FI;12.345.678/0001-90;;2026-10-14;1.326\n"
                    "FI;11.111.111/0001-11;;2026-10-09;9\n")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("inf_diario_fi_202610.csv", csv_text)
        with patch("market_quotes._download", return_value=buffer.getvalue()):
            quotes = fetch_cvm("202610", {"12.345.678/0001-90"}, date(2026, 10, 13))
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0]["price"], "1.32543341")

    def test_brapi_requires_matching_symbol_and_market_time(self):
        response = {"results": [{"requestedSymbol": "ABCD11", "symbol": "ABCD11",
                                 "data": {"regularMarketPrice": 101.25,
                                          "regularMarketTime": "2026-10-13T15:00:00Z"}}]}
        with patch("market_quotes._download", return_value=json.dumps(response).encode()) as download:
            quote = fetch_brapi("ABCD11", "private-token", date(2026, 10, 13))
        self.assertEqual((quote["price"], quote["quote_date"]), ("101.25", "2026-10-13"))
        self.assertEqual(download.call_args.args[0], "https://brapi.dev/api/v2/stocks/quote?symbols=ABCD11")
        self.assertEqual(download.call_args.args[1]["Authorization"], "Bearer private-token")

    def test_estimates_require_all_assets_for_total_and_never_expose_token(self):
        self.market.save_token("private-token")
        self.assertEqual(os.stat(self.market.token_path).st_mode & 0o777, 0o600)
        quotes = [
            ("cvm", "12.345.678/0001-90", "2026-10-09", "1"),
            ("cvm", "12.345.678/0001-90", "2026-10-14", "1.1"),
            ("cvm", "98.765.432/0001-10", "2026-10-09", "100"),
            ("cvm", "98.765.432/0001-10", "2026-10-14", "105"),
            ("brapi", "ABCD11", "2026-10-14", "11"),
        ]
        self.store.save_market_quotes([{"source": source, "identifier": identifier,
                                        "quote_date": when, "price": price,
                                        "checked_at": "2026-10-14T12:00:00+00:00"}
                                       for source, identifier, when, price in quotes])
        with patch("market_quotes.date") as clock:
            clock.today.return_value = date(2026, 10, 14)
            clock.fromisoformat.side_effect = date.fromisoformat
            overview = self.market.overview()
        self.assertFalse(overview["complete"])
        self.assertNotIn("estimated_gain_cents", overview)
        self.assertNotIn("private-token", json.dumps(overview))
        by_name = {asset["name"]: asset for asset in overview["assets"]}
        self.assertEqual(by_name["Fundo Alpha FIRF RL"]["estimated_value_cents"], 11000)
        self.assertEqual(by_name["Fundo Beta FIA RL"]["estimated_value_cents"], 10500)
        self.assertEqual(by_name["ABCD11"]["estimated_value_cents"], 15400)
        self.assertIsNone(by_name["EFGH11"]["estimated_value_cents"])
        self.store.save_market_quotes([{"source": "brapi", "identifier": "EFGH11",
                                        "quote_date": "2026-10-14", "price": "26",
                                        "checked_at": "2026-10-14T12:00:00+00:00"}])
        with patch("market_quotes.date") as clock:
            clock.today.return_value = date(2026, 10, 14)
            clock.fromisoformat.side_effect = date.fromisoformat
            complete = self.market.overview()
        self.assertTrue(complete["complete"])
        self.assertEqual(complete["estimated_assets_cents"], 42100)
        self.assertEqual(complete["estimated_gain_cents"], 4700)

    def test_token_validation_and_removal(self):
        with self.assertRaises(ValidationError):
            self.market.save_token("bad token")
        self.market.save_token("valid-token-123")
        self.assertTrue(self.market.token_configured())
        self.market.delete_token()
        self.assertFalse(self.market.token_configured())


if __name__ == "__main__":
    unittest.main()
