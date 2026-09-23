import copy
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from ai import EvidenceSelector
from app import make_handler
from matcher import END, START, Matcher

BASE = dict(city="Алматы", date="2026-10-15", category="Ведущий", event_format="корпоратив", budget=1_000_000)


class MatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = Matcher()

    def test_source_and_calendar(self):
        self.assertEqual(len(self.m.catalog), 66)
        self.assertEqual(sum(r["synthetic"] for r in self.m.catalog), 13)
        self.assertEqual(len(self.m.categories), 17)
        self.assertEqual(len({r["id"] for r in self.m.catalog}), 66)

    def test_dense_and_determinism(self):
        first = self.m.recommend(BASE)
        self.assertEqual(first["status"], "matched")
        self.assertEqual(first["counts"]["eligible"], 6)
        self.assertEqual(len(first["cards"]), 3)
        self.assertEqual(first, self.m.recommend(BASE))
        reversed_matcher = Matcher(list(reversed(self.m.catalog)))
        self.assertEqual([c["id"] for c in first["cards"]], [c["id"] for c in reversed_matcher.recommend(BASE)["cards"]])

    def test_two_dates_change_results(self):
        first = self.m.recommend(BASE)
        second = self.m.recommend(dict(BASE, date="2026-10-16"))
        self.assertEqual(second["counts"]["eligible"], 4)
        self.assertNotEqual([c["id"] for c in first["cards"]], [c["id"] for c in second["cards"]])
        self.assertTrue(any(c["id"] in second["busy_profile_ids"] for c in first["cards"]))

    def test_rare_category_and_null_hours(self):
        result = self.m.recommend(dict(BASE, category="Флорист", event_format="свадьба", budget=500_000, hours=12))
        self.assertEqual(result["counts"]["eligible"], 2)
        self.assertEqual(len(result["cards"]), 2)
        self.assertIn("Все подходящие профили показаны", result["message"])
        self.assertTrue(any(c["synthetic"] for c in result["cards"]))
        self.assertTrue(all(c["max_hours"] is None for c in result["cards"]))

    def test_distinct_empty_outcomes(self):
        absent = self.m.recommend(dict(BASE, city="Астана", category="Декоратор"))
        budget = self.m.recommend(dict(BASE, budget=10_000))
        self.assertEqual(absent["status"], "no_category")
        self.assertEqual(budget["status"], "no_matches")
        self.assertEqual(absent["counts"]["in_category"], 0)
        self.assertEqual(budget["counts"]["in_category"], 10)
        self.assertTrue(budget["suggested_min_budget"] > 10_000)
        self.assertEqual(budget["cards"], [])

    def test_every_returned_profile_meets_conditions_across_calendar(self):
        rows = {r["id"]: r for r in self.m.catalog}
        # Covers dense/rare/venue profiles and both optional filters on 15 dates.
        for offset in range(0, 100, 7):
            for category in ["Ведущий", "Флорист", "Банкетный зал"]:
                q = dict(BASE, date=(START + timedelta(days=offset)).isoformat(), category=category,
                         budget=4_000_000, language="русский", hours=6)
                result = self.m.recommend(q)
                self.assertLessEqual(len(result["cards"]), 3)
                for card in result["cards"]:
                    row = rows[card["id"]]
                    self.assertEqual(row["city"], q["city"])
                    self.assertIn(category, row["categories"])
                    self.assertNotIn(q["date"], row["busy_dates"])
                    self.assertIn(q["event_format"], row["event_formats"])
                    self.assertIn(q["language"], row["languages"])
                    self.assertLessEqual(row["price_from_kzt"], q["budget"])
                    self.assertTrue(row["max_hours"] is None or row["max_hours"] >= 6)

    def test_explanations_are_specific_and_grounded(self):
        result = self.m.recommend(BASE)
        self.assertEqual(len({c["evidence"] for c in result["cards"]}), len(result["cards"]))
        for card in result["cards"]:
            literal = card["evidence"].removesuffix("…")
            self.assertIn(literal, card["description"])
            self.assertIn("15.10.2026", card["explanation"])
            self.assertNotIn("отличный выбор", card["explanation"].lower())
            self.assertNotIn("приветствую", card["evidence"].lower())

    def test_invalid_inputs(self):
        for edit in [{"date": "2026-09-22"}, {"date": "2027-01-01"}, {"date": "2026-02-30"},
                     {"date": "20261015"}, {"budget": -1}, {"budget": True}, {"budget": float("nan")},
                     {"budget": float("inf")}, {"budget": "10000"}, {"hours": -1}, {"hours": True},
                     {"language": "несуществующий"}, {"brief": "x" * 601}, {"city": ""}]:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.m.recommend(dict(BASE, **edit))
        for day in [START, END]:
            self.m.recommend(dict(BASE, date=day.isoformat()))

    def test_budget_boundary(self):
        q = dict(BASE, category="Флорист", event_format="свадьба", budget=200_000)
        self.assertEqual(len(self.m.recommend(q)["cards"]), 1)
        self.assertEqual(len(self.m.recommend(dict(q, budget=199_999))["cards"]), 0)

    def test_alternative_dates_keep_other_constraints(self):
        found = False
        for day in range(1, 32):
            q = dict(BASE, date=f"2026-12-{day:02d}")
            result = self.m.recommend(q)
            for alt in result["alternative_dates"]:
                found = True
                other = self.m.recommend(dict(q, date=alt["date"]))
                self.assertEqual(other["counts"]["eligible"], alt["count"])
                self.assertGreater(alt["count"], result["counts"]["eligible"])
        self.assertTrue(found)

    def test_brief_influences_order_without_relaxing_filters(self):
        basic = self.m.recommend(BASE)
        tailored = self.m.recommend(dict(BASE, brief="DJ современная танцевальная музыка мультимедийное оборудование"))
        self.assertEqual(basic["counts"]["eligible"], tailored["counts"]["eligible"])
        self.assertNotEqual([c["id"] for c in basic["cards"]], [c["id"] for c in tailored["cards"]])

    def test_budget_recovery_is_minimal_and_preserves_other_requirements(self):
        query = dict(BASE, budget=10000, language="русский", hours=6, brief="Спокойная подача")
        empty = self.m.recommend(query)
        minimum = empty["suggested_min_budget"]
        self.assertIsNotNone(minimum)
        recovered = self.m.recommend(dict(query, budget=minimum))
        self.assertEqual(recovered["status"], "matched")
        self.assertFalse(self.m.recommend(dict(query, budget=minimum-1))["cards"])
        for key, value in empty["query"].items():
            if key != "budget": self.assertEqual(recovered["query"][key], value)

    def test_budget_recovery_excludes_busy_or_wrong_format_candidates(self):
        row = copy.deepcopy(next(row for row in self.m.catalog if row["city"] == BASE["city"] and BASE["category"] in row["categories"]))
        row.update(price_from_kzt=40000, busy_dates=[BASE["date"]], event_formats=[BASE["event_format"]])
        query = dict(BASE, budget=10000)
        self.assertIsNone(Matcher([row]).recommend(query)["suggested_min_budget"])
        row.update(busy_dates=[], event_formats=["свадьба"])
        # Keep корпоратив in metadata without adding an eligible row in this city.
        other = copy.deepcopy(row)
        other.update(id="other-city",city="Астана",event_formats=[BASE["event_format"]])
        empty = Matcher([row,other]).recommend(query)
        self.assertIsNone(empty["suggested_min_budget"])
        self.assertEqual(empty["alternative_dates"], [])


