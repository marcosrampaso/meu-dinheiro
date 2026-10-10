import json
import unittest
from unittest.mock import patch

from categorizer import CategorizationError, MissingAPIKey, suggest_categories


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return self.body


def model_response(items):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps({"items": items})},
    ]}]}


class CategorizerTests(unittest.TestCase):
    def test_sends_only_redacted_descriptions_and_validates_categories(self):
        rows = [{"id": 7, "description": "Mercado 123456 email@exemplo.com", "amount_cents": 12345,
                 "occurred_on": "2026-10-09", "source": "Inter · conta"}]
        sent = {}

        def fake_urlopen(request, timeout):
            sent["body"] = json.loads(request.data)
            sent["timeout"] = timeout
            return FakeResponse(model_response([{"id": 7, "category": "Mercado"}]))

        with patch("categorizer.urlopen", side_effect=fake_urlopen):
            result = suggest_categories(rows, api_key="test-key")
        self.assertEqual(result, [{"id": 7, "category": "Mercado"}])
        self.assertEqual(sent["body"]["model"], "gpt-6-luna")
        self.assertFalse(sent["body"]["store"])
        data = json.loads(sent["body"]["input"][1]["content"])
        self.assertEqual(data, {"items": [{"id": 7, "description": "Mercado [número] [email]"}]})
        self.assertNotIn("12345", json.dumps(sent["body"]))
        self.assertNotIn("2026-10-09", json.dumps(sent["body"]))
        self.assertNotIn("Inter · conta", json.dumps(sent["body"]))

    def test_missing_key_and_invalid_model_result_do_not_save_anything(self):
        rows = [{"id": 1, "description": "Café"}]
        with self.assertRaises(MissingAPIKey):
            suggest_categories(rows, api_key="")
        with patch("categorizer.urlopen", return_value=FakeResponse(
                model_response([{"id": 1, "category": "Categoria inventada"}]))):
            with self.assertRaises(CategorizationError):
                suggest_categories(rows, api_key="test-key")
        with patch("categorizer.urlopen", return_value=FakeResponse(model_response([]))):
            with self.assertRaises(CategorizationError):
                suggest_categories(rows, api_key="test-key")


if __name__ == "__main__":
    unittest.main()
