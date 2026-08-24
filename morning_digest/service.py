from __future__ import annotations

import fcntl
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .collectors import collect_all
from .config import Settings
from .emailer import build_message, send_email
from .models import Digest
from .ranking import select_items
from .recommendations import load_recommendation_profile
from .rendering import render
from .storage import Storage
from .summarizer import summarize
from .timewindow import digest_window


@contextmanager
def run_lock(database_path: Path):
    lock_path = database_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another digest run is already active") from exc
        yield


def run(settings: Settings, as_of: datetime, dry_run: bool = False) -> tuple[Path | None, str]:
    start, end = digest_window(as_of, settings.timezone)
    with run_lock(settings.database_path):
        storage = Storage(settings.database_path)
        try:
            if not dry_run and storage.is_delivered(end):
                return None, "Digest already delivered for this cutoff."
            profile = load_recommendation_profile(
                settings.job_profiles_dir, settings.recommendation_mode
            )
            callback = lambda name, ok, error: storage.source_result(end, name, ok, error)
            items, failures = collect_all(settings.feeds, callback)
            selected = select_items(items, start, end, profile)
            flattened = [item for section in selected.values() for item in section]
            # Archive every fetched record, not just newsletter selections. This gives
            # future search/personalization work a useful history and stable IDs.
            for item in items:
                storage.upsert_item(item)
            summaries, overview, fallback = summarize(
                flattened, settings.gemini_api_key, settings.gemini_model
            )
            digest = Digest(start, end, overview=overview, failed_sources=failures, used_fallback=fallback)
            for section in ("news", "technology", "jobs"):
                setattr(digest, section, [summaries[item.item_id] for item in selected[section]])
            subject, html, plain = render(digest)
            digest_id = storage.save_digest(start, end, subject, html, plain)
            for section in ("news", "technology", "jobs"):
                for entry in getattr(digest, section):
                    storage.attach_item(digest_id, entry.item, section, entry.summary, entry.why_it_matters)
            if dry_run:
                settings.output_dir.mkdir(parents=True, exist_ok=True)
                path = settings.output_dir / f"digest-{end:%Y-%m-%d}.html"
                path.write_text(html, encoding="utf-8")
                path.with_suffix(".txt").write_text(plain, encoding="utf-8")
                return path, f"Preview generated with {len(flattened)} items."
            if not settings.smtp_app_password:
                raise RuntimeError("SMTP_APP_PASSWORD is required to send")
            message = build_message(subject, plain, html, settings.smtp_username, settings.recipient)
            try:
                send_email(message, settings.smtp_username, settings.smtp_app_password)
            except Exception as exc:
                storage.mark_error(digest_id, str(exc))
                raise
            storage.mark_delivered(digest_id)
            return None, f"Digest sent to {settings.recipient} with {len(flattened)} items."
        finally:
            storage.close()
