from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ENV_FILE, read_env
from .emailer import build_message, send_email
from .models import Digest, DigestEntry, Item
from .rendering import render


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _mock_digest(now: datetime | None = None) -> Digest:
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(hours=24)
    return Digest(
        cutoff_start=start,
        cutoff_end=end,
        overview=[
            "A major model release improves reasoning and tool use.",
            "New research makes efficient local inference more practical.",
            "Several early-career AI and data roles opened in Switzerland and remotely.",
        ],
        news=[DigestEntry(
            Item("Example AI News", "rss", "New multimodal model released",
                 "https://example.com/news/model-release", end - timedelta(hours=3),
                 category="news"),
            "The example release combines text, image, and structured-data capabilities.",
            "It demonstrates how a real news item and its context appear in the daily digest.",
        )],
        technology=[DigestEntry(
            Item("Example Research", "arxiv", "Efficient inference for smaller language models",
                 "https://example.com/research/efficient-inference", end - timedelta(hours=6),
                 category="technology"),
            "Researchers describe a method that reduces inference cost while preserving quality.",
            "Lower serving costs can make local and privacy-sensitive deployments more viable.",
        )],
        jobs=[DigestEntry(
            Item("Example Jobs", "jobs_ch", "Junior Machine Learning Engineer",
                 "https://example.com/jobs/junior-ml-engineer", end - timedelta(hours=8),
                 location="Zurich, Switzerland", company="Example Labs", remote=True,
                 category="jobs"),
            "An entry-level role working on production machine-learning systems.",
            "The role offers practical ML experience and matches an early-career profile.",
        )],
    )


def build_test_message(sender: str, recipient: str, *, now: datetime | None = None):
    subject, html, plain = render(_mock_digest(now))
    return build_message(f"[TEST] {subject}", plain, html, sender, recipient)


def main(argv: list[str] | None = None, *, env_path: Path = ENV_FILE) -> int:
    parser = argparse.ArgumentParser(description="Send a standalone Morning Signal test email")
    parser.add_argument("recipient", help="Destination email address")
    args = parser.parse_args(argv)
    recipient = args.recipient.strip()
    if len(recipient) > 254 or not EMAIL_RE.fullmatch(recipient):
        parser.error("recipient must be a valid email address")
    try:
        config = read_env(env_path)
        username = config.get("SMTP_USERNAME", "").strip()
        password = config.get("SMTP_APP_PASSWORD", "").strip()
        if not username or not password:
            raise ValueError("SMTP_USERNAME and SMTP_APP_PASSWORD are required")
        send_email(build_test_message(username, recipient), username, password)
        print(f"Test email sent to {recipient}")
        return 0
    except Exception as exc:
        print(f"Test email failed: {' '.join(str(exc).split())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
