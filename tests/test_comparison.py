import copy
import unittest
from types import SimpleNamespace

from comparison import add_comparison_fields
from matcher import Matcher


class ComparisonTests(unittest.TestCase):
    def test_presentation_adds_only_source_fields_without_changing_selection(self):
        matcher = Matcher()
        result = matcher.recommend(dict(city="Алматы",date="2026-10-15",event_format="корпоратив",category="Ведущий",budget=1000000))
        original = copy.deepcopy(result)
        enriched = add_comparison_fields(result, matcher)
        rows = {row["id"]: row for row in matcher.catalog}
        self.assertEqual(len(enriched["cards"]), 3)
        for before, after in zip(original["cards"], enriched["cards"]):
            self.assertEqual(after["event_formats"], rows[before["id"]]["event_formats"])
            excerpt = after["experience_excerpt"]
            if excerpt: self.assertIn(excerpt.removesuffix("…"), before["description"])
            self.assertEqual({key:value for key,value in after.items() if key not in {"event_formats","experience_excerpt"}}, before)
        self.assertEqual({k:v for k,v in enriched.items() if k != "cards"}, {k:v for k,v in original.items() if k != "cards"})

    def test_missing_experience_is_null_and_age_is_not_converted_to_experience(self):
        matcher = SimpleNamespace(catalog=[{"id":"one", "event_formats":["свадьба"], "evidence":["Мне 30 лет. Съёмка на 5 часов, до 200 гостей."]}])
        result = add_comparison_fields({"cards":[{"id":"one"}]}, matcher)
        self.assertIsNone(result["cards"][0]["experience_excerpt"])
        self.assertNotIn("rating", result["cards"][0])
        self.assertNotIn("experience_years", result["cards"][0])

    def test_experience_is_a_literal_claim_including_negation(self):
        for excerpt in ["Опыт работы — более 12 лет.", "Без опыта работы на свадьбах.", "Есть опыт деловых конференций."]:
            matcher = SimpleNamespace(catalog=[{"id":"one", "event_formats":["корпоратив"], "evidence":[excerpt]}])
            result = add_comparison_fields({"cards":[{"id":"one"}]}, matcher)
            self.assertEqual(result["cards"][0]["experience_excerpt"], excerpt)


if __name__ == "__main__":
    unittest.main()
