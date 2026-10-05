from datetime import datetime, timezone

from morning_digest.models import Digest, DigestEntry, Item
from morning_digest.rendering import render


def test_render_escapes_untrusted_feed_content():
    now = datetime(2026, 8, 12, 6, tzinfo=timezone.utc)
    item = Item("source", "rss", "<script>alert(1)</script>", "https://example.com", now)
    digest = Digest(now, now, news=[DigestEntry(item, "safe summary", "safe why")])
    subject, html, plain = render(digest)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "AI Newsletter" in subject
    assert "safe summary" in plain


def test_render_omits_sections_disabled_for_subscriber():
    now = datetime(2026, 8, 12, 6, tzinfo=timezone.utc)
    job = Item("source", "api", "ML Intern", "https://example.com/job", now, category="jobs")
    digest = Digest(
        now, now, jobs=[DigestEntry(job, "A role", "A match")],
        enabled_sections=frozenset({"jobs"}),
    )
    _, html, plain = render(digest)
    assert "ML Intern" in html
    assert "Top AI developments" not in html
    assert "TECHNOLOGY & RESEARCH" not in plain
