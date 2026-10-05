from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Item
from .text import canonicalize_url, fingerprint

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,source TEXT NOT NULL,source_type TEXT NOT NULL,category TEXT NOT NULL,title TEXT NOT NULL,url TEXT NOT NULL,canonical_url TEXT NOT NULL,published_at TEXT NOT NULL,excerpt TEXT NOT NULL,author TEXT NOT NULL,engagement INTEGER NOT NULL DEFAULT 0,location TEXT NOT NULL DEFAULT '',company TEXT NOT NULL DEFAULT '',remote INTEGER NOT NULL DEFAULT 0,score REAL NOT NULL DEFAULT 0,first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS digests (id INTEGER PRIMARY KEY,subscriber_id TEXT NOT NULL,cutoff_start TEXT NOT NULL,cutoff_end TEXT NOT NULL,subject TEXT NOT NULL,html TEXT NOT NULL,plain_text TEXT NOT NULL,delivered_at TEXT,error TEXT NOT NULL DEFAULT '',UNIQUE(subscriber_id,cutoff_end));
CREATE TABLE IF NOT EXISTS digest_items (digest_id INTEGER NOT NULL REFERENCES digests(id),item_id INTEGER NOT NULL REFERENCES items(id),section TEXT NOT NULL,summary TEXT NOT NULL,why_it_matters TEXT NOT NULL DEFAULT '',PRIMARY KEY(digest_id,item_id));
CREATE TABLE IF NOT EXISTS source_runs (id INTEGER PRIMARY KEY,cutoff_end TEXT NOT NULL,source TEXT NOT NULL,ok INTEGER NOT NULL,error TEXT NOT NULL DEFAULT '',fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""
SUBSCRIBER_SCHEMA = """
CREATE TABLE subscribers (id TEXT PRIMARY KEY,email TEXT NOT NULL COLLATE NOCASE UNIQUE,preset_id TEXT NOT NULL,config_overrides TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','unsubscribed','disabled')),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,confirmed_at TEXT,unsubscribed_at TEXT,disabled_at TEXT,disable_reason TEXT NOT NULL DEFAULT '',pending_preset_id TEXT,consecutive_failures INTEGER NOT NULL DEFAULT 0,last_delivery_at TEXT);
"""
LIFECYCLE_SCHEMA = """
CREATE TABLE subscriber_tokens (id INTEGER PRIMARY KEY,subscriber_id TEXT NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,purpose TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,consumed_at TEXT);
CREATE INDEX subscriber_tokens_lookup ON subscriber_tokens(token_hash,purpose);
CREATE TABLE delivery_attempts (id INTEGER PRIMARY KEY,subscriber_id TEXT NOT NULL,digest_id INTEGER,attempted_at TEXT NOT NULL,status TEXT NOT NULL,error_category TEXT NOT NULL DEFAULT '',error_message TEXT NOT NULL DEFAULT '',provider_message_id TEXT);
CREATE INDEX delivery_attempts_subscriber ON delivery_attempts(subscriber_id,attempted_at);
"""
MANAGEMENT_SCHEMA = """
CREATE TABLE management_sessions (id INTEGER PRIMARY KEY,subscriber_id TEXT NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,session_hash TEXT NOT NULL UNIQUE,csrf_hash TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,revoked_at TEXT);
CREATE INDEX management_sessions_lookup ON management_sessions(session_hash);
"""

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()

def _execute_script(connection: sqlite3.Connection, script: str) -> None:
    """Run simple migration DDL without executescript's implicit commit."""
    for statement in script.split(";"):
        if statement.strip():
            connection.execute(statement)

class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
            row = self.connection.execute("SELECT version FROM schema_version").fetchone()
            version = int(row[0]) if row else 0
            if not row: self.connection.execute("INSERT INTO schema_version VALUES(0)")
            if version < 1:
                # A legacy digest table must be renamed before the current schema is created.
                columns = {r[1] for r in self.connection.execute("PRAGMA table_info(digests)")}
                legacy = bool(columns and "subscriber_id" not in columns)
                if legacy:
                    self.connection.execute("ALTER TABLE digest_items RENAME TO legacy_digest_items")
                    self.connection.execute("ALTER TABLE digests RENAME TO legacy_digests")
                _execute_script(self.connection, BASE_SCHEMA)
                if legacy:
                    self.connection.execute("INSERT INTO digests SELECT id,'default',cutoff_start,cutoff_end,subject,html,plain_text,delivered_at,error FROM legacy_digests")
                    self.connection.execute("INSERT INTO digest_items SELECT * FROM legacy_digest_items")
                    self.connection.execute("DROP TABLE legacy_digest_items")
                    self.connection.execute("DROP TABLE legacy_digests")
                self.connection.execute("UPDATE schema_version SET version=1")
            if version < 2:
                exists = self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscribers'").fetchone()
                if not exists:
                    _execute_script(self.connection, SUBSCRIBER_SCHEMA)
                else:
                    columns = {r[1] for r in self.connection.execute("PRAGMA table_info(subscribers)")}
                    if "pending_preset_id" not in columns:
                        self.connection.execute("ALTER TABLE subscribers RENAME TO legacy_subscribers")
                        _execute_script(self.connection, SUBSCRIBER_SCHEMA)
                        self.connection.execute("""INSERT INTO subscribers(id,email,preset_id,config_overrides,status,created_at,updated_at,confirmed_at,unsubscribed_at)
                            SELECT id,email,preset_id,config_overrides,status,created_at,updated_at,CASE WHEN status='active' THEN updated_at END,CASE WHEN status='unsubscribed' THEN updated_at END FROM legacy_subscribers""")
                        self.connection.execute("DROP TABLE legacy_subscribers")
                self.connection.execute("UPDATE schema_version SET version=2")
            if version < 3:
                _execute_script(self.connection, LIFECYCLE_SCHEMA)
                self.connection.execute("UPDATE schema_version SET version=3")
            if version < 4:
                _execute_script(self.connection, MANAGEMENT_SCHEMA)
                self.connection.execute("UPDATE schema_version SET version=4")
            self.connection.commit()
        except Exception:
            self.connection.rollback(); raise

    def close(self) -> None: self.connection.close()

    @staticmethod
    def token_hash(token: str) -> str: return hashlib.sha256(token.encode()).hexdigest()

    def request_confirmation(self, email: str, preset_id: str, *, now: datetime | None = None,
                             enforce_cooldown: bool = False) -> tuple[sqlite3.Row, str | None]:
        now = now or _now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT * FROM subscribers WHERE email=? COLLATE NOCASE", (email,)).fetchone()
            if row is None:
                subscriber_id = f"web-{secrets.token_hex(8)}"
                self.connection.execute("INSERT INTO subscribers(id,email,preset_id,status) VALUES(?,?,?,'pending')", (subscriber_id,email,preset_id))
            elif row["status"] == "disabled":
                self.connection.commit(); return row, None
            else:
                subscriber_id = str(row["id"])
                if row["status"] == "active":
                    pending = preset_id if preset_id != row["preset_id"] else None
                    self.connection.execute("UPDATE subscribers SET email=?,pending_preset_id=?,updated_at=? WHERE id=?", (email,pending,_iso(now),subscriber_id))
                else:
                    self.connection.execute("UPDATE subscribers SET email=?,preset_id=?,pending_preset_id=NULL,status='pending',unsubscribed_at=NULL,updated_at=? WHERE id=?", (email,preset_id,_iso(now),subscriber_id))
            latest = self.connection.execute("SELECT created_at FROM subscriber_tokens WHERE subscriber_id=? AND purpose='confirmation' ORDER BY id DESC LIMIT 1", (subscriber_id,)).fetchone()
            if enforce_cooldown and latest and now - datetime.fromisoformat(latest[0]) < timedelta(minutes=10):
                self.connection.commit()
                return self.connection.execute("SELECT * FROM subscribers WHERE id=?",(subscriber_id,)).fetchone(), None
            self.connection.execute("UPDATE subscriber_tokens SET consumed_at=? WHERE subscriber_id=? AND purpose='confirmation' AND consumed_at IS NULL", (_iso(now),subscriber_id))
            token = secrets.token_urlsafe(32)
            self.connection.execute("INSERT INTO subscriber_tokens(subscriber_id,purpose,token_hash,created_at,expires_at) VALUES(?,?,?,?,?)", (subscriber_id,"confirmation",self.token_hash(token),_iso(now),_iso(now+timedelta(hours=24))))
            self.connection.commit()
            return self.connection.execute("SELECT * FROM subscribers WHERE id=?",(subscriber_id,)).fetchone(), token
        except Exception:
            self.connection.rollback(); raise

    def inspect_confirmation(self, token: str, *, now: datetime | None = None) -> bool:
        return self.connection.execute("SELECT 1 FROM subscriber_tokens WHERE token_hash=? AND purpose='confirmation' AND consumed_at IS NULL AND expires_at>?", (self.token_hash(token),_iso(now or _now()))).fetchone() is not None

    def resend_confirmation(self, email: str, *, now: datetime | None = None) -> tuple[sqlite3.Row | None, str | None]:
        row = self.connection.execute("SELECT * FROM subscribers WHERE email=? COLLATE NOCASE", (email,)).fetchone()
        if row is None or row["status"] == "disabled":
            return row, None
        preset = row["pending_preset_id"] or row["preset_id"]
        return self.request_confirmation(email, preset, now=now, enforce_cooldown=True)

    def confirm(self, token: str, *, now: datetime | None = None) -> bool:
        now = now or _now(); self.connection.execute("BEGIN IMMEDIATE")
        try:
            record = self.connection.execute("SELECT * FROM subscriber_tokens WHERE token_hash=? AND purpose='confirmation' AND consumed_at IS NULL AND expires_at>?", (self.token_hash(token),_iso(now))).fetchone()
            if record is None: self.connection.rollback(); return False
            if not self.connection.execute("UPDATE subscriber_tokens SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (_iso(now),record["id"])).rowcount:
                self.connection.rollback(); return False
            activated = self.connection.execute(
                """UPDATE subscribers
                   SET preset_id=COALESCE(pending_preset_id,preset_id),
                       pending_preset_id=NULL,status='active',
                       confirmed_at=COALESCE(confirmed_at,?),unsubscribed_at=NULL,updated_at=?
                   WHERE id=? AND status!='disabled'""",
                (_iso(now), _iso(now), record["subscriber_id"]),
            ).rowcount
            self.connection.commit()
            return bool(activated)
        except Exception:
            self.connection.rollback(); raise

    def upsert_subscriber(self, email: str, preset_id: str) -> tuple[sqlite3.Row,bool]:
        row = self.connection.execute("SELECT id FROM subscribers WHERE email=? COLLATE NOCASE",(email,)).fetchone(); created = row is None; now = _iso(_now())
        if created:
            subscriber_id=f"web-{secrets.token_hex(8)}"; self.connection.execute("INSERT INTO subscribers(id,email,preset_id,status,confirmed_at) VALUES(?,?,?,'active',?)",(subscriber_id,email,preset_id,now))
        else:
            subscriber_id=str(row["id"]); self.connection.execute("UPDATE subscribers SET email=?,preset_id=?,status='active',confirmed_at=COALESCE(confirmed_at,?),updated_at=? WHERE id=?",(email,preset_id,now,now,subscriber_id))
        return self.connection.execute("SELECT * FROM subscribers WHERE id=?",(subscriber_id,)).fetchone(),created

    def active_subscribers(self) -> list[tuple[str,str,str,dict[str,object]]]:
        result=[]
        for row in self.connection.execute("SELECT id,email,preset_id,config_overrides FROM subscribers WHERE status='active' ORDER BY created_at,id"):
            overrides=json.loads(row["config_overrides"])
            if not isinstance(overrides,dict): raise ValueError(f"subscriber {row['id']!r} has invalid config overrides")
            result.append((row["id"],row["email"],row["preset_id"],overrides))
        return result

    def validate_preset_references(self, preset_ids: set[str]) -> None:
        unknown = self.connection.execute(
            "SELECT id,preset_id FROM subscribers WHERE preset_id NOT IN ({}) ORDER BY id".format(
                ",".join("?" for _ in preset_ids) or "NULL"
            ), tuple(sorted(preset_ids)),
        ).fetchall()
        if unknown:
            details = ", ".join(f"{row['id']!r} -> {row['preset_id']!r}" for row in unknown)
            raise ValueError(f"database subscribers reference unknown presets: {details}")

    def create_delivery_tokens(self, subscriber_id: str, *, now: datetime | None = None) -> tuple[str, str]:
        now = now or _now()
        tokens: list[str] = []
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT status FROM subscribers WHERE id=?", (subscriber_id,)).fetchone()
            if row is None or row["status"] != "active":
                raise ValueError("management links require an active database subscriber")
            for purpose in ("management", "unsubscribe"):
                token = secrets.token_urlsafe(32)
                self.connection.execute(
                    "INSERT INTO subscriber_tokens(subscriber_id,purpose,token_hash,created_at,expires_at) VALUES(?,?,?,?,?)",
                    (subscriber_id, purpose, self.token_hash(token), _iso(now), _iso(now + timedelta(days=7))),
                )
                tokens.append(token)
            self.connection.commit()
            return tokens[0], tokens[1]
        except Exception:
            self.connection.rollback(); raise

    def inspect_token(self, token: str, purpose: str, *, now: datetime | None = None) -> sqlite3.Row | None:
        if purpose not in {"management", "unsubscribe"}:
            return None
        return self.connection.execute(
            """SELECT s.* FROM subscriber_tokens t JOIN subscribers s ON s.id=t.subscriber_id
               WHERE t.token_hash=? AND t.purpose=? AND t.consumed_at IS NULL AND t.expires_at>?""",
            (self.token_hash(token), purpose, _iso(now or _now())),
        ).fetchone()

    def create_management_session(self, token: str, *, now: datetime | None = None) -> tuple[str, str] | None:
        now = now or _now(); self.connection.execute("BEGIN IMMEDIATE")
        try:
            record = self.connection.execute(
                """SELECT t.id,t.subscriber_id,s.status FROM subscriber_tokens t
                   JOIN subscribers s ON s.id=t.subscriber_id
                   WHERE t.token_hash=? AND t.purpose='management' AND t.consumed_at IS NULL AND t.expires_at>?""",
                (self.token_hash(token), _iso(now)),
            ).fetchone()
            if record is None or record["status"] == "disabled":
                self.connection.rollback(); return None
            if not self.connection.execute("UPDATE subscriber_tokens SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (_iso(now), record["id"])).rowcount:
                self.connection.rollback(); return None
            session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.connection.execute(
                "INSERT INTO management_sessions(subscriber_id,session_hash,csrf_hash,created_at,expires_at) VALUES(?,?,?,?,?)",
                (record["subscriber_id"], self.token_hash(session), self.token_hash(csrf), _iso(now), _iso(now + timedelta(minutes=30))),
            )
            self.connection.commit(); return session, csrf
        except Exception:
            self.connection.rollback(); raise

    def session_subscriber(self, session: str, *, csrf: str | None = None, now: datetime | None = None) -> sqlite3.Row | None:
        if not session:
            return None
        params: list[str] = [self.token_hash(session), _iso(now or _now())]
        csrf_clause = ""
        if csrf is not None:
            csrf_clause = " AND m.csrf_hash=?"
            params.append(self.token_hash(csrf))
        return self.connection.execute(
            """SELECT s.* FROM management_sessions m JOIN subscribers s ON s.id=m.subscriber_id
               WHERE m.session_hash=? AND m.revoked_at IS NULL AND m.expires_at>?""" + csrf_clause,
            tuple(params),
        ).fetchone()

    def update_preset(self, session: str, csrf: str, preset_id: str, *, now: datetime | None = None) -> bool:
        subscriber = self.session_subscriber(session, csrf=csrf, now=now)
        if subscriber is None or subscriber["status"] == "disabled": return False
        self.connection.execute("UPDATE subscribers SET preset_id=?,pending_preset_id=NULL,updated_at=? WHERE id=?", (preset_id, _iso(now or _now()), subscriber["id"]))
        return True

    def unsubscribe(self, token: str, *, now: datetime | None = None) -> bool:
        now = now or _now(); self.connection.execute("BEGIN IMMEDIATE")
        try:
            record = self.connection.execute(
                """SELECT t.id,t.subscriber_id,t.consumed_at,s.status FROM subscriber_tokens t
                   JOIN subscribers s ON s.id=t.subscriber_id
                   WHERE t.token_hash=? AND t.purpose='unsubscribe' AND t.expires_at>?""",
                (self.token_hash(token), _iso(now)),
            ).fetchone()
            if record is None or record["status"] == "disabled": self.connection.rollback(); return False
            self.connection.execute("UPDATE subscriber_tokens SET consumed_at=COALESCE(consumed_at,?) WHERE id=?", (_iso(now), record["id"]))
            self.connection.execute("UPDATE subscribers SET status='unsubscribed',unsubscribed_at=COALESCE(unsubscribed_at,?),updated_at=? WHERE id=?", (_iso(now), _iso(now), record["subscriber_id"]))
            self.connection.commit(); return True
        except Exception:
            self.connection.rollback(); raise

    def resubscribe(self, session: str, csrf: str, *, now: datetime | None = None) -> tuple[sqlite3.Row | None, str | None]:
        subscriber = self.session_subscriber(session, csrf=csrf, now=now)
        if subscriber is None or subscriber["status"] != "unsubscribed": return subscriber, None
        return self.request_confirmation(subscriber["email"], subscriber["preset_id"], now=now)

    def record_delivery_attempt(self, subscriber_id: str, digest_id: int|None, status: str, category: str="", error: str="", message_id: str|None=None) -> None:
        self.connection.execute("INSERT INTO delivery_attempts(subscriber_id,digest_id,attempted_at,status,error_category,error_message,provider_message_id) VALUES(?,?,?,?,?,?,?)",(subscriber_id,digest_id,_iso(_now()),status,category," ".join(str(error).split())[:500],message_id))

    def delivery_succeeded(self, subscriber_id: str, digest_id: int, message_id: str|None=None) -> None:
        now=_iso(_now()); self.record_delivery_attempt(subscriber_id,digest_id,"success",message_id=message_id); self.connection.execute("UPDATE digests SET delivered_at=?,error='' WHERE id=?",(now,digest_id)); self.connection.execute("UPDATE subscribers SET consecutive_failures=0,last_delivery_at=? WHERE id=?",(now,subscriber_id))

    def delivery_failed(self, subscriber_id: str, digest_id: int, category: str, error: str, *, permanent: bool=False) -> None:
        clean=" ".join(str(error).split())[:500]; self.record_delivery_attempt(subscriber_id,digest_id,"failed",category,clean); self.mark_error(digest_id,clean)
        row=self.connection.execute("SELECT consecutive_failures FROM subscribers WHERE id=?",(subscriber_id,)).fetchone()
        if row:
            count=int(row[0])+1; disable=permanent or count>=3
            self.connection.execute("UPDATE subscribers SET consecutive_failures=?,status=CASE WHEN ? THEN 'disabled' ELSE status END,disabled_at=CASE WHEN ? THEN ? ELSE disabled_at END,disable_reason=CASE WHEN ? THEN ? ELSE disable_reason END WHERE id=?",(count,disable,disable,_iso(_now()),disable,category,subscriber_id))

    def upsert_item(self,item:Item)->int:
        item.canonical_url=canonicalize_url(item.url); key=fingerprint(item.url,item.title)
        self.connection.execute("INSERT INTO items(fingerprint,source,source_type,category,title,url,canonical_url,published_at,excerpt,author,engagement,location,company,remote,score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET score=excluded.score,excerpt=excluded.excerpt",(key,item.source,item.source_type,item.category,item.title,item.url,item.canonical_url,item.published_at.isoformat(),item.excerpt,item.author,item.engagement,item.location,item.company,item.remote,item.score))
        row=self.connection.execute("SELECT id FROM items WHERE fingerprint=?",(key,)).fetchone(); item.item_id=int(row["id"]); return item.item_id
    def is_delivered(self,cutoff_end:datetime,subscriber_id:str="default")->bool:
        row=self.connection.execute("SELECT delivered_at FROM digests WHERE subscriber_id=? AND cutoff_end=?",(subscriber_id,cutoff_end.isoformat())).fetchone(); return bool(row and row["delivered_at"])
    def save_digest(self,start:datetime,end:datetime,subject:str,html:str,plain:str,subscriber_id:str="default")->int:
        self.connection.execute("INSERT INTO digests(subscriber_id,cutoff_start,cutoff_end,subject,html,plain_text) VALUES(?,?,?,?,?,?) ON CONFLICT(subscriber_id,cutoff_end) DO UPDATE SET subject=excluded.subject,html=excluded.html,plain_text=excluded.plain_text,error=''",(subscriber_id,start.isoformat(),end.isoformat(),subject,html,plain)); row=self.connection.execute("SELECT id FROM digests WHERE subscriber_id=? AND cutoff_end=?",(subscriber_id,end.isoformat())).fetchone(); self.connection.execute("DELETE FROM digest_items WHERE digest_id=?",(row["id"],)); return int(row["id"])
    def attach_item(self,digest_id:int,item:Item,section:str,summary:str,why:str)->None:
        assert item.item_id is not None; self.connection.execute("INSERT OR REPLACE INTO digest_items VALUES(?,?,?,?,?)",(digest_id,item.item_id,section,summary,why))
    def mark_delivered(self,digest_id:int)->None: self.connection.execute("UPDATE digests SET delivered_at=?,error='' WHERE id=?",(_iso(_now()),digest_id))
    def mark_error(self,digest_id:int,error:str)->None: self.connection.execute("UPDATE digests SET error=? WHERE id=?",(error[:1000],digest_id))
    def source_result(self,cutoff_end:datetime,source:str,ok:bool,error:str="")->None: self.connection.execute("INSERT INTO source_runs(cutoff_end,source,ok,error) VALUES(?,?,?,?)",(cutoff_end.isoformat(),source,ok,error[:1000]))
