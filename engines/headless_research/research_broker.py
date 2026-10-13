"""Study-scoped read broker for Codex and Gemini research sessions.

Captured sources stay in the mirror. Live reads go only to the app-owned
background RedNote window. Every broker call gets an append-only receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from study_core import StudyEngine
from study_storage import SqliteArchive, SqliteStudies


HERE = Path(__file__).resolve().parent
HARVEST = HERE.parent / "01_Mining_App" / "Reel_Harvest"
sys.path.insert(0, str(HARVEST))
from harvest_mcp import HarvestClient, TOOL_BY_NAME, invoke  # noqa: E402


LIVE_TOOLS = {
    "codex_red_note_research_status", "codex_red_note_live_search",
    "codex_red_note_library_list", "codex_red_note_live_post",
    "codex_red_note_live_profile", "codex_red_note_library_scan_status",
}


class ResearchCancelled(RuntimeError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now():
    return datetime.now(timezone.utc).isoformat()


BROKER_TOOLS = [
    {"name": "study_search", "description": "Search exact captured study text with indexed source membership.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}, "kind": {"type": "string", "enum": ["all", "post", "comment"]},
         "cursor": {"type": "integer"}, "limit": {"type": "integer"}},
         "required": ["query"], "additionalProperties": False}},
    {"name": "study_source", "description": "Read one pinned captured source by kind and ID.",
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["profile", "post", "comment", "asset"]},
         "id": {"type": "string"}}, "required": ["kind", "id"], "additionalProperties": False}},
    {"name": "study_thread", "description": "Read one captured post with a page of pinned comments.",
     "inputSchema": {"type": "object", "properties": {
         "post_id": {"type": "string"}, "cursor": {"type": "integer"},
         "limit": {"type": "integer"}}, "required": ["post_id"], "additionalProperties": False}},
    {"name": "study_comment_profile", "description": "Resolve a pinned comment to its live commenter profile; optionally observe that profile in the app-owned background window without cloning it. Profile descriptions are self-reported, not verified demographics.",
     "inputSchema": {"type": "object", "properties": {
         "comment_id": {"type": "string"}, "visit": {"type": "boolean"}},
         "required": ["comment_id"], "additionalProperties": False}},
    {"name": "study_comment_media", "description": "Read a pinned comment with its currently archived ordered emoji/GIF/sticker/gift segments and attachment verification. Supplemental media may postdate the study pin and is labeled accordingly.",
     "inputSchema": {"type": "object", "properties": {
         "comment_id": {"type": "string"}},
         "required": ["comment_id"], "additionalProperties": False}},
] + [TOOL_BY_NAME[name] for name in sorted(LIVE_TOOLS)]


class ResearchSessionStore:
    def __init__(self, path: Path | None = None):
        self.path = path or HERE / "data" / "research-sessions.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, study_id TEXT NOT NULL, manifest_hash TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, question TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    result_json TEXT, error_class TEXT);
                CREATE TABLE IF NOT EXISTS calls (
                    session_id TEXT NOT NULL REFERENCES sessions(id), sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL, tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL, result_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL, PRIMARY KEY(session_id,sequence));
                CREATE INDEX IF NOT EXISTS calls_tool ON calls(session_id,tool_name,sequence);
                CREATE TRIGGER IF NOT EXISTS calls_no_update BEFORE UPDATE ON calls
                    BEGIN SELECT RAISE(ABORT,'Research receipts are immutable'); END;
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            if "owner_pid" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN owner_pid INTEGER")
            if "heartbeat_at" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN heartbeat_at TEXT")
            if "cancel_requested" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            db.commit()
        finally:
            db.close()

    def create(self, study, provider, model, question):
        session_id = "research-" + uuid.uuid4().hex
        with self._connect() as db:
            db.execute("""INSERT INTO sessions
                (id,study_id,manifest_hash,provider,model,question,state,created_at,updated_at,result_json,error_class)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (session_id, study["id"], study["manifest_hash"], provider, model,
                        question, "created", _now(), _now(), None, None))
            db.commit()
        return session_id

    def record(self, session_id, tool, arguments, result):
        serialized = _json(result)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sequence = db.execute("SELECT coalesce(max(sequence)+1,0) FROM calls WHERE session_id=?",
                                  (session_id,)).fetchone()[0]
            db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?)",
                       (session_id, sequence, _now(), tool, _json(arguments),
                        hashlib.sha256(serialized.encode("utf-8")).hexdigest(), serialized))
            db.commit()
        return sequence

    def finish(self, session_id, state, result=None, error=None):
        with self._connect() as db:
            db.execute("""UPDATE sessions SET state=?,updated_at=?,result_json=?,error_class=?,
                owner_pid=CASE WHEN ?='running' THEN ? ELSE NULL END,
                heartbeat_at=CASE WHEN ?='running' THEN ? ELSE heartbeat_at END WHERE id=?""",
                (state, _now(), _json(result) if result is not None else None, error,
                 state, os.getpid(), state, _now(), session_id))
            db.commit()

    def heartbeat(self, session_id):
        with self._connect() as db:
            db.execute("UPDATE sessions SET heartbeat_at=?,updated_at=? WHERE id=? AND state='running'",
                       (_now(), _now(), session_id))

    def request_cancel(self, session_id):
        with self._connect() as db:
            row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise LookupError("Research session not found")
            if row["state"] not in {"created", "running"}:
                return {"requested": False, "state": row["state"]}
            db.execute("UPDATE sessions SET cancel_requested=1,updated_at=? WHERE id=?",
                       (_now(), session_id))
            return {"requested": True, "state": row["state"]}

    def cancellation_requested(self, session_id):
        with self._connect() as db:
            row = db.execute("SELECT cancel_requested FROM sessions WHERE id=?", (session_id,)).fetchone()
            return bool(row and row[0])

    def recover_interrupted(self, stale_seconds=30):
        deadline = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
        recovered = []
        with self._connect() as db:
            for row in db.execute("SELECT id,heartbeat_at,owner_pid FROM sessions WHERE state='running'"):
                heartbeat = datetime.fromisoformat(row["heartbeat_at"]) if row["heartbeat_at"] else None
                owner_alive = False
                if row["owner_pid"]:
                    try:
                        os.kill(row["owner_pid"], 0)
                        owner_alive = True
                    except PermissionError:
                        owner_alive = True
                    except OSError:
                        pass
                if not owner_alive or heartbeat is None or heartbeat < deadline:
                    recovered.append(row["id"])
            for session_id in recovered:
                db.execute("UPDATE sessions SET state='interrupted',updated_at=?,owner_pid=NULL,error_class='WorkerLost' WHERE id=?",
                           (_now(), session_id))
        return {"interrupted": recovered}

    def read(self, session_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise LookupError("Research session not found")
            result = dict(row)
            result["result"] = json.loads(result.pop("result_json")) if result["result_json"] else None
            result["calls"] = [dict(item) for item in db.execute(
                "SELECT sequence,created_at,tool_name,result_hash FROM calls WHERE session_id=? ORDER BY sequence",
                (session_id,))]
            return result

    def list(self, limit=30):
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,study_id,provider,model,state,created_at,updated_at,heartbeat_at,cancel_requested "
                "FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,))]


class RedEngineToolBroker:
    def __init__(self, study_id: str, session_id: str, store: ResearchSessionStore | None = None):
        self.engine = StudyEngine(SqliteArchive(), SqliteStudies())
        self.study = self.engine.get(study_id)
        self.session_id = session_id
        self.store = store or ResearchSessionStore()
        self.live = HarvestClient(HERE.parent / "Red Note Gemini" / "harvest-host.json")

    def call(self, name: str, args: dict):
        if self.store.cancellation_requested(self.session_id):
            raise ResearchCancelled("Research session cancelled")
        if name not in {tool["name"] for tool in BROKER_TOOLS} or not isinstance(args, dict):
            raise ValueError("Tool is outside the Red Engine research broker")
        if name == "study_search":
            result = self.engine.search(self.study["id"], args["query"], args.get("cursor", 0),
                                        min(args.get("limit", 20), 50), args.get("kind", "all"))
        elif name == "study_source":
            result = self.engine.source(self.study["id"], args["kind"], args["id"])
        elif name == "study_thread":
            result = self.engine.thread(self.study["id"], args["post_id"],
                                        args.get("cursor", 0), min(args.get("limit", 20), 50))
        elif name == "study_comment_profile":
            pinned = self.engine.source(self.study["id"], "comment", args["comment_id"])
            if pinned["status"] != "pinned":
                result = {"status": pinned["status"], "comment_id": args["comment_id"],
                          "commenter_profile_cloned": False}
            else:
                author_id = pinned["record"].get("author_id")
                if not isinstance(author_id, str) or not re.fullmatch(r"[a-fA-F0-9]{24}", author_id):
                    result = {"status": "profile_link_unavailable", "comment_id": args["comment_id"],
                              "commenter_profile_cloned": False}
                else:
                    url = "https://www.rednote.com/user/profile/" + author_id.lower()
                    result = {"status": "linked", "comment_id": args["comment_id"],
                              "comment_source_hash": pinned["content_hash"], "author_id": author_id,
                              "profile_url": url, "commenter_profile_cloned": False,
                              "profile_evidence": "live observation only if visit=true; self-described fields are not verified demographics"}
                    if args.get("visit") is True:
                        result["live_observation"] = invoke(self.live, "codex_red_note_live_profile", {"url": url})
        elif name == "study_comment_media":
            pinned = self.engine.source(self.study["id"], "comment", args["comment_id"])
            if pinned["status"] != "pinned":
                result = {"status": pinned["status"], "comment_id": args["comment_id"]}
            else:
                with self.engine.archive.connect() as db:
                    has_rich = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='comment_rich'").fetchone()
                    rich = db.execute("SELECT segments,source_text_hash FROM comment_rich WHERE comment_id=?", (args["comment_id"],)).fetchone() if has_rich else None
                    assets = [dict(row) for row in db.execute("""SELECT a.id,a.slot,a.mime,a.source_available,
                        f.sha256,f.bytes,f.verification FROM assets a LEFT JOIN files f ON f.id=a.file_id
                        WHERE a.owner_id=? AND a.role='comment_attachment' ORDER BY a.slot LIMIT 100""", (args["comment_id"],))]
                body_hash = hashlib.sha256(pinned["record"]["body"].encode("utf-8")).hexdigest()
                ordered = json.loads(rich["segments"])[:100] if rich and rich["source_text_hash"] == body_hash else []
                for asset in assets:
                    member = self.engine.studies.member(self.study["id"], "asset", asset["id"])
                    asset["study_pin"] = (self.engine.source(self.study["id"], "asset", asset["id"])["status"]
                                          if member else "outside_study")
                    asset["media_kind"] = asset["slot"].split(":", 1)[0]
                result = {"status": "pinned_comment", "comment_id": args["comment_id"],
                          "comment_source_hash": pinned["content_hash"], "body": pinned["record"]["body"],
                          "ordered_segments": ordered, "segments_status": "current_archive_matching_text" if ordered else "unavailable",
                          "attachments": assets, "media_evidence": "current archive; inspect study_pin for each asset"}
        else:
            result = invoke(self.live, name, args)
        # Preserve full receipt locally; provider gets a bounded view and can
        # page again for more detail. Never treat live observations as pins.
        sequence = self.store.record(self.session_id, name, args, result)
        encoded = _json(result)
        if len(encoded.encode("utf-8")) > 48_000:
            return {"receipt": sequence, "truncated": True,
                    "preview": encoded[:24_000], "note": "Full result is in the local immutable receipt."}
        return {"receipt": sequence, "result": result,
                "source_lane": "live_observation" if name in LIVE_TOOLS else "captured_study"}
