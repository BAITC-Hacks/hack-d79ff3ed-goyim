import copy
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import make_handler
from event_parser import (MALFORMED, UNAVAILABLE, ParserUnavailable, ParserValidationError,
                          decode_response, fetch_extraction, parse_event, validate_extraction)
from matcher import Matcher

OPTIONS = Matcher().metadata()
TEXT = "Нужен фотограф для офлайн-свадьбы в Астане 5 октября, бюджет до 200 000 ₸."
VALID = {"city": "Астана", "date": "2026-10-05", "format": "офлайн", "category": "Фотограф", "budget": 200000}
EMPTY = {key: None for key in VALID}


def api_response(fields):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(fields, ensure_ascii=False)}]}]}


class ParserTests(unittest.TestCase):
    def test_valid_five_fields_and_strict_transport(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _): return json.dumps(api_response(VALID)).encode()
        with patch.dict(os.environ, {"OPENAI_MODEL": ""}), patch("event_parser.urllib.request.urlopen", return_value=Response()) as call:
            self.assertEqual(fetch_extraction(TEXT, "fixture-key-not-real", OPTIONS), VALID)
        request = call.call_args.args[0]
        sent = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(sent["input"], TEXT)
        self.assertFalse(sent["store"])
        self.assertTrue(sent["text"]["format"]["strict"])
        self.assertEqual(set(sent["text"]["format"]["schema"]["properties"]), set(VALID))
        self.assertNotIn("fixture-key-not-real", request.data.decode())
        self.assertNotIn("HK-", request.data.decode())
        self.assertNotIn("description", sent)

    def test_missing_fields_remain_null(self):
        partial = dict(EMPTY, category="Фотограф")
        self.assertEqual(decode_response(api_response(partial), OPTIONS), partial)

    def test_malformed_api_response_is_russian_error(self):
        cases = [None, {}, {"status": "incomplete"}, api_response([VALID]), api_response(dict(VALID, extra="x")),
                 {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not JSON"}]}]},
                 {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}]
        for case in cases:
            with self.subTest(case=case), self.assertRaisesRegex(ParserValidationError, "AI вернул некорректные параметры"):
                decode_response(case, OPTIONS)

    def test_strict_types_dates_duplicate_keys_and_no_markdown(self):
        for edit in [{"budget": True}, {"budget": "200000"}, {"budget": 200000.0}, {"budget": -1},
                     {"date": "2026-02-30"}, {"date": "20261005"}, {"city": ["Астана"]}, {"category": ""}]:
            with self.subTest(edit=edit), self.assertRaises(ParserValidationError):
                validate_extraction(dict(VALID, **edit), OPTIONS)
        for text in ['{"city":null,"city":"Астана"}', '```json\n{}\n```', '{"budget":NaN}']:
            response = api_response(VALID)
            response["output"][0]["content"][0]["text"] = text
            with self.assertRaises(ParserValidationError): decode_response(response, OPTIONS)
        with self.assertRaises(ParserValidationError):
            validate_extraction({"category": "Фотограф"}, OPTIONS)

    def test_mode_synonyms(self):
        for value, expected in [("очная", "офлайн"), ("удалённая", "онлайн"), ("hybrid", "гибрид")]:
            self.assertEqual(validate_extraction(dict(VALID, format=value), OPTIONS)["format"], expected)

    def test_too_vague_and_out_of_calendar(self):
        with self.assertRaisesRegex(ParserValidationError, "Не удалось определить параметры"):
            decode_response(api_response(EMPTY), OPTIONS)
        with self.assertRaisesRegex(ParserValidationError, "вне календаря"):
            validate_extraction(dict(VALID, date="2027-10-05"), OPTIONS)

    @patch.dict(os.environ, {"OPENAI_API_KEY": ""})
    @patch("event_parser.fetch_extraction")
    def test_missing_key_fallback_no_request(self, fetch):
        with self.assertRaisesRegex(ParserUnavailable, UNAVAILABLE):
            parse_event({"text": TEXT}, OPTIONS)
        fetch.assert_not_called()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key-not-real"})
    def test_api_failure_fallback(self):
        for error in [TimeoutError(), urllib.error.URLError("test outage"), OSError("test failure")]:
            with patch("event_parser.fetch_extraction", side_effect=error) as fetch:
                with self.assertRaisesRegex(ParserUnavailable, UNAVAILABLE):
                    parse_event({"text": TEXT}, OPTIONS)
                self.assertEqual(fetch.call_count, 1)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key-not-real"})
    def test_parse_passes_only_whitelisted_context(self):
        with patch("event_parser.fetch_extraction", return_value=VALID) as fetch:
            result = parse_event({"text": TEXT}, dict(OPTIONS, profiles=[{"private": "not sent"}]))
        self.assertEqual(result, VALID)
        self.assertEqual(set(fetch.call_args.args[2]), {"cities", "categories", "formats", "calendar_start", "calendar_end"})

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key-not-real"})
    @patch("event_parser.fetch_extraction")
    def test_sensitive_input_and_code_rejected_before_network(self, fetch):
        for text in ["Нужен фотограф, почта test@example.com", "Телефон +7 777 123 45 67", "Мой пароль: example",
                     "import os\nprint(os.environ)", "```python\nprint('hello')\n```"]:
            with self.subTest(text=text), self.assertRaises(ParserValidationError):
                parse_event({"text": text}, OPTIONS)
        fetch.assert_not_called()


class ParserAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class LocalSelector:
            def enhance(self, result, matcher): return result
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(selector=LocalSelector()))
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, path, payload):
        request = urllib.request.Request(self.url + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as response: return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key-not-real"})
    @patch("event_parser.fetch_extraction", return_value=VALID)
    def test_http_extraction_success(self, fetch):
        status, result = self.post("/api/parse-event", {"text": TEXT})
        self.assertEqual(status, 200)
        self.assertEqual(result, {"fields": VALID})

    @patch.dict(os.environ, {"OPENAI_API_KEY": ""})
    def test_missing_key_does_not_break_manual_search(self):
        status, result = self.post("/api/parse-event", {"text": TEXT})
        self.assertEqual((status, result), (503, {"error": UNAVAILABLE}))
        status, result = self.post("/api/recommend", {"city": "Алматы", "date": "2026-10-15", "event_format": "корпоратив", "category": "Ведущий", "budget": 1000000})
        self.assertEqual(status, 200)
        self.assertEqual([c["anon_name"] for c in result["cards"]], ["Буллма", "Кики", "Мицури Канроджи"])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key-not-real"})
    def test_http_malformed_and_api_failure(self):
        with patch("event_parser.fetch_extraction", side_effect=ParserValidationError(MALFORMED)):
            self.assertEqual(self.post("/api/parse-event", {"text": TEXT}), (422, {"error": MALFORMED}))
        with patch("event_parser.fetch_extraction", side_effect=TimeoutError):
            self.assertEqual(self.post("/api/parse-event", {"text": TEXT}), (503, {"error": UNAVAILABLE}))


if __name__ == "__main__":
    unittest.main()
