from __future__ import annotations

import math
import re
from datetime import datetime

from .models import Item
from .recommendations import RecommendationProfile
from .text import canonicalize_url, similar_titles

AI_RE = re.compile(r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml|llm|large language model|"
                   r"generative|neural|deep learning|computer vision|nlp|language model|"
                   r"data science|data scientist|mlops)\b", re.I)
TECH_RE = re.compile(r"\b(release|launch|model|open.source|api|framework|benchmark|research|paper|github)\b", re.I)
STARTUP_RE = re.compile(r"\b(startup|funding|raises?|seed|series [a-z]|valuation|founded|launches?)\b", re.I)
JUNIOR_RE = re.compile(r"\b(graduate|new grad|junior|entry.level|trainee|intern(ship)?|working student)\b", re.I)
SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|staff|principal|manager|director|head of|vp|chief)\b", re.I)
AI_JOB_TITLE_RE = re.compile(
    r"\b(ai|artificial intelligence|machine learning|ml|llm|nlp|computer vision|"
    r"data scientist|research engineer|research scientist|mlops|model engineer)\b", re.I
)
SWISS_RE = re.compile(r"\b(switzerland|swiss|zurich|zürich|bern|basel|lausanne|geneva|genève|zug|lucerne)\b", re.I)
EUROPE_RE = re.compile(r"\b(europe|eu|emea|worldwide|anywhere|global|cet|utc\+?[012])\b", re.I)


def in_window(item: Item, start: datetime, end: datetime) -> bool:
    return start <= item.published_at.astimezone(start.tzinfo) < end


def relevant_job(item: Item) -> bool:
    text = f"{item.title} {item.excerpt} {item.location}"
    # Explicit early-career wording is ideal. Also admit strongly relevant titles
    # whose level is unspecified; many employers omit "junior" from suitable roles.
    level_ok = bool(JUNIOR_RE.search(text) or AI_JOB_TITLE_RE.search(item.title))
    if not AI_RE.search(text) or not level_ok or SENIOR_RE.search(f"{item.title} {item.author}"):
        return False
    location_ok = bool(SWISS_RE.search(text))
    remote_europe = item.remote and bool(EUROPE_RE.search(text) or not item.location.strip())
    return location_ok or remote_europe


def score(item: Item, end: datetime, profile: RecommendationProfile | None = None) -> float:
    age_hours = max(0, (end - item.published_at.astimezone(end.tzinfo)).total_seconds() / 3600)
    text = f" {item.title} {item.excerpt[:500]} "
    value = item.score + max(0, 1.5 - age_hours / 16)
    value += 1.3 if AI_RE.search(text) else 0
    value += 0.7 if STARTUP_RE.search(text) else 0
    value += 0.5 if TECH_RE.search(text) else 0
    value += min(1.0, math.log1p(item.engagement) / 6)
    if item.category == "jobs" and profile:
        value += profile.score_adjustment(item)
    return round(value, 4)


def deduplicate(items: list[Item]) -> list[Item]:
    selected: list[Item] = []
    urls: set[str] = set()
    for item in sorted(items, key=lambda x: (x.score, x.published_at), reverse=True):
        canonical = canonicalize_url(item.url)
        if canonical in urls or any(similar_titles(item.title, old.title) for old in selected):
            continue
        item.canonical_url = canonical
        selected.append(item)
        urls.add(canonical)
    return selected


def select_items(
    items: list[Item], start: datetime, end: datetime,
    profile: RecommendationProfile | None = None,
) -> dict[str, list[Item]]:
    candidates = []
    for item in items:
        if not in_window(item, start, end):
            continue
        if item.category == "jobs" and not relevant_job(item):
            continue
        if item.category == "jobs" and profile and profile.score_adjustment(item) <= -100:
            continue
        if item.category != "jobs" and not AI_RE.search(f"{item.title} {item.excerpt[:500]}"):
            continue
        item.score = score(item, end, profile)
        candidates.append(item)
    unique = deduplicate(candidates)
    news = [x for x in unique if x.category == "news"][:7]
    technology = [x for x in unique if x.category == "technology"][:4]
    jobs = [x for x in unique if x.category == "jobs"][:8]
    return {"news": news, "technology": technology, "jobs": jobs}
