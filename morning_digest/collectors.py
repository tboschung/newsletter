from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import feedparser
import httpx
from dateutil import parser as date_parser

from .config import SourceConfig
from .models import Item
from .text import clean_html

AI_TERMS = ("artificial intelligence", " ai ", "machine learning", "llm", "model", "openai",
            "anthropic", "gemini", "deepmind", "agent", "neural", "inference", "nvidia")
JOBS_CH_TERMS = ("artificial intelligence", "machine learning", "data science", "AI internship")


def _date(value: str | int | float | None) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        parsed = date_parser.parse(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError, OverflowError):
        return None


def fetch_feed(client: httpx.Client, source: SourceConfig) -> list[Item]:
    response = client.get(source.url)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"invalid feed: {parsed.bozo_exception}")
    items = []
    for entry in parsed.entries:
        published = _date(entry.get("published") or entry.get("updated"))
        url = entry.get("link", "")
        title = clean_html(entry.get("title", ""))
        if published and url and title:
            items.append(Item(
                source=source.name, source_type="rss", category=source.category,
                title=title, url=url, published_at=published,
                excerpt=clean_html(entry.get("summary", ""))[:1600],
                author=clean_html(entry.get("author", "")),
                score=source.weight,
            ))
    return items


def fetch_hacker_news(client: httpx.Client) -> list[Item]:
    ids = client.get("https://hacker-news.firebaseio.com/v0/topstories.json").raise_for_status().json()[:120]
    items = []
    for story_id in ids:
        story = client.get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json").raise_for_status().json()
        if not story or story.get("type") != "story":
            continue
        title = story.get("title", "")
        haystack = f" {title.lower()} "
        if not any(term in haystack for term in AI_TERMS):
            continue
        items.append(Item(
            source="Hacker News", source_type="api", category="news", title=title,
            url=story.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
            published_at=datetime.fromtimestamp(story["time"], tz=timezone.utc),
            excerpt=f"Hacker News discussion with {story.get('descendants', 0)} comments.",
            engagement=int(story.get("score", 0)) + int(story.get("descendants", 0)), score=1.0,
        ))
    return items


def fetch_arxiv(client: httpx.Client) -> list[Item]:
    url = ("https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.LG"
           "&start=0&max_results=40&sortBy=submittedDate&sortOrder=descending")
    response = client.get(url)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    return [Item(
        source="arXiv", source_type="api", category="technology",
        title=clean_html(e.get("title", "")), url=e.get("link", ""),
        published_at=_date(e.get("published")) or datetime.min.replace(tzinfo=timezone.utc),
        excerpt=clean_html(e.get("summary", ""))[:1600],
        author=", ".join(a.get("name", "") for a in e.get("authors", [])), score=1.05,
    ) for e in parsed.entries if e.get("link")]


def fetch_arbeitnow(client: httpx.Client) -> list[Item]:
    data = client.get("https://www.arbeitnow.com/api/job-board-api").raise_for_status().json()
    items = []
    for job in data.get("data", []):
        created = _date(job.get("created_at"))
        if not created:
            continue
        items.append(Item(
            source="Arbeitnow", source_type="api", category="jobs", title=job.get("title", ""),
            url=job.get("url", ""), published_at=created,
            excerpt=clean_html(job.get("description", ""))[:2400], company=job.get("company_name", ""),
            location=job.get("location", ""), remote=bool(job.get("remote")), score=1.0,
        ))
    return items


def fetch_jobicy(client: httpx.Client) -> list[Item]:
    data = client.get(
        "https://jobicy.com/api/v2/remote-jobs",
        params={"count": 100, "geo": "europe"},
    ).raise_for_status().json()
    return [Item(
        source="Jobicy", source_type="api", category="jobs",
        title=job.get("jobTitle", ""), url=job.get("url", ""),
        published_at=_date(job.get("pubDate")) or datetime.min.replace(tzinfo=timezone.utc),
        excerpt=clean_html(job.get("jobDescription") or job.get("jobExcerpt", ""))[:2400],
        company=job.get("companyName", ""), location=job.get("jobGeo", ""),
        remote=True, score=1.05,
    ) for job in data.get("jobs", []) if job.get("url") and job.get("jobTitle")]


def fetch_remotive(client: httpx.Client) -> list[Item]:
    data = client.get("https://remotive.com/api/remote-jobs", params={"limit": 100}).raise_for_status().json()
    return [Item(
        source="Remotive", source_type="api", category="jobs",
        title=job.get("title", ""), url=job.get("url", ""),
        published_at=_date(job.get("publication_date")) or datetime.min.replace(tzinfo=timezone.utc),
        excerpt=clean_html(job.get("description", ""))[:2400],
        company=job.get("company_name", ""), location=job.get("candidate_required_location", ""),
        remote=True, score=1.05,
    ) for job in data.get("jobs", []) if job.get("url") and job.get("title")]


def fetch_remote_ok(client: httpx.Client) -> list[Item]:
    data = client.get("https://remoteok.com/api").raise_for_status().json()
    return [Item(
        source="Remote OK", source_type="api", category="jobs",
        title=job.get("position", ""), url=job.get("url", ""),
        published_at=_date(job.get("date") or job.get("epoch"))
        or datetime.min.replace(tzinfo=timezone.utc),
        excerpt=clean_html(job.get("description", ""))[:2400],
        company=job.get("company", ""), location=job.get("location", "Worldwide"),
        remote=True, score=1.0,
    ) for job in data if isinstance(job, dict) and job.get("position") and job.get("url")]


