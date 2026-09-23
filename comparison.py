"""Presentation-only metadata for the already selected contractors."""
from __future__ import annotations

import re


def add_comparison_fields(result, matcher):
    rows = {row["id"]: row for row in matcher.catalog}
    for card in result["cards"]:
        row = rows[card["id"]]
        card["event_formats"] = list(row["event_formats"])
        # Quote an explicit experience claim; never infer years from ages/dates.
        card["experience_excerpt"] = next((text for text in row["evidence"]
            if re.search(r"\b(?:опыт[а-яё]*|стаж[а-яё]*)\b", text, re.IGNORECASE)), None)
    return result
