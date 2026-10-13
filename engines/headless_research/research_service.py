"""Headless Red Engine research presets and bounded archive text search.

Research configuration is stored apart from the immutable RedNote mirror. This
service makes no demographic or identity inference from matching text.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from study_core import StudyEngine
from study_storage import SqliteArchive, SqliteStudies


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
PRESET_DB = HERE / "data" / "research-presets.sqlite"
MIRROR_DB = PROJECT / "Red Note Gemini" / "data_v2" / "rednote-v2.sqlite"
HOST, PORT = "127.0.0.1", 5188
DEFAULT_ID = "rednote-glamour-audience"
STUDY_ENGINE = StudyEngine(SqliteArchive(), SqliteStudies())

DEFAULT_PRESET = {
    "id": DEFAULT_ID,
    "title": "Glamour, curves & women's responses",
    "summary": "Glamorous RedNote creators of Chinese background with pronounced curves, makeup and fashion content, plus their commenters.",
    "question": "Within a selected collection of glamorous RedNote creators of Chinese background featuring pronounced curves, makeup and fashion, how do captured commenters discuss body presentation, admiration, aspiration, shopping and attraction-related language? Which audience attributes can actually be verified?",
    "inclusion_rule": "Selected captured RedNote creator posts and their captured comment threads; preserve original wording, post context and source links.",
    "exclusion_rule": "Exclude uncaptured material and unsupported demographic, purchase or sexual-orientation inferences; report gaps and counterexamples.",
    "mode": "Locked",
    "source_types": ["Profiles", "Posts", "Comments", "Media"],
    "source_ref": "C:/Users/User/Downloads/RED ENGINE.zip :: RED ENGINE/messages/01_user.md",
    "terms": [
        {"term": "臀", "concept": "body presentation", "weight": 8},
        {"term": "屁股", "concept": "body presentation", "weight": 8},
        {"term": "身材", "concept": "body presentation", "weight": 5},
        {"term": "曲线", "concept": "body presentation", "weight": 5},
        {"term": "性感", "concept": "glamour", "weight": 4},
        {"term": "辣妹", "concept": "glamour", "weight": 4},
        {"term": "妆容", "concept": "makeup", "weight": 4},
        {"term": "化妆", "concept": "makeup", "weight": 4},
        {"term": "美妆", "concept": "makeup", "weight": 4},
        {"term": "穿搭", "concept": "fashion", "weight": 3},
        {"term": "老婆", "concept": "audience language", "weight": 3},
        {"term": "姐妹", "concept": "audience language", "weight": 2},
        {"term": "百合", "concept": "audience language", "weight": 3},
        {"term": "wife", "concept": "audience language", "weight": 3},
        {"term": "yuri", "concept": "audience language", "weight": 3},
    ],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def preset_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(PRESET_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize() -> None:
    PRESET_DB.parent.mkdir(parents=True, exist_ok=True)
    with preset_connection() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS research_presets (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                question TEXT NOT NULL,
                inclusion_rule TEXT NOT NULL,
                exclusion_rule TEXT NOT NULL,
                mode TEXT NOT NULL,
                source_types_json TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS preset_terms (
                preset_id TEXT NOT NULL REFERENCES research_presets(id) ON DELETE CASCADE,
                term TEXT NOT NULL,
                concept TEXT NOT NULL,
                weight INTEGER NOT NULL CHECK(weight BETWEEN 1 AND 10),
                PRIMARY KEY (preset_id, term)
            );
        """)
        if not db.execute("SELECT 1 FROM research_presets WHERE id=?", (DEFAULT_ID,)).fetchone():
            insert_preset(db, DEFAULT_PRESET, is_default=True)


