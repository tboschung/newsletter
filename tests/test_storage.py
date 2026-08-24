from datetime import datetime, timezone

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

