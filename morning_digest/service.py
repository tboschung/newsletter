from __future__ import annotations

import fcntl
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .config import Settings, Subscriber, resolve_subscriber
from .emailer import build_message, classify_smtp_error, send_email
from .engine import ContentEngine
from .models import Digest, Item
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


def _build_digest(
    subscriber: Subscriber,
    items: list[Item],
    start: datetime,
    end: datetime,
    failures: list[str],
    settings: Settings,
) -> tuple[Digest, int]:
    profile = None
    if "jobs" in subscriber.content:
        profile = load_recommendation_profile(settings.job_profiles_dir, subscriber.job_profile)
    subscriber_items = [replace(item) for item in items if item.category in subscriber.content]
    selected = select_items(subscriber_items, start, end, profile)
    for section in selected:
        if section not in subscriber.content:
            selected[section] = []
    flattened = [item for section in selected.values() for item in section]
    summaries, overview, fallback = summarize(
        flattened, settings.gemini_api_key, settings.gemini_model
    )
    digest = Digest(
        start,
        end,
        overview=overview,
        failed_sources=failures,
        used_fallback=fallback,
        enabled_sections=subscriber.content,
    )
    for section in ("news", "technology", "jobs"):
        setattr(digest, section, [summaries[item.item_id] for item in selected[section]])
    return digest, len(flattened)


def run(settings: Settings, as_of: datetime, dry_run: bool = False) -> tuple[list[Path], str]:
    start, end = digest_window(as_of, settings.timezone)
    previews: list[Path] = []
    with run_lock(settings.database_path):
        storage = Storage(settings.database_path)
        try:
            presets = {preset.preset_id: preset for preset in settings.presets}
            storage.validate_preset_references(set(presets))
            configured_subscribers = list(settings.subscribers)
            database_subscriber_ids: set[str] = set()
            for subscriber_id, email, preset_id, overrides in storage.active_subscribers():
                preset = presets.get(preset_id)
                if preset is None:
                    raise ValueError(
                        f"subscriber {subscriber_id!r} references unknown preset {preset_id!r}"
                    )
                configured_subscribers.append(
                    resolve_subscriber(subscriber_id, email, preset, overrides)
                )
                database_subscriber_ids.add(subscriber_id)
            subscriber_ids = [subscriber.subscriber_id for subscriber in configured_subscribers]
            if len(subscriber_ids) != len(set(subscriber_ids)):
                raise ValueError("duplicate subscriber ids across file and database configuration")
            subscribers = [
                subscriber for subscriber in configured_subscribers
                if dry_run or not storage.is_delivered(end, subscriber.subscriber_id)
            ]
            if not subscribers:
                return [], "All subscriber digests were already delivered for this cutoff."

            wanted = frozenset().union(*(subscriber.content for subscriber in subscribers))
            items: list[Item] = []
            failures_by_engine: dict[str, list[str]] = {}
            headers = {"User-Agent": "MorningIntelligence/0.2 (personal daily digest)"}
            callback = lambda name, ok, error: storage.source_result(end, name, ok, error)
            with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
                for engine_config in settings.engines:
                    categories = {source.category for source in engine_config.sources}
                    if not categories & wanted:
                        continue
                    engine_items, failures = ContentEngine(engine_config).collect(
                        client, callback, wanted
                    )
                    items.extend(engine_items)
                    failures_by_engine[engine_config.name] = failures

            for item in items:
                storage.upsert_item(item)

            sent = 0
            failed = 0
            total_items = 0
            for subscriber in subscribers:
                relevant_failures = [
                    failure
                    for engine in settings.engines
                    if {source.category for source in engine.sources} & subscriber.content
                    for failure in failures_by_engine.get(engine.name, [])
                ]
                digest, item_count = _build_digest(
                    subscriber, items, start, end, relevant_failures, settings
                )
                total_items += item_count
                subject, html, plain = render(digest)
                digest_id = storage.save_digest(
                    start, end, subject, html, plain, subscriber.subscriber_id
                )
                for section in ("news", "technology", "jobs"):
                    for entry in getattr(digest, section):
                        storage.attach_item(
                            digest_id, entry.item, section, entry.summary, entry.why_it_matters
                        )
                if dry_run:
                    settings.output_dir.mkdir(parents=True, exist_ok=True)
                    path = settings.output_dir / (
                        f"digest-{end:%Y-%m-%d}-{subscriber.subscriber_id}.html"
                    )
                    path.write_text(html, encoding="utf-8")
                    path.with_suffix(".txt").write_text(plain, encoding="utf-8")
                    previews.append(path)
                    continue
                if not settings.smtp_username or not settings.smtp_app_password:
                    raise RuntimeError("SMTP_USERNAME and SMTP_APP_PASSWORD are required to send")
                unsubscribe_post_url = ""
                if subscriber.subscriber_id in database_subscriber_ids:
                    management_token, unsubscribe_token = storage.create_delivery_tokens(subscriber.subscriber_id)
                    base = settings.public_url.rstrip("/")
                    manage_url = f"{base}/manage?{urlencode({'token': management_token})}"
                    unsubscribe_url = f"{base}/unsubscribe?{urlencode({'token': unsubscribe_token})}"
                    unsubscribe_post_url = f"{base}/api/subscription/unsubscribe?{urlencode({'token': unsubscribe_token})}"
                    _, delivery_html, delivery_plain = render(digest, manage_url=manage_url, unsubscribe_url=unsubscribe_url)
                else:
                    delivery_html, delivery_plain = html, plain
                message = build_message(subject, delivery_plain, delivery_html, settings.smtp_username,
                                        subscriber.email, unsubscribe_url=unsubscribe_post_url)
                for attempt in range(2):
                    try:
                        message_id = send_email(
                            message, settings.smtp_username, settings.smtp_app_password
                        )
                        storage.delivery_succeeded(subscriber.subscriber_id, digest_id, message_id)
                        sent += 1
                        break
                    except Exception as exc:
                        category, permanent, global_failure = classify_smtp_error(exc)
                        if global_failure:
                            storage.record_delivery_attempt(
                                subscriber.subscriber_id, digest_id, "failed", category, str(exc)
                            )
                            storage.mark_error(digest_id, str(exc))
                            raise
                        transient = category == "recipient_transient"
                        if transient and attempt == 0:
                            storage.record_delivery_attempt(
                                subscriber.subscriber_id, digest_id, "retry", category, str(exc)
                            )
                            continue
                        storage.delivery_failed(
                            subscriber.subscriber_id, digest_id, category, str(exc),
                            permanent=permanent,
                        )
                        failed += 1
                        break

            if dry_run:
                return previews, (
                    f"Generated {len(previews)} subscriber preview(s) with "
                    f"{total_items} total selected item(s)."
                )
            if failed:
                raise RuntimeError(
                    f"Sent {sent} subscriber digest(s); {failed} recipient(s) failed after processing the batch."
                )
            return [], f"Sent {sent} subscriber digest(s) with {total_items} total selected item(s)."
        finally:
            storage.close()
