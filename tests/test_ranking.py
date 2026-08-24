from datetime import datetime, timedelta, timezone

from morning_digest.models import Item
from morning_digest.ranking import relevant_job, select_items


NOW = datetime(2026, 8, 12, 6, tzinfo=timezone.utc)


def item(title: str, category: str = "jobs", excerpt: str = "", location: str = "Zurich", remote: bool = False):
    return Item("test", "fixture", title, "https://example.com/" + title.replace(" ", "-"),
                NOW - timedelta(hours=2), excerpt=excerpt, location=location,
                remote=remote, category=category)


def test_job_filter_accepts_swiss_ai_graduate_role():
    assert relevant_job(item("Graduate Machine Learning Engineer"))


def test_job_filter_accepts_relevant_role_with_unspecified_level():
    assert relevant_job(item("Data Scientist"))
    assert relevant_job(item("MLOps Engineer", location="Worldwide", remote=True))


def test_job_filter_rejects_senior_and_non_ai_roles():
    assert not relevant_job(item("Senior Machine Learning Engineer"))
    assert not relevant_job(item("Graduate Accountant"))


def test_remote_role_must_be_europe_compatible():
    assert relevant_job(item("AI Research Intern", location="Europe", remote=True))
    assert not relevant_job(item("AI Research Intern", location="United States", remote=True))


def test_strict_end_boundary_and_limits():
    in_range = item("OpenAI launches new AI model", category="news", location="")
    on_end = item("AI item at cutoff", category="news", location="")
    on_end.published_at = NOW
    selected = select_items([in_range, on_end], NOW - timedelta(days=1), NOW)
    assert [x.title for x in selected["news"]] == [in_range.title]