def _job_postings_from_json_ld(html: str) -> list[dict]:
    postings: list[dict] = []
    pattern = r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    for raw in re.findall(pattern, html, flags=re.I | re.S):
        try:
            documents = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for document in documents if isinstance(documents, list) else [documents]:
            if not isinstance(document, dict):
                continue
            if document.get("@type") == "JobPosting":
                postings.append(document)
            for element in document.get("itemListElement", []):
                candidate = element.get("item", {}) if isinstance(element, dict) else {}
                if candidate.get("@type") == "JobPosting":
                    postings.append(candidate)
    return postings


def fetch_jobs_ch(client: httpx.Client) -> list[Item]:
    items = []
    for term in JOBS_CH_TERMS:
        response = client.get("https://www.jobs.ch/en/vacancies/", params={"term": term})
        response.raise_for_status()
        for job in _job_postings_from_json_ld(response.text):
            address = job.get("jobLocation", {}).get("address", {})
            organization = job.get("hiringOrganization", {})
            published = _date(job.get("datePosted"))
            if not published or not job.get("title") or not job.get("url"):
                continue
            items.append(Item(
                source="jobs.ch", source_type="structured-html", category="jobs",
                title=clean_html(job["title"]), url=job["url"], published_at=published,
                excerpt=clean_html(job.get("description", ""))[:2400],
                company=clean_html(organization.get("name", "")),
                location=clean_html(address.get("addressLocality") or "Switzerland"),
                score=1.15,
            ))
    return items


class _SwissAIJobParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.jobs: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.field = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        href = values.get("href") or ""
        if tag == "a" and href.startswith("/jobs/") and self.current is None:
            self.current = {"url": "https://swissaijob.ch" + href}
        elif self.current is not None:
            classes = values.get("class") or ""
            if tag == "img" and values.get("alt"):
                self.current.setdefault("company", values["alt"] or "")
            elif tag == "h3":
                self.field = "title"
            elif "uppercase tracking-widest" in classes and tag == "span":
                self.field = "label"

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag in {"h3", "span"}:
            self.field = ""
        if tag == "a":
            if self.current.get("title"):
                self.jobs.append(self.current)
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        value = clean_html(data).strip()
        if not value:
            return
        if self.field == "title":
            self.current["title"] = self.current.get("title", "") + value
        elif self.field == "label":
            lowered = value.lower()
            if lowered in {"today", "yesterday"} or "day" in lowered:
                self.current["age"] = lowered
            elif lowered in {"junior", "mid", "senior", "phd"}:
                self.current["seniority"] = value


def fetch_swiss_ai_job(client: httpx.Client) -> list[Item]:
    response = client.get("https://swissaijob.ch/")
    response.raise_for_status()
    parser = _SwissAIJobParser()
    parser.feed(response.text)
    now = datetime.now(timezone.utc)
    items = []
    for job in parser.jobs:
        age = job.get("age", "")
        match = re.search(r"\d+", age)
        days = 1 if age == "yesterday" else int(match.group()) if match else 0
        items.append(Item(
            source="SwissAIJob", source_type="structured-html", category="jobs",
            title=job["title"], url=job["url"],
            published_at=now.replace(microsecond=0) - timedelta(days=days),
            excerpt=f"AI role listed by SwissAIJob. Seniority: {job.get('seniority', 'unspecified')}.",
            author=job.get("seniority", ""), company=job.get("company", ""),
            location="Switzerland", score=1.25,
        ))
    return items


def fetch_swiss_dev_jobs(client: httpx.Client) -> list[Item]:
    data = client.get("https://swissdevjobs.ch/api/jobsLight").raise_for_status().json()
    items = []
    for job in data:
        published = _date(job.get("activeFrom"))
        url = job.get("redirectJobUrl")
        if not published or not url or not job.get("name"):
            continue
        technologies = ", ".join(job.get("technologies", []))
        items.append(Item(
            source="SwissDevJobs", source_type="api", category="jobs",
            title=clean_html(job["name"]), url=url, published_at=published,
            excerpt=f"Technologies: {technologies}. Experience level: {job.get('expLevel', 'unspecified')}.",
            author=job.get("expLevel", ""), company=clean_html(job.get("company", "")),
            location=clean_html(job.get("actualCity") or "Switzerland"),
            remote=job.get("workplace") in {"remote", "hybrid"}, score=1.2,
        ))
    return items


ADAPTERS = {
    "hacker_news": fetch_hacker_news,
    "arxiv": fetch_arxiv,
    "arbeitnow": fetch_arbeitnow,
    "jobicy": fetch_jobicy,
    "remotive": fetch_remotive,
    "remote_ok": fetch_remote_ok,
    "swiss_ai_job": fetch_swiss_ai_job,
    "jobs_ch": fetch_jobs_ch,
    "swiss_dev_jobs": fetch_swiss_dev_jobs,
}


def collect_source(client: httpx.Client, source: SourceConfig) -> list[Item]:
    """Run one configured adapter and normalize its output to the source config."""
    if source.adapter == "rss":
        items = fetch_feed(client, source)
    else:
        try:
            collector = ADAPTERS[source.adapter]
        except KeyError as exc:
            raise ValueError(f"unknown collector adapter: {source.adapter}") from exc
        items = collector(client)
    for item in items:
        item.source = source.name
        item.category = source.category
        item.score = source.weight
    return items
