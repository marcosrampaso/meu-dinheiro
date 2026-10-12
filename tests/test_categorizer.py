import json
import unittest
from unittest.mock import patch

from categorizer import CategorizationError, suggest_categories


class FakeResponse:
    def __init__(self, body):
        events = body if isinstance(body, list) else [body]
        self.body = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def __iter__(self):
        return iter(self.body.splitlines(keepends=True))


def model_response(items):
    return {"type": "response.completed", "response": {
        "status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"items": items})},
        ]}],
    }}


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
            result = suggest_categories(rows, access_token="test-oauth-token", model="gpt-6-luna")
        self.assertEqual(result, [{"id": 7, "category": "Mercado"}])
        self.assertEqual(sent["body"]["model"], "gpt-6-luna")
        self.assertFalse(sent["body"]["store"])
        self.assertTrue(sent["body"]["stream"])
        self.assertNotIn("max_output_tokens", sent["body"])
        data = json.loads(sent["body"]["input"][1]["content"])
        self.assertEqual(data, {"items": [{"id": 7, "description": "Mercado [número] [email]"}]})
        self.assertNotIn("12345", json.dumps(sent["body"]))
        self.assertNotIn("2026-10-09", json.dumps(sent["body"]))
        self.assertNotIn("Inter · conta", json.dumps(sent["body"]))

    def test_missing_connection_and_invalid_model_result_do_not_save_anything(self):
        rows = [{"id": 1, "description": "Café"}]
        with self.assertRaises(CategorizationError):
            suggest_categories(rows, access_token="", model="gpt-6-luna")
        with patch("categorizer.urlopen", return_value=FakeResponse(
                model_response([{"id": 1, "category": "Categoria inventada"}]))):
            with self.assertRaises(CategorizationError):
                suggest_categories(rows, "test-oauth-token", "gpt-6-luna")
        with patch("categorizer.urlopen", return_value=FakeResponse(model_response([]))):
            with self.assertRaises(CategorizationError):
                suggest_categories(rows, "test-oauth-token", "gpt-6-luna")

    def test_incomplete_stream_does_not_return_suggestions(self):
        rows = [{"id": 1, "description": "Café"}]
        with patch("categorizer.urlopen", return_value=FakeResponse(
                {"type": "response.failed", "response": {"error": {
                    "code": "subscription_sharing_usage_limit_exceeded"}}})):
            with self.assertRaisesRegex(CategorizationError, "Limite de uso"):
                suggest_categories(rows, "test-oauth-token", "gpt-6-luna")

    def test_reads_streamed_text_when_completed_response_has_no_output(self):
        rows = [{"id": 1, "description": "Café"}]
        answer = json.dumps({"items": [{"id": 1, "category": "Alimentação"}]})
        events = [
            {"type": "response.output_text.delta", "output_index": 0, "content_index": 0,
             "delta": answer[:15]},
            {"type": "response.output_text.delta", "output_index": 0, "content_index": 0,
             "delta": answer[15:]},
            {"type": "response.output_text.done", "output_index": 0, "content_index": 0,
             "text": answer},
            {"type": "response.completed", "response": {"status": "completed", "output": []}},
        ]
        with patch("categorizer.urlopen", return_value=FakeResponse(events)):
            self.assertEqual(suggest_categories(rows, "test-oauth-token", "gpt-6-luna"),
                             [{"id": 1, "category": "Alimentação"}])

    def test_streamed_text_without_completion_is_rejected(self):
        rows = [{"id": 1, "description": "Café"}]
        events = [{"type": "response.output_text.delta", "delta":
                   json.dumps({"items": [{"id": 1, "category": "Alimentação"}]})}]
        with patch("categorizer.urlopen", return_value=FakeResponse(events)):
            with self.assertRaisesRegex(CategorizationError, "antes da resposta completa"):
                suggest_categories(rows, "test-oauth-token", "gpt-6-luna")


if __name__ == "__main__":
    unittest.main()
