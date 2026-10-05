import sqlite3
from datetime import datetime, timedelta, timezone

from morning_digest.models import Item
from morning_digest.storage import Storage


def test_upsert_and_delivery_idempotency(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime(2026, 8, 12, 6, tzinfo=timezone.utc)
    item = Item("source", "rss", "AI release", "https://example.com/a?utm_source=x", now)
    first = storage.upsert_item(item)
    second = storage.upsert_item(item)
    assert first == second
    digest_id = storage.save_digest(now, now, "subject", "html", "plain")
    assert not storage.is_delivered(now)
    storage.mark_delivered(digest_id)
    assert storage.is_delivered(now)
    storage.close()


def test_delivery_is_tracked_per_subscriber(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime(2026, 8, 12, 6, tzinfo=timezone.utc)
    alice = storage.save_digest(now, now, "subject", "html", "plain", "alice")
    storage.save_digest(now, now, "subject", "html", "plain", "bob")
    storage.mark_delivered(alice)
    assert storage.is_delivered(now, "alice")
    assert not storage.is_delivered(now, "bob")
    storage.close()


def test_legacy_digest_schema_is_migrated_to_default_subscriber(tmp_path):
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE digests (
          id INTEGER PRIMARY KEY, cutoff_start TEXT NOT NULL, cutoff_end TEXT NOT NULL UNIQUE,
          subject TEXT NOT NULL, html TEXT NOT NULL, plain_text TEXT NOT NULL,
          delivered_at TEXT, error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE digest_items (
          digest_id INTEGER NOT NULL, item_id INTEGER NOT NULL, section TEXT NOT NULL,
          summary TEXT NOT NULL, why_it_matters TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (digest_id, item_id)
        );
    """)
    now = datetime(2026, 8, 12, 6, tzinfo=timezone.utc)
    connection.execute(
        "INSERT INTO digests VALUES(1,?,?,?,?,?,?,?)",
        (now.isoformat(), now.isoformat(), "subject", "html", "plain", now.isoformat(), ""),
    )
    connection.commit()
    connection.close()

    storage = Storage(path)
    assert storage.is_delivered(now, "default")
    storage.close()


def test_web_subscriber_signup_is_idempotent_and_keeps_override_slot(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    first, created = storage.upsert_subscriber("reader@example.com", "ai-briefing")
    second, created_again = storage.upsert_subscriber("READER@example.com", "full-signal")

    assert created
    assert not created_again
    assert first["id"] == second["id"]
    assert second["email"] == "READER@example.com"
    assert second["preset_id"] == "full-signal"
    active = storage.active_subscribers()
    assert active == [(second["id"], "READER@example.com", "full-signal", {})]
    storage.close()


def test_confirmation_is_hashed_one_time_and_activates_pending_subscriber(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime(2026, 8, 12, 6, tzinfo=timezone.utc)
    subscriber, token = storage.request_confirmation("reader@example.com", "ai-briefing", now=now)

    assert subscriber["status"] == "pending"
    assert token is not None
    stored = storage.connection.execute("SELECT token_hash FROM subscriber_tokens").fetchone()[0]
    assert stored != token
    assert storage.confirm(token, now=now + timedelta(minutes=1))
    assert not storage.confirm(token, now=now + timedelta(minutes=2))
    assert storage.active_subscribers()[0][2] == "ai-briefing"
    storage.close()


def test_active_preset_change_waits_for_confirmation_and_resend_cools_down(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime(2026, 8, 12, 6, tzinfo=timezone.utc)
    _, first = storage.request_confirmation("reader@example.com", "ai-briefing", now=now)
    assert first and storage.confirm(first, now=now + timedelta(minutes=1))

    subscriber, change = storage.request_confirmation(
        "READER@example.com", "full-signal", now=now + timedelta(minutes=2)
    )
    assert subscriber["status"] == "active"
    assert subscriber["preset_id"] == "ai-briefing"
    assert subscriber["pending_preset_id"] == "full-signal"
    _, resend = storage.resend_confirmation("reader@example.com", now=now + timedelta(minutes=3))
    assert resend is None
    assert change and storage.confirm(change, now=now + timedelta(minutes=4))
    assert storage.active_subscribers()[0][2] == "full-signal"
    storage.close()
