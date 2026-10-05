from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Item
from .text import canonicalize_url, fingerprint

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  source_type TEXT NOT NULL,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  published_at TEXT NOT NULL,
  excerpt TEXT NOT NULL,
  author TEXT NOT NULL,
  engagement INTEGER NOT NULL DEFAULT 0,
  location TEXT NOT NULL DEFAULT '',
  company TEXT NOT NULL DEFAULT '',
  remote INTEGER NOT NULL DEFAULT 0,
  score REAL NOT NULL DEFAULT 0,
  first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS digests (
  id INTEGER PRIMARY KEY,
  subscriber_id TEXT NOT NULL,
  cutoff_start TEXT NOT NULL,
  cutoff_end TEXT NOT NULL,
  subject TEXT NOT NULL,
  html TEXT NOT NULL,
  plain_text TEXT NOT NULL,
  delivered_at TEXT,
  error TEXT NOT NULL DEFAULT '',
  UNIQUE(subscriber_id, cutoff_end)
);
CREATE TABLE IF NOT EXISTS digest_items (
  digest_id INTEGER NOT NULL REFERENCES digests(id),
  item_id INTEGER NOT NULL REFERENCES items(id),
  section TEXT NOT NULL,
  summary TEXT NOT NULL,
  why_it_matters TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (digest_id, item_id)
);
CREATE TABLE IF NOT EXISTS source_runs (
  id INTEGER PRIMARY KEY,
  cutoff_end TEXT NOT NULL,
  source TEXT NOT NULL,
  ok INTEGER NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS subscribers (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL COLLATE NOCASE UNIQUE,
  preset_id TEXT NOT NULL,
  config_overrides TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','unsubscribed')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._migrate_subscribers()
        self.connection.executescript(SCHEMA)

    def _migrate_subscribers(self) -> None:
        """Preserve archives created before digests were subscriber-specific."""
        table = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='digests'"
        ).fetchone()
        if not table:
            return
        columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(digests)").fetchall()
        }
        if "subscriber_id" in columns:
            return
        self.connection.executescript(
            """
            ALTER TABLE digest_items RENAME TO legacy_digest_items;
            ALTER TABLE digests RENAME TO legacy_digests;
            CREATE TABLE digests (
              id INTEGER PRIMARY KEY,
              subscriber_id TEXT NOT NULL,
              cutoff_start TEXT NOT NULL,
              cutoff_end TEXT NOT NULL,
              subject TEXT NOT NULL,
              html TEXT NOT NULL,
              plain_text TEXT NOT NULL,
              delivered_at TEXT,
              error TEXT NOT NULL DEFAULT '',
              UNIQUE(subscriber_id, cutoff_end)
            );
            CREATE TABLE digest_items (
              digest_id INTEGER NOT NULL REFERENCES digests(id),
              item_id INTEGER NOT NULL REFERENCES items(id),
              section TEXT NOT NULL,
              summary TEXT NOT NULL,
              why_it_matters TEXT NOT NULL DEFAULT '',
              PRIMARY KEY (digest_id, item_id)
            );
            INSERT INTO digests
              (id,subscriber_id,cutoff_start,cutoff_end,subject,html,plain_text,delivered_at,error)
            SELECT id,'default',cutoff_start,cutoff_end,subject,html,plain_text,delivered_at,error
              FROM legacy_digests;
            INSERT INTO digest_items SELECT * FROM legacy_digest_items;
            DROP TABLE legacy_digest_items;
            DROP TABLE legacy_digests;
            """
        )

    def close(self) -> None:
        self.connection.close()

    def upsert_subscriber(self, email: str, preset_id: str) -> tuple[sqlite3.Row, bool]:
        existing = self.connection.execute(
            "SELECT id FROM subscribers WHERE email=? COLLATE NOCASE", (email,)
        ).fetchone()
        created = existing is None
        if created:
            subscriber_id = f"web-{secrets.token_hex(8)}"
            self.connection.execute(
                "INSERT INTO subscribers(id,email,preset_id) VALUES(?,?,?)",
                (subscriber_id, email, preset_id),
            )
        else:
            subscriber_id = str(existing["id"])
            self.connection.execute(
                """UPDATE subscribers
                   SET email=?,preset_id=?,status='active',updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (email, preset_id, subscriber_id),
            )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM subscribers WHERE id=?", (subscriber_id,)
        ).fetchone()
        assert row is not None
        return row, created

    def active_subscribers(self) -> list[tuple[str, str, str, dict[str, object]]]:
        rows = self.connection.execute(
            """SELECT id,email,preset_id,config_overrides FROM subscribers
               WHERE status='active' ORDER BY created_at,id"""
        ).fetchall()
        subscribers = []
        for row in rows:
            overrides = json.loads(row["config_overrides"])
            if not isinstance(overrides, dict):
                raise ValueError(f"subscriber {row['id']!r} has invalid config overrides")
            subscribers.append((row["id"], row["email"], row["preset_id"], overrides))
        return subscribers

    def upsert_item(self, item: Item) -> int:
        item.canonical_url = canonicalize_url(item.url)
        key = fingerprint(item.url, item.title)
        self.connection.execute(
            """INSERT INTO items
            (fingerprint,source,source_type,category,title,url,canonical_url,published_at,
             excerpt,author,engagement,location,company,remote,score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fingerprint) DO UPDATE SET score=excluded.score, excerpt=excluded.excerpt""",
            (key, item.source, item.source_type, item.category, item.title, item.url,
             item.canonical_url, item.published_at.isoformat(), item.excerpt, item.author,
             item.engagement, item.location, item.company, item.remote, item.score),
        )
        row = self.connection.execute("SELECT id FROM items WHERE fingerprint=?", (key,)).fetchone()
        self.connection.commit()
        item.item_id = int(row["id"])
        return item.item_id

    def is_delivered(self, cutoff_end: datetime, subscriber_id: str = "default") -> bool:
        row = self.connection.execute(
            "SELECT delivered_at FROM digests WHERE subscriber_id=? AND cutoff_end=?",
            (subscriber_id, cutoff_end.isoformat()),
        ).fetchone()
        return bool(row and row["delivered_at"])

    def save_digest(
        self, start: datetime, end: datetime, subject: str, html: str, plain: str,
        subscriber_id: str = "default",
    ) -> int:
        self.connection.execute(
            """INSERT INTO digests(subscriber_id,cutoff_start,cutoff_end,subject,html,plain_text)
            VALUES(?,?,?,?,?,?) ON CONFLICT(subscriber_id,cutoff_end) DO UPDATE SET
            subject=excluded.subject,html=excluded.html,plain_text=excluded.plain_text,error=''""",
            (subscriber_id, start.isoformat(), end.isoformat(), subject, html, plain),
        )
        row = self.connection.execute(
            "SELECT id FROM digests WHERE subscriber_id=? AND cutoff_end=?",
            (subscriber_id, end.isoformat()),
        ).fetchone()
        self.connection.execute("DELETE FROM digest_items WHERE digest_id=?", (row["id"],))
        self.connection.commit()
        return int(row["id"])

    def attach_item(self, digest_id: int, item: Item, section: str, summary: str, why: str) -> None:
        assert item.item_id is not None
        self.connection.execute(
            "INSERT OR REPLACE INTO digest_items VALUES(?,?,?,?,?)",
            (digest_id, item.item_id, section, summary, why),
        )
        self.connection.commit()

    def mark_delivered(self, digest_id: int) -> None:
        self.connection.execute(
            "UPDATE digests SET delivered_at=?,error='' WHERE id=?",
            (datetime.now().astimezone().isoformat(), digest_id),
        )
        self.connection.commit()

    def mark_error(self, digest_id: int, error: str) -> None:
        self.connection.execute("UPDATE digests SET error=? WHERE id=?", (error[:1000], digest_id))
        self.connection.commit()

    def source_result(self, cutoff_end: datetime, source: str, ok: bool, error: str = "") -> None:
        self.connection.execute(
            "INSERT INTO source_runs(cutoff_end,source,ok,error) VALUES(?,?,?,?)",
            (cutoff_end.isoformat(), source, ok, error[:1000]),
        )
        self.connection.commit()
