"""Revisioned authored source data, separate from captured mirror evidence and UI layout."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from paths import DATA


DB_PATH = DATA / "scatter-authored.sqlite"
KINDS = {"profile", "post", "comment"}
ARCHIVE_ID = "scatter-authored/1"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _id(value, label):
    if not isinstance(value, str) or not 1 <= len(value) <= 120 or any(c.isspace() for c in value):
        raise ValueError(f"Invalid {label}")
    return value


def _content(kind, content):
    if kind not in KINDS or not isinstance(content, dict):
        raise ValueError("Custom source needs a supported kind and object content")
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > 128_000:
        raise ValueError("Custom source exceeds 128 KB; use smaller records")
    if kind == "profile" and not isinstance(content.get("name"), str):
        raise ValueError("Custom profile needs a name")
    if kind == "post" and not isinstance(content.get("profile_id"), str):
        raise ValueError("Custom post needs a profile_id")
    if kind == "comment" and not isinstance(content.get("post_id"), str):
        raise ValueError("Custom comment needs a post_id")
    return encoded


class AuthoredSources:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS authored_records (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                    head INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS authored_revisions (
                    record_id TEXT NOT NULL REFERENCES authored_records(id),
                    number INTEGER NOT NULL, parent INTEGER, command_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL, content_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(record_id,number),
                    UNIQUE(record_id,command_id));
                CREATE INDEX IF NOT EXISTS authored_recent ON authored_records(kind,created_at DESC);
                CREATE TRIGGER IF NOT EXISTS authored_revision_no_update
                    BEFORE UPDATE ON authored_revisions
                    BEGIN SELECT RAISE(ABORT,'Authored source revisions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS authored_revision_no_delete
                    BEFORE DELETE ON authored_revisions
                    BEGIN SELECT RAISE(ABORT,'Authored source revisions are immutable'); END;
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=15000")
            yield db
            db.commit()
        finally:
            db.close()

    def create(self, kind: str, title: str, content: dict, record_id: str | None = None) -> dict:
        encoded = _content(kind, content)
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
            raise ValueError("Custom source title needs 1–160 characters")
        record_id = _id(record_id or "custom-" + uuid.uuid4().hex, "custom source ID")
        digest, now = _hash(content), _now()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO authored_records VALUES (?,?,?,?,?)",
                       (record_id, kind, title.strip(), 1, now))
            db.execute("INSERT INTO authored_revisions VALUES (?,?,?,?,?,?,?)",
                       (record_id, 1, None, "initial", digest, encoded, now))
        return self.read(record_id)

    def revise(self, record_id: str, expected_revision: int,
               command_id: str, content: dict) -> dict:
        _id(record_id, "custom source ID")
        _id(command_id, "command ID")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("Expected source revision is required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            record = db.execute("SELECT kind,head FROM authored_records WHERE id=?",
                                (record_id,)).fetchone()
            if not record:
                raise LookupError("Custom source not found")
            encoded = _content(record["kind"], content)
            digest = _hash(content)
            prior = db.execute("SELECT number,content_hash FROM authored_revisions "
                               "WHERE record_id=? AND command_id=?", (record_id, command_id)).fetchone()
            if prior:
                if prior["content_hash"] != digest:
                    raise ValueError("Command ID was reused with different content")
                number = prior["number"]
            else:
                if record["head"] != expected_revision:
                    raise ValueError(f"Source revision changed to {record['head']}")
                number = expected_revision + 1
                db.execute("INSERT INTO authored_revisions VALUES (?,?,?,?,?,?,?)",
                           (record_id, number, expected_revision, command_id,
                            digest, encoded, _now()))
                db.execute("UPDATE authored_records SET head=? WHERE id=? AND head=?",
                           (number, record_id, expected_revision))
        return self.read(record_id, number)

    def read(self, record_id: str, revision: int | None = None) -> dict:
        _id(record_id, "custom source ID")
        with self._db() as db:
            record = db.execute("SELECT * FROM authored_records WHERE id=?", (record_id,)).fetchone()
            if not record:
                raise LookupError("Custom source not found")
            number = record["head"] if revision is None else revision
            row = db.execute("SELECT * FROM authored_revisions WHERE record_id=? AND number=?",
                             (record_id, number)).fetchone()
            if not row:
                raise LookupError("Custom source revision not found")
        content = json.loads(row["content_json"])
        if _hash(content) != row["content_hash"]:
            raise RuntimeError("Custom source checksum mismatch")
        return {"id": record_id, "kind": record["kind"], "title": record["title"],
                "revision": number, "head": record["head"],
                "content_hash": row["content_hash"], "content": content,
                "created_at": row["created_at"]}

    def list(self, kind: str | None = None, cursor: int = 0, limit: int = 50) -> dict:
        if kind is not None and kind not in KINDS:
            raise ValueError("Unsupported custom source kind")
        if type(cursor) is not int or type(limit) is not int or cursor < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid custom source page range")
        with self._db() as db:
            where, args = ("WHERE kind=?", (kind,)) if kind else ("", ())
            total = db.execute(f"SELECT count(*) FROM authored_records {where}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM authored_records {where} ORDER BY created_at DESC,id DESC "
                              "LIMIT ? OFFSET ?", (*args, limit, cursor)).fetchall()
        return {"items": [dict(row) for row in rows], "total": total,
                "next_cursor": cursor + len(rows) if cursor + len(rows) < total else None}

    def pin(self, record_id: str, revision: int | None = None) -> dict:
        record = self.read(record_id, revision)
        return {"archive_id": ARCHIVE_ID, "study_id": "authored",
                "entity_kind": record["kind"], "entity_id": record_id,
                "content_hash": record["content_hash"],
                "source_revision": record["revision"]}

    def resolve(self, reference: dict) -> dict:
        if not isinstance(reference, dict) or reference.get("archive_id") != ARCHIVE_ID:
            raise ValueError("Unknown custom source reference")
        if (type(reference.get("source_revision")) is not int or
                reference["source_revision"] < 1):
            raise ValueError("Custom source pin needs an immutable source revision")
        try:
            record = self.read(reference.get("entity_id"), reference.get("source_revision"))
        except LookupError:
            return {"status": "pin_missing", "entity_id": reference.get("entity_id")}
        if (record["kind"] != reference.get("entity_kind") or
                reference.get("study_id") != "authored" or
                record["content_hash"] != reference.get("content_hash")):
            return {"status": "reference_changed", "entity_id": record["id"]}
        return {"status": "pinned", "entity_id": record["id"],
                "source_revision": record["revision"], "head": record["head"],
                "content_hash": record["content_hash"]}

    def read_pinned(self, reference: dict) -> dict:
        if self.resolve(reference)["status"] != "pinned":
            raise ValueError("Custom source pin is invalid")
        record = self.read(reference["entity_id"], reference["source_revision"])
        return {"kind": record["kind"], "id": record["id"],
                "study_id": "authored", "content_hash": record["content_hash"],
                "source_revision": record["revision"], "content": record["content"]}
