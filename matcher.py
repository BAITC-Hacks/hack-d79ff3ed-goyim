"""Deterministic recommendation pipeline over the original hackathon CSV."""
from __future__ import annotations

import csv
import math
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

START = date(2026, 9, 23)
END = date(2026, 12, 31)
DATA_PATH = Path(__file__).parent / "data" / "contractors.csv"
REASON_LABELS = {
    "busy": "Заняты на выбранную дату",
    "budget": "Начальная цена выше бюджета",
    "format": "Не работают с этим форматом",
    "language": "Не указан нужный язык",
    "hours": "Длительность превышает лимит",
}
STOP = set("и в на с со по для из от до как что это мы я вы не о об а но или за под при все более меня мой ваш нам мне лет года очень также the and of to for".split())
FORMAT_TERMS = {
    "корпоратив": "корпоратив деловой бизнес форум компания тимбилдинг",
    "конференция": "конференция форум деловой бизнес презентация",
    "свадьба": "свадьба свадебный невеста молодожены церемония",
    "той": "той традиции казахский национальный",
    "юбилей": "юбилей годовщина семья",
    "день рождения": "день рождения праздник развлечения танцы",
}


def tokens(text):
    # A small, documented lexical heuristic, not a trained semantic model.
    return [w[:5] for w in re.findall(r"[а-яёa-z0-9]+", text.lower().replace("ё", "е"))
            if len(w) > 2 and w not in STOP]


def fragments(description):
    parts = re.split(r"(?<=[.!?])\s+|[\n•]+", description)
    result = []
    for part in parts:
        part = part.strip()
        if len(part) < 20:
            continue
        # Keep literal excerpts from the source. Long fragments are visibly truncated.
        if len(part) > 260:
            part = part[:257].rsplit(" ", 1)[0] + "…"
        if part not in result:
            result.append(part)
    return result[:16] or [description[:257] + ("…" if len(description) > 257 else "")]


def load_catalog(path=DATA_PATH):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    seen = set()
    for row in rows:
        if row["id"] in seen:
            raise ValueError("Повторяющийся ID в каталоге")
        seen.add(row["id"])
        for key in ("categories", "event_formats", "languages", "busy_dates"):
            row[key] = row[key].split("|") if row[key] else []
        row["price_from_kzt"] = int(row["price_from_kzt"])
        row["max_hours"] = float(row["max_hours"]) if row["max_hours"] else None
        for key in ("synthetic", "city_imputed", "price_imputed"):
            row[key] = row[key].lower() == "true"
        for day in row["busy_dates"]:
            if not START <= date.fromisoformat(day) <= END:
                raise ValueError("Дата вне календаря каталога")
        row["evidence"] = fragments(row["description"])
    return rows


def money(value):
    return f"{value:,.0f}".replace(",", " ") + " ₸"


