import json
import unittest
from unittest.mock import patch

from investment_news import ResearchError, _asset_source_match, research_assets


class FakeResponse:
    def __init__(self, payload, streamed_items=False):
        if streamed_items:
            events = [{"type": "response.output_item.done", "item": item} for item in payload["output"]]
            events.append({"type": "response.completed", "response": {"status": "completed", "output": []}})
        else:
            events = [{"type": "response.completed", "response": payload}]
        self.body = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def __iter__(self):
        return iter(self.body.splitlines(keepends=True))


def completed(report, source_urls):
    return {"status": "completed", "output": [
        {"type": "web_search_call", "action": {"sources": [{"url": url} for url in source_urls]}},
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(report)}]},
    ]}


class InvestmentNewsTests(unittest.TestCase):
    def test_fund_reference_must_name_the_actual_fund(self):
        self.assertTrue(_asset_source_match("Fundo Alpha FIRF RL",
                                            "https://www.xpasset.com.br/fundos/fundo-alpha-firf-rl/"))
        self.assertFalse(_asset_source_match("Fundo Beta FIA RL",
                                             "https://conteudos.xpi.com.br/fundos/fundo-gama-fia/"))

    def test_research_sends_asset_names_only_and_keeps_verified_sources(self):
        source = "https://www.b3.com.br/pt_br/mercado-e-titulos/acoes/abcd11.htm"
        report = {"assets": [{
            "asset": "ABCD11", "headline": "Fato recente", "summary": "Resumo do fato.",
            "source_url": source, "published_on": "2026-10-09",
            "short_term": "Observar resultados.", "medium_term": "Acompanhar execução.",
            "long_term": "Acompanhar estratégia.", "risk": "Volatilidade.",
        }], "opportunities": [{"title": "Estudar setor", "thesis": "Tese.",
                              "risk": "Risco.", "source_url": source}]}
        sent = {}

        def fake_urlopen(request, timeout):
            sent["body"] = json.loads(request.data)
            return FakeResponse(completed(report, [source]), streamed_items=True)

        with patch("investment_news.urlopen", side_effect=fake_urlopen):
            result = research_assets(["ABCD11"], "fake-token", "gpt-6-luna")
        self.assertEqual(result["assets"][0]["source_url"], source)
        self.assertEqual(len(result["opportunities"]), 1)
        self.assertEqual(sent["body"]["model"], "gpt-6-luna")
        self.assertEqual(sent["body"]["tool_choice"], "required")
        self.assertEqual(sent["body"]["tools"][0]["type"], "web_search")
        self.assertFalse(sent["body"]["store"])
        self.assertEqual(json.loads(sent["body"]["input"][1]["content"]), {"assets": ["ABCD11"]})
        self.assertNotIn("amount", json.dumps(sent["body"]))

    def test_unverified_articles_are_neutralized_and_opportunities_dropped(self):
        source = "https://www.b3.com.br/noticia.html"
        report = {"assets": [
            {"asset": "ABCD11", "headline": "Verificado", "summary": "Fato.",
             "source_url": source, "published_on": "data ruim", "short_term": "Cenário.",
             "medium_term": "Cenário.", "long_term": "Cenário.", "risk": "Risco."},
            {"asset": "EFGH11", "headline": "Inventado", "summary": "Fato falso.",
             "source_url": "https://example.com/inventado", "published_on": "2026-10-09",
             "short_term": "Compre já.", "medium_term": "Lucro garantido.",
             "long_term": "Lucro garantido.", "risk": "Nenhum."},
        ], "opportunities": [{"title": "Compre já", "thesis": "Lucro garantido.",
                              "risk": "Nenhum.", "source_url": "https://example.com/inventado"}]}
        with patch("investment_news.urlopen", return_value=FakeResponse(completed(report, [source]))):
            result = research_assets(["ABCD11", "EFGH11"], "fake-token", "gpt-6-luna")
        self.assertEqual(result["assets"][0]["published_on"], "")
        self.assertEqual(result["assets"][1]["headline"], "")
        self.assertNotIn("Compre já", json.dumps(result))
        self.assertEqual(result["opportunities"], [])

    def test_no_verifiable_source_cannot_be_saved(self):
        report = {"assets": [{"asset": "ABCD11", "headline": "Rumor", "summary": "Algo.",
                              "source_url": "https://example.com/rumor", "published_on": "",
                              "short_term": "", "medium_term": "", "long_term": "", "risk": ""}],
                  "opportunities": []}
        with patch("investment_news.urlopen", return_value=FakeResponse(completed(report, []))):
            with self.assertRaisesRegex(ResearchError, "fontes verificáveis"):
                research_assets(["ABCD11"], "fake-token", "gpt-6-luna")


if __name__ == "__main__":
    unittest.main()