class AITests(unittest.TestCase):
    def setUp(self):
        self.matcher = Matcher()
        self.result = self.matcher.recommend(BASE)
        self.selector = EvidenceSelector()

    @patch.dict(os.environ, {"OPENAI_API_KEY": ""})
    @patch("ai.fetch_choices")
    def test_without_key_is_fully_local(self, fetch):
        self.assertEqual(self.selector.enhance(self.result, self.matcher), self.result)
        fetch.assert_not_called()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"})
    def test_ai_cannot_reorder_or_invent(self):
        choices = [{"id": c["id"], "evidence_index": 0} for c in reversed(self.result["cards"])]
        with patch("ai.fetch_choices", return_value=choices) as fetch:
            enhanced = self.selector.enhance(copy.deepcopy(self.result), self.matcher)
            self.assertEqual(enhanced["explanation_mode"], "ai")
            self.assertEqual([c["id"] for c in enhanced["cards"]], [c["id"] for c in self.result["cards"]])
            cached = self.selector.enhance(copy.deepcopy(self.result), self.matcher)
            self.assertEqual(cached["explanation_mode"], "ai_cached")
            self.assertEqual(fetch.call_count, 1)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"})
    def test_invalid_ai_and_network_error_use_fallback(self):
        invalid = [{"id": c["id"], "evidence_index": 9999} for c in self.result["cards"]]
        for kwargs in [{"return_value": invalid}, {"side_effect": TimeoutError}, {"return_value": []}]:
            with patch("ai.fetch_choices", **kwargs):
                enhanced = self.selector.enhance(copy.deepcopy(self.result), self.matcher)
                self.assertEqual(enhanced["explanation_mode"], "fallback")
                self.assertEqual(enhanced["cards"], self.result["cards"])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"})
    @patch("ai.fetch_choices")
    def test_sensitive_brief_stays_local(self, fetch):
        for brief in ["Напишите на person@example.test", "Мой пароль: example-only", "```python\nprint('example')\n```"]:
            original = self.matcher.recommend(dict(BASE, brief=brief))
            enhanced = self.selector.enhance(copy.deepcopy(original), self.matcher)
            self.assertEqual(enhanced["explanation_mode"], "fallback")
            self.assertEqual(enhanced["cards"], original["cards"])
            self.assertIn("не отправлен в AI", enhanced["ai_notice"])
        fetch.assert_not_called()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"})
    @patch("ai.fetch_choices")
    def test_sensitive_profile_excerpt_stays_local(self, fetch):
        matcher = copy.deepcopy(self.matcher)
        target = next(row for row in matcher.catalog if row["id"] == self.result["cards"][0]["id"])
        target["evidence"].append("Контакт: person@example.test")
        enhanced = self.selector.enhance(copy.deepcopy(self.result), matcher)
        self.assertEqual(enhanced["cards"], self.result["cards"])
        self.assertEqual(enhanced["explanation_mode"], "fallback")
        fetch.assert_not_called()


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class LocalSelector:
            def enhance(self, result, matcher):
                return result
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(selector=LocalSelector()))
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_http_success_and_validation(self):
        request = urllib.request.Request(self.url + "/api/recommend", data=json.dumps(BASE).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            data = json.load(response)
            self.assertEqual(len(data["cards"]), 3)
            self.assertIn("elapsed_ms", data)
        request = urllib.request.Request(self.url + "/api/recommend", data=b'{}', headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 400)

    def test_private_files_are_not_served(self):
        for path in ["/.env", "/app.py", "/data/contractors.csv", "/../.env"]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.url + path)
            self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