def insert_preset(db: sqlite3.Connection, value: dict, *, is_default: bool = False) -> None:
    now = utc_now()
    db.execute("""INSERT INTO research_presets
        (id,title,summary,question,inclusion_rule,exclusion_rule,mode,source_types_json,
         source_ref,is_default,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
        value["id"], value["title"], value["summary"], value["question"],
        value["inclusion_rule"], value["exclusion_rule"], value["mode"],
        json.dumps(value["source_types"], ensure_ascii=False), value["source_ref"],
        int(is_default), now, now,
    ))
    db.executemany("INSERT INTO preset_terms VALUES (?,?,?,?)", [
        (value["id"], t["term"], t["concept"], t["weight"]) for t in value["terms"]
    ])


def read_preset(db: sqlite3.Connection, preset_id: str) -> dict | None:
    row = db.execute("SELECT * FROM research_presets WHERE id=?", (preset_id,)).fetchone()
    if not row:
        return None
    value = dict(row)
    value["source_types"] = json.loads(value.pop("source_types_json"))
    value["is_default"] = bool(value["is_default"])
    value["terms"] = [dict(term) for term in db.execute(
        "SELECT term,concept,weight FROM preset_terms WHERE preset_id=? ORDER BY weight DESC, term", (preset_id,)
    )]
    return value


def save_custom_preset(payload: dict) -> dict:
    allowed_types = {"Profiles", "Posts", "Comments", "Media"}
    text_fields = {"title": 100, "summary": 240, "question": 1000,
                   "inclusion_rule": 600, "exclusion_rule": 600}
    for field, maximum in text_fields.items():
        if not isinstance(payload.get(field), str) or not 1 <= len(payload[field].strip()) <= maximum:
            raise ValueError(f"{field} must contain 1–{maximum} characters")
    mode = payload.get("mode")
    if mode not in {"Locked", "Discovery", "External"}:
        raise ValueError("Unsupported research mode")
    source_types = payload.get("source_types")
    if not isinstance(source_types, list) or not source_types or len(source_types) > 4 or set(source_types) - allowed_types:
        raise ValueError("Choose one or more supported source types")
    raw_terms = payload.get("terms")
    if not isinstance(raw_terms, list) or not 1 <= len(raw_terms) <= 30:
        raise ValueError("Provide 1–30 search seed terms")
    terms, seen = [], set()
    for raw in raw_terms:
        term = raw.get("term") if isinstance(raw, dict) else raw
        if not isinstance(term, str) or not 1 <= len(term.strip()) <= 40:
            raise ValueError("Each term must contain 1–40 characters")
        term = term.strip()
        if term.casefold() in seen:
            continue
        seen.add(term.casefold())
        weight = raw.get("weight", 3) if isinstance(raw, dict) else 3
        concept = raw.get("concept", "custom") if isinstance(raw, dict) else "custom"
        if not isinstance(weight, int) or isinstance(weight, bool) or not 1 <= weight <= 10:
            raise ValueError("Term weights must be integers from 1 to 10")
        if not isinstance(concept, str) or len(concept) > 60:
            raise ValueError("Invalid term concept")
        terms.append({"term": term, "concept": concept, "weight": weight})
    value = {field: payload[field].strip() for field in text_fields}
    value.update(id="custom-" + uuid.uuid4().hex[:12], mode=mode,
                 source_types=list(dict.fromkeys(source_types)), terms=terms,
                 source_ref="User-authored local research preset")
    with preset_connection() as db:
        insert_preset(db, value)
        return read_preset(db, value["id"])


def mirror_connection() -> sqlite3.Connection:
    if not MIRROR_DB.is_file():
        raise FileNotFoundError("Captured mirror database is unavailable")
    db = sqlite3.connect(MIRROR_DB.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def excerpt(content: str, needles: list[str], width: int = 210) -> str:
    content = re.sub(r"\s+", " ", content or "").strip()
    positions = [content.casefold().find(term.casefold()) for term in needles if term]
    positions = [position for position in positions if position >= 0]
    start = max(0, min(positions) - 55) if positions else 0
    return ("…" if start else "") + content[start:start + width] + ("…" if start + width < len(content) else "")


def search_archive(preset: dict, query: str, limit: int) -> dict:
    if len(query) > 120:
        raise ValueError("Search text is limited to 120 characters")
    query = query.strip()
    terms = preset["terms"]
    if not terms:
        raise ValueError("Preset has no search terms")
    scored = []
    with mirror_connection() as db:
        totals = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in ("profiles", "posts", "comments")}
        sources = []
        if "Posts" in preset["source_types"]:
            sources.append("""SELECT 'post' AS record_type,p.id,p.id AS post_id,p.title,p.body,
                              p.source_url,p.ordinal,NULL AS author FROM posts p""")
        if "Comments" in preset["source_types"]:
            sources.append("""SELECT 'comment',c.id,c.post_id,p.title,c.body,p.source_url,c.ordinal,c.author
                              FROM comments c JOIN posts p ON p.id=c.post_id""")
        if not sources:
            return {"preset_id": preset["id"], "query": query, "total_matches": 0,
                    "items": [], "archive_counts": totals,
                    "scope_note": "This preset selects no searchable post or comment text. Profile and media fields are not indexed in this preview."}
        rows = db.execute(" UNION ALL ".join(sources))
        for row in rows:
            searchable = ((row["title"] or "") + " " if row["record_type"] == "post" else "") + (row["body"] or "")
            content = searchable.casefold()
            if query and query.casefold() not in content:
                continue
            matches = [term for term in terms if term["term"].casefold() in content]
            if not matches and not query:
                continue
            score = sum(term["weight"] for term in matches)
            if query:
                score += 20
            # Distinct concepts outrank repeated single-word mentions.
            score += max(0, len({term["concept"] for term in matches}) - 1) * 3
            scored.append((score, row["record_type"] == "post", -row["ordinal"], dict(row), matches))
    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    items, comment_counts = [], defaultdict(int)
    for score, _, _, row, matches in scored:
        if row["record_type"] == "comment":
            if comment_counts[row["post_id"]] >= 2:
                continue
            comment_counts[row["post_id"]] += 1
        needles = ([query] if query else []) + [term["term"] for term in matches]
        items.append({"id": row["id"], "post_id": row["post_id"],
                      "record_type": row["record_type"], "post_title": row["title"],
                      "source_url": row["source_url"], "excerpt": excerpt(row["body"], needles),
                      "matched_terms": [term["term"] for term in matches], "score": score})
        if len(items) == limit:
            break
    return {"preset_id": preset["id"], "query": query, "total_matches": len(scored),
            "items": items, "archive_counts": totals,
            "scope_note": "Text matches within the captured local archive only. Ranking favors preset terms; matches do not establish identity, preference, purchase or broader audience composition."}


class Handler(BaseHTTPRequestHandler):
    def json_response(self, value: dict, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        route = urlsplit(self.path)
        try:
            if route.path == "/api/health":
                return self.json_response({"service": "red-engine-research", "ui": False,
                                           "preset_database": PRESET_DB.is_file(),
                                           "study_database": STUDY_ENGINE.studies.path.is_file(),
                                           "mirror_available": MIRROR_DB.is_file()})
            if route.path == "/api/coverage":
                return self.json_response(STUDY_ENGINE.coverage())
            if route.path == "/api/studies":
                return self.json_response({"items": STUDY_ENGINE.list()})
            parts = route.path.strip("/").split("/")
            if len(parts) >= 3 and parts[:2] == ["api", "studies"]:
                study_id = parts[2]
                if len(parts) == 3:
                    return self.json_response(STUDY_ENGINE.get(study_id))
                args = parse_qs(route.query)
                cursor = int(args.get("cursor", ["0"])[0])
                limit = int(args.get("limit", ["30"])[0])
                if len(parts) == 4 and parts[3] == "search":
                    return self.json_response(STUDY_ENGINE.search(
                        study_id, args.get("q", [""])[0], cursor, limit,
                        args.get("kind", ["all"])[0]))
                if len(parts) == 4 and parts[3] == "records":
                    return self.json_response(STUDY_ENGINE.records(
                        study_id, args.get("kind", ["post"])[0], cursor, limit))
                if len(parts) == 6 and parts[3] == "sources":
                    return self.json_response(STUDY_ENGINE.source(study_id, parts[4], parts[5]))
                if len(parts) == 6 and parts[3] == "derived-reading":
                    return self.json_response(STUDY_ENGINE.derived_reading(study_id, parts[4], parts[5]))
                if len(parts) == 5 and parts[3] == "threads":
                    return self.json_response(STUDY_ENGINE.thread(study_id, parts[4], cursor, limit))
                if len(parts) == 4 and parts[3] == "measurements":
                    return self.json_response({"items": STUDY_ENGINE.measurements(study_id)})
                if len(parts) == 5 and parts[3] == "measurements":
                    return self.json_response(STUDY_ENGINE.measurement(study_id, parts[4]))
                if len(parts) == 5 and parts[3] == "media-context":
                    return self.json_response(STUDY_ENGINE.media_context(study_id, parts[4]))
                if len(parts) == 4 and parts[3] == "findings":
                    return self.json_response({"items": STUDY_ENGINE.findings(study_id)})
                if len(parts) == 5 and parts[3] == "findings":
                    return self.json_response(STUDY_ENGINE.finding(study_id, parts[4]))
                if len(parts) == 4 and parts[3] == "contexts":
                    return self.json_response({"items": STUDY_ENGINE.contexts(study_id)})
                if len(parts) == 5 and parts[3] == "contexts":
                    return self.json_response(STUDY_ENGINE.context(study_id, parts[4]))
                if len(parts) == 4 and parts[3] == "jobs":
                    return self.json_response({"items": STUDY_ENGINE.jobs(study_id)})
                if len(parts) == 5 and parts[3] == "jobs":
                    return self.json_response(STUDY_ENGINE.job(study_id, parts[4]))
                if len(parts) == 6 and parts[3] == "jobs" and parts[5] == "output":
                    return self.json_response(STUDY_ENGINE.job_output(study_id, parts[4]))
            with preset_connection() as db:
                if route.path == "/api/presets":
                    items = [read_preset(db, row["id"]) for row in db.execute(
                        "SELECT id FROM research_presets ORDER BY is_default DESC, updated_at DESC")]
                    return self.json_response({"items": items})
                if route.path == "/api/search":
                    args = parse_qs(route.query)
                    preset = read_preset(db, args.get("preset", [DEFAULT_ID])[0])
                    if not preset:
                        return self.json_response({"error": "Preset not found"}, 404)
                    limit = max(1, min(30, int(args.get("limit", ["12"])[0])))
                    return self.json_response(search_archive(preset, args.get("q", [""])[0], limit))
            self.json_response({"error": "Unknown route"}, 404)
        except LookupError as exc:
            self.json_response({"error": str(exc)}, 404)
        except (ValueError, TypeError) as exc:
            self.json_response({"error": str(exc)}, 400)
        except (OSError, sqlite3.Error) as exc:
            self.json_response({"error": str(exc)}, 503)

    def do_POST(self) -> None:
        parts = urlsplit(self.path).path.strip("/").split("/")
        measurement_action = len(parts) == 4 and parts[:2] == ["api", "studies"] and parts[3] == "measurements"
        marketing_action = len(parts) == 4 and parts[:2] == ["api", "studies"] and parts[3] == "marketing-measurements"
        finding_action = len(parts) == 4 and parts[:2] == ["api", "studies"] and parts[3] == "findings"
        review_action = len(parts) == 6 and parts[:2] == ["api", "studies"] and parts[3] == "findings" and parts[5] == "review"
        context_action = len(parts) == 4 and parts[:2] == ["api", "studies"] and parts[3] == "contexts"
        job_action = len(parts) == 4 and parts[:2] == ["api", "studies"] and parts[3] == "jobs"
        cancel_action = len(parts) == 6 and parts[:2] == ["api", "studies"] and parts[3] == "jobs" and parts[5] == "cancel"
        retry_action = len(parts) == 6 and parts[:2] == ["api", "studies"] and parts[3] == "jobs" and parts[5] == "retry"
        if (self.path not in {"/api/presets", "/api/studies"} and not measurement_action and not marketing_action and not finding_action and not review_action and not context_action and not job_action and not cancel_action and not retry_action) or self.headers.get("X-Red-Engine-Action") != "explicit":
            return self.json_response({"error": "Action unavailable"}, 403)
        try:
            if cancel_action:
                if int(self.headers.get("Content-Length", "0")) != 0:
                    raise ValueError("Cancel action takes no payload")
                return self.json_response(STUDY_ENGINE.cancel_job(parts[2], parts[4]))
            if retry_action:
                if int(self.headers.get("Content-Length", "0")) != 0:
                    raise ValueError("Retry action takes no payload")
                return self.json_response(STUDY_ENGINE.retry_job(parts[2], parts[4]))
            if measurement_action:
                if int(self.headers.get("Content-Length", "0")) != 0:
                    raise ValueError("Measurement action takes no payload")
                return self.json_response(STUDY_ENGINE.measure(parts[2]), 201)
            if marketing_action:
                if int(self.headers.get("Content-Length", "0")) != 0:
                    raise ValueError("Marketing measurement action takes no payload")
                study = STUDY_ENGINE.get(parts[2])
                with preset_connection() as db:
                    preset = read_preset(db, study.get("preset_id") or DEFAULT_ID)
                if not preset:
                    raise LookupError("Research preset not found")
                return self.json_response(STUDY_ENGINE.measure_marketing(parts[2], preset["terms"], preset["id"]), 201)
            length = int(self.headers.get("Content-Length", "0"))
            maximum = 131072 if self.path == "/api/studies" else 32768 if context_action or finding_action else 16384
            if not 0 < length <= maximum:
                raise ValueError("Request payload exceeds limit")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Payload must be an object")
            if context_action:
                return self.json_response(STUDY_ENGINE.create_context(parts[2], payload), 201)
            if finding_action:
                return self.json_response(STUDY_ENGINE.create_finding(parts[2], payload), 201)
            if review_action:
                return self.json_response(STUDY_ENGINE.review_finding(parts[2], parts[4], payload))
            if job_action:
                return self.json_response(STUDY_ENGINE.create_job(parts[2], payload), 201)
            if self.path == "/api/studies":
                return self.json_response(STUDY_ENGINE.create(payload), 201)
            self.json_response(save_custom_preset(payload), 201)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.json_response({"error": str(exc)}, 400)
        except LookupError as exc:
            self.json_response({"error": str(exc)}, 404)
        except (OSError, sqlite3.Error) as exc:
            self.json_response({"error": str(exc)}, 503)


if __name__ == "__main__":
    initialize()
    def recover_expired_leases():
        while True:
            try:
                STUDY_ENGINE.recover_jobs()
            except (OSError, sqlite3.Error):
                pass
            time.sleep(20)
    threading.Thread(target=recover_expired_leases, name="red-job-recovery", daemon=True).start()
    print(f"Red Engine research service: http://{HOST}:{PORT}/api/health", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
