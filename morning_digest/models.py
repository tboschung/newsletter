from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Item:
    source: str
    source_type: str
    title: str
    url: str
    published_at: datetime
    excerpt: str = ""
    author: str = ""
    engagement: int = 0
    location: str = ""
    company: str = ""
    remote: bool = False
    canonical_url: str = ""
    score: float = 0.0
    category: str = "news"
    item_id: int | None = None


@dataclass(slots=True)
class DigestEntry:
    item: Item
    summary: str
    why_it_matters: str = ""


@dataclass(slots=True)
class Digest:
    cutoff_start: datetime
    cutoff_end: datetime
    overview: list[str] = field(default_factory=list)
    news: list[DigestEntry] = field(default_factory=list)
    technology: list[DigestEntry] = field(default_factory=list)
    jobs: list[DigestEntry] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    used_fallback: bool = False
    enabled_sections: frozenset[str] = field(
        default_factory=lambda: frozenset(("news", "technology", "jobs"))
    )
