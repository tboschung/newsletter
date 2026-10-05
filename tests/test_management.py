from datetime import datetime, timedelta, timezone

from morning_digest.storage import Storage


def test_management_credentials_are_scoped_hashed_and_expiring(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    subscriber, _ = storage.upsert_subscriber("reader@example.com", "brief")
    management, unsubscribe = storage.create_delivery_tokens(subscriber["id"], now=now)

    stored = storage.connection.execute(
        "SELECT purpose,token_hash FROM subscriber_tokens ORDER BY id"
    ).fetchall()
    assert [row["purpose"] for row in stored] == ["management", "unsubscribe"]
    assert all(row["token_hash"] not in {management, unsubscribe} for row in stored)
    assert storage.inspect_token(management, "unsubscribe", now=now) is None

    session, csrf = storage.create_management_session(management, now=now)
    assert storage.create_management_session(management, now=now) is None
    assert storage.session_subscriber(session, csrf="wrong", now=now) is None
    assert storage.session_subscriber(session, csrf=csrf, now=now + timedelta(minutes=29))
    assert storage.session_subscriber(session, csrf=csrf, now=now + timedelta(minutes=31)) is None
    session_row = storage.connection.execute(
        "SELECT session_hash,csrf_hash FROM management_sessions"
    ).fetchone()
    assert session_row["session_hash"] != session
    assert session_row["csrf_hash"] != csrf
    storage.close()


def test_unsubscribe_is_idempotent_and_resubscribe_requires_confirmation(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    subscriber, _ = storage.upsert_subscriber("reader@example.com", "brief")
    management, unsubscribe = storage.create_delivery_tokens(subscriber["id"], now=now)
    session, csrf = storage.create_management_session(management, now=now)

    assert storage.unsubscribe(unsubscribe, now=now)
    assert storage.unsubscribe(unsubscribe, now=now + timedelta(minutes=1))
    assert storage.active_subscribers() == []
    pending, confirmation = storage.resubscribe(
        session, csrf, now=now + timedelta(minutes=2)
    )
    assert pending["status"] == "pending"
    assert confirmation
    assert storage.active_subscribers() == []
    assert storage.confirm(confirmation, now=now + timedelta(minutes=3))
    assert storage.active_subscribers()[0][2] == "brief"
    storage.close()


def test_preset_update_preserves_overrides(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    subscriber, _ = storage.upsert_subscriber("reader@example.com", "brief")
    storage.connection.execute(
        "UPDATE subscribers SET config_overrides=? WHERE id=?",
        ('{"job_profile":"cv"}', subscriber["id"]),
    )
    management, _ = storage.create_delivery_tokens(subscriber["id"], now=now)
    session, csrf = storage.create_management_session(management, now=now)
    assert storage.update_preset(session, csrf, "full", now=now)
    updated = storage.connection.execute(
        "SELECT preset_id,config_overrides FROM subscribers WHERE id=?", (subscriber["id"],)
    ).fetchone()
    assert (updated["preset_id"], updated["config_overrides"]) == (
        "full", '{"job_profile":"cv"}'
    )
    storage.close()