class Matcher:
    def __init__(self, catalog=None):
        self.catalog = load_catalog() if catalog is None else catalog
        self.cities = sorted({r["city"] for r in self.catalog})
        self.categories = sorted({c for r in self.catalog for c in r["categories"]})
        self.formats = sorted({c for r in self.catalog for c in r["event_formats"]})
        self.languages = sorted({c for r in self.catalog for c in r["languages"]})
        df = Counter(t for r in self.catalog for t in set(tokens(r["description"])))
        self.idf = {t: math.log((1 + len(self.catalog)) / (1 + n)) + 1 for t, n in df.items()}
        self.vectors = {r["id"]: self.vector(r["description"]) for r in self.catalog}

    def vector(self, text):
        counts = Counter(tokens(text))
        weighted = {t: count * self.idf[t] for t, count in counts.items() if t in self.idf}
        length = math.sqrt(sum(v * v for v in weighted.values())) or 1
        return {t: v / length for t, v in weighted.items()}

    def similarity(self, text, vector):
        return sum(v * vector.get(t, 0) for t, v in self.vector(text).items())

    def metadata(self):
        return {"cities": self.cities, "categories": self.categories,
                "formats": self.formats, "languages": self.languages,
                "calendar_start": START.isoformat(), "calendar_end": END.isoformat(),
                "total": len(self.catalog), "synthetic": sum(r["synthetic"] for r in self.catalog)}

    def validate(self, raw):
        if not isinstance(raw, dict):
            raise ValueError("Ожидается объект с параметрами заказа.")
        q = {}
        for key, label, allowed in [("city", "город", self.cities),
                                    ("category", "категорию", self.categories),
                                    ("event_format", "формат", self.formats)]:
            value = raw.get(key)
            if not isinstance(value, str) or value.strip() not in allowed:
                raise ValueError(f"Выберите {label} из списка.")
            q[key] = value.strip()
        try:
            value = raw.get("date", "")
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError()
            day = date.fromisoformat(value)
        except (ValueError, TypeError):
            raise ValueError("Укажите существующую дату в формате ГГГГ-ММ-ДД.") from None
        if not START <= day <= END:
            raise ValueError("Календарь доступен только с 23.09.2026 по 31.12.2026. За пределами этого периода занятость неизвестна.")
        q["date"] = day.isoformat()
        budget = raw.get("budget")
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or not 0 < budget <= 1_000_000_000:
            raise ValueError("Бюджет должен быть числом от 1 до 1 000 000 000 ₸.")
        if budget != int(budget):
            raise ValueError("Укажите бюджет в целых тенге.")
        q["budget"] = int(budget)
        language = raw.get("language") or None
        if language is not None and language not in self.languages:
            raise ValueError("Выберите язык из списка.")
        q["language"] = language
        hours = raw.get("hours")
        if hours is not None and (isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or not 0 < hours <= 24):
            raise ValueError("Длительность должна быть больше 0 и не больше 24 часов.")
        q["hours"] = hours
        brief = raw.get("brief", "")
        if not isinstance(brief, str) or len(brief) > 600:
            raise ValueError("Пожелания должны быть текстом не длиннее 600 символов.")
        q["brief"] = brief.strip()
        return q

    @staticmethod
    def failures(row, q):
        reasons = []
        if q["date"] in row["busy_dates"]:
            reasons.append("busy")
        if row["price_from_kzt"] > q["budget"]:
            reasons.append("budget")
        if q["event_format"] not in row["event_formats"]:
            reasons.append("format")
        if q["language"] and q["language"] not in row["languages"]:
            reasons.append("language")
        if q["hours"] is not None and row["max_hours"] is not None and q["hours"] > row["max_hours"]:
            reasons.append("hours")
        return reasons

    @staticmethod
    def parameter_match(row, q):
        """Return the share of explicitly requested parameters satisfied by a profile."""
        checks = [
            row["city"] == q["city"],
            q["category"] in row["categories"],
            q["date"] not in row["busy_dates"],
            q["event_format"] in row["event_formats"],
            row["price_from_kzt"] <= q["budget"],
        ]
        if q["language"]:
            checks.append(q["language"] in row["languages"])
        if q["hours"] is not None:
            checks.append(row["max_hours"] is None or q["hours"] <= row["max_hours"])
        return sum(checks) / len(checks), sum(checks), len(checks)

    def evidence_index(self, row, q):
        target = self.vector(FORMAT_TERMS.get(q["event_format"], q["event_format"]) + " " + q["brief"])
        def quality(i):
            text = row["evidence"][i].lower()
            value = 3 * self.similarity(text, target)
            value += 0.04 * len(re.findall(r"опыт|сценари|импровиз|оборудован|язык|подач|флорист|цвет|оформлен|снима|съ[её]м|композици|вместим|зал|театр|акт[её]р|танц|репертуар|печат|дизайн|монтаж", text))
            value += 0.05 if re.search(r"\d+\s*(лет|гост|человек|заказ|мероприяти)", text) else 0
            if re.search(r"привет|уважением|дорогие друзья|меня зовут|свяж|связавш|позвон|телефон", text):
                value -= 2
            return value, -i
        return max(range(len(row["evidence"])), key=quality)

    def explanation(self, row, q, index):
        day = date.fromisoformat(q["date"]).strftime("%d.%m.%Y")
        first = f"На {day} нет отметки о занятости, указан формат «{q['event_format']}», цена от {money(row['price_from_kzt'])} при бюджете {money(q['budget'])}"
        if q["language"]:
            first += f", язык — {q['language']}"
        if q["hours"] is not None:
            if row["max_hours"] is None:
                first += "; работа не привязана к часам присутствия"
            else:
                first += f", лимит {row['max_hours']:g} ч покрывает запрос на {q['hours']:g} ч"
        return first + ". В профиле: «" + row["evidence"][index].rstrip(".") + "»."

    def recommend(self, raw):
        q = self.validate(raw)
        base = [r for r in self.catalog if r["city"] == q["city"] and q["category"] in r["categories"]]
        counts = Counter()
        accepted = []
        for row in base:
            failed = self.failures(row, q)
            counts.update(failed)
            if not failed:
                v = self.vectors[row["id"]]
                by_format = self.similarity(FORMAT_TERMS.get(q["event_format"], q["event_format"]), v)
                by_brief = self.similarity(q["brief"], v)
                relevance = 0.75 * by_brief + 0.25 * by_format if q["brief"] else by_format
                accepted.append((round(relevance, 8), row))
        accepted.sort(key=lambda pair: (-pair[0], pair[1]["price_from_kzt"], pair[1]["id"]))
        cards = []
        for relevance, row in accepted[:3]:
            idx = self.evidence_index(row, q)
            parameter_match, matched_parameters, total_parameters = self.parameter_match(row, q)
            card = {k: row[k] for k in ("id", "anon_name", "categories", "city", "price_from_kzt", "languages", "max_hours", "synthetic", "city_imputed", "price_imputed", "description")}
            card.update({"explanation": self.explanation(row, q, idx), "evidence": row["evidence"][idx],
                         "relevance": relevance, "rank": len(cards) + 1,
                         "parameter_match": parameter_match,
                         "matched_parameters": matched_parameters,
                         "total_parameters": total_parameters,
                         "budget_margin": q["budget"] - row["price_from_kzt"]})
            cards.append(card)
        status = "matched" if cards else ("no_category" if not base else "no_matches")
        if not base:
            message = f"В каталоге города {q['city']} нет категории «{q['category']}»."
        elif not cards:
            message = f"В этой категории в городе есть {len(base)} профилей, но ни один не проходит все выбранные условия."
        elif len(cards) < 3:
            message = f"Подходящих вариантов: {len(cards)}. В категории в этом городе всего {len(base)} профилей."
            message += f" Остальные {len(base) - len(cards)} не проходят условия." if len(base) > len(cards) else " Все подходящие профили показаны."
        else:
            message = f"Показываем 3 варианта из {len(accepted)} подходящих."
        alternatives = []
        if base and len(accepted) < 3 and counts["busy"]:
            current = date.fromisoformat(q["date"])
            for offset in sorted(range(-7, 8), key=lambda n: (abs(n), n)):
                other = current + timedelta(days=offset)
                if offset == 0 or not START <= other <= END:
                    continue
                alt = dict(q, date=other.isoformat())
                number = sum(not self.failures(r, alt) for r in base)
                if number > len(accepted):
                    alternatives.append({"date": other.isoformat(), "count": number})
                if len(alternatives) == 3:
                    break
        min_budget = None
        if not cards and base:
            candidates = [r["price_from_kzt"] for r in base if set(self.failures(r, q)) == {"budget"}]
            if candidates:
                min_budget = min(candidates)
        return {"status": status, "query": q, "message": message, "cards": cards,
                "counts": {"catalog": len(self.catalog), "in_category": len(base), "eligible": len(accepted), "excluded": len(base) - len(accepted)},
                "rejections": [{"reason": key, "label": label, "count": counts[key]} for key, label in REASON_LABELS.items() if counts[key]],
                "alternative_dates": alternatives, "suggested_min_budget": min_budget,
                "busy_profile_ids": [r["id"] for r in base if q["date"] in r["busy_dates"]],
                "explanation_mode": "local", "ai_notice": None}
