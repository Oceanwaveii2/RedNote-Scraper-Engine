"""SQLite adapters for the captured mirror and separate Red Engine study data."""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


MIRROR_DB = Path(os.environ.get("REDNOTE_MIRROR_DB") or
                 Path(__file__).resolve().parent.parent / "Red Note Gemini" / "data_v2" / "rednote-v2.sqlite")
STUDY_DB = Path(os.environ.get("REDNOTE_STUDY_DB") or
                Path(__file__).resolve().parent / "data" / "studies.sqlite")
INDEX_DB = Path(os.environ.get("REDNOTE_INDEX_DB") or
                Path(__file__).resolve().parent / "data" / "source-index.sqlite")


def decode_json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {"raw": result}
    except (ValueError, TypeError):
        return {"unparsed": value}


def placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


class SqliteArchive:
    """Reads original-language archive records without writing to the mirror."""

    def __init__(self, path: Path = MIRROR_DB):
        self.path = path

    def generation(self) -> str:
        """Cheap SQLite revision signal; a changed signal requires pin verification."""
        stat = self.path.stat()
        with self.path.open("rb") as stream:
            header = stream.read(100)
        wal = Path(str(self.path) + "-wal")
        wal_stat = wal.stat() if wal.exists() else None
        wal_signal = f"{wal_stat.st_size}:{wal_stat.st_mtime_ns}" if wal_stat else "none"
        return f"{stat.st_size}:{stat.st_mtime_ns}:{header[24:28].hex()}:{wal_signal}"

    @contextmanager
    def connect(self):
        if not self.path.is_file():
            raise FileNotFoundError("Captured mirror database is unavailable")
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            yield db
        finally:
            db.close()

    def coverage(self) -> dict:
        with self.connect() as db:
            counts = {kind: db.execute(f"SELECT count(*) FROM {kind}").fetchone()[0]
                      for kind in ("profiles", "posts", "comments", "assets")}
            statuses = {}
            for row in db.execute("SELECT coverage FROM posts"):
                coverage = decode_json(row["coverage"])
                status = coverage.get("status", "unknown")
                statuses[status] = statuses.get(status, 0) + 1
            profiles = [{"id": row["id"], "name": row["name"], "source_url": row["source_url"],
                         "capture_status": decode_json(row["metadata"]).get("coverage", {}).get("status", "unknown")}
                        for row in db.execute("SELECT id,name,source_url,metadata FROM profiles ORDER BY name")]
            return {"archive": str(self.path), "counts": counts,
                    "post_coverage_statuses": statuses, "profiles": profiles,
                    "note": "Current captured archive inventory; completeness and audience representativeness are not implied."}

    def _revisions(self, db, post_ids: list[str]) -> dict[str, str]:
        if not post_ids:
            return {}
        result = {}
        for start in range(0, len(post_ids), 500):
            batch = post_ids[start:start + 500]
            query = "SELECT entity_id,revision,observed_at FROM observations WHERE entity_id IN (" + placeholders(batch) + ") ORDER BY observed_at,revision"
            for row in db.execute(query, batch):
                result[row["entity_id"]] = row["revision"]
        return result

    def _profile(self, row) -> dict:
        return {"kind": "profile", "id": row["id"], "name": row["name"],
                "bio": row["bio"], "source_url": row["source_url"],
                "metadata": decode_json(row["metadata"])}

    def _post(self, row, revision: str | None) -> dict:
        return {"kind": "post", "id": row["id"], "profile_id": row["profile_id"],
                "source_id": row["source_id"], "ordinal": row["ordinal"],
                "title": row["title"], "body": row["body"], "media_kind": row["kind"],
                "source_url": row["source_url"], "captured_at": row["captured_at"],
                "coverage": decode_json(row["coverage"]), "inventory": decode_json(row["inventory"]),
                "source_revision": revision}

    def _comment(self, row, revision: str | None) -> dict:
        return {"kind": "comment", "id": row["id"], "post_id": row["post_id"],
                "source_id": row["source_id"], "parent_source_id": row["parent_source_id"],
                "ordinal": row["ordinal"], "author_id": row["author_id"],
                "author": row["author"], "body": row["body"], "likes": row["likes"],
                "created_at": row["created_at"], "avatar_id": row["avatar_id"],
                "source_url": row["source_url"], "source_revision": revision}

    def _asset(self, row, post_id: str | None, revision: str | None) -> dict:
        return {"kind": "asset", "id": row["id"], "owner_id": row["owner_id"],
                "post_id": post_id, "role": row["role"], "slot": row["slot"],
                "file_id": row["file_id"], "mime": row["mime"],
                "source_available": bool(row["source_available"]),
                "file_path": row["path"], "file_sha256": row["sha256"],
                "file_bytes": row["bytes"], "file_verification": row["verification"],
                "source_revision": revision}

    def capture(self, profile_ids: list[str], post_ids: list[str] | None) -> tuple[list[dict], dict]:
        with self.connect() as db:
            db.execute("BEGIN")
            profiles = db.execute("SELECT * FROM profiles WHERE id IN (" + placeholders(profile_ids) + ")", profile_ids).fetchall()
            if len(profiles) != len(profile_ids):
                raise ValueError("One or more selected profiles are not captured")
            if post_ids is None:
                posts = db.execute("SELECT * FROM posts WHERE profile_id IN (" + placeholders(profile_ids) + ") ORDER BY profile_id,ordinal,id", profile_ids).fetchall()
            else:
                posts = db.execute("SELECT * FROM posts WHERE id IN (" + placeholders(post_ids) + ") ORDER BY profile_id,ordinal,id", post_ids).fetchall()
                if len(posts) != len(post_ids) or any(row["profile_id"] not in profile_ids for row in posts):
                    raise ValueError("Selected posts must exist beneath the selected profiles")
            selected_posts = [row["id"] for row in posts]
            revisions = self._revisions(db, selected_posts)
            records = [self._profile(row) for row in profiles]
            records += [self._post(row, revisions.get(row["id"])) for row in posts]
            post_urls = {row["id"]: row["source_url"] for row in posts}
            comments = []
            for start in range(0, len(selected_posts), 500):
                batch = selected_posts[start:start + 500]
                query = "SELECT c.*,p.source_url FROM comments c JOIN posts p ON p.id=c.post_id WHERE c.post_id IN (" + placeholders(batch) + ") ORDER BY c.post_id,c.ordinal,c.id"
                comments.extend(db.execute(query, batch).fetchall())
                if len(comments) > 100_000:
                    raise ValueError("Study exceeds the current 100,000-comment capture limit; select fewer posts")
            records += [self._comment(row, revisions.get(row["post_id"])) for row in comments]
            comment_posts = {row["id"]: row["post_id"] for row in comments}
            author_entities = set()
            comment_ids = list(comment_posts)
            for start in range(0, len(comment_ids), 500):
                batch = comment_ids[start:start + 500]
                query = "SELECT to_id FROM relationships WHERE kind='authored_by' AND from_id IN (" + placeholders(batch) + ")"
                author_entities.update(row["to_id"] for row in db.execute(query, batch))
            owner_ids = profile_ids + selected_posts + comment_ids + sorted(author_entities)
            assets = []
            for start in range(0, len(owner_ids), 500):
                batch = owner_ids[start:start + 500]
                query = "SELECT a.*,f.path,f.sha256,f.bytes,f.verification FROM assets a LEFT JOIN files f ON f.id=a.file_id WHERE a.owner_id IN (" + placeholders(batch) + ") ORDER BY a.owner_id,a.role,a.slot,a.id"
                for row in db.execute(query, batch):
                    post_id = row["owner_id"] if row["owner_id"] in post_urls else comment_posts.get(row["owner_id"])
                    assets.append(self._asset(row, post_id, revisions.get(post_id)))
            records += assets
            statuses = {}
            for row in posts:
                status = decode_json(row["coverage"]).get("status", "unknown")
                statuses[status] = statuses.get(status, 0) + 1
            captured_source_ids = {(row["post_id"], row["source_id"]) for row in comments}
            missing_parents = sum(1 for row in comments if row["parent_source_id"] and
                                  (row["post_id"], row["parent_source_id"]) not in captured_source_ids)
            scope = {"archive_path": str(self.path), "post_coverage_statuses": statuses,
                     "unresolved_comment_parents": missing_parents,
                     "selected_profile_count": len(profiles), "selected_post_count": len(posts),
                     "captured_comment_count": len(comments), "captured_asset_count": len(assets),
                     "comment_author_asset_owners": len(author_entities),
                     "warning": "Pins are content hashes, not a media copy. Changed or missing source records are reported explicitly."}
            return records, scope

    def capture_into(self, profile_ids: list[str], post_ids: list[str] | None, emit) -> dict:
        """Read one SQLite snapshot and emit records without retaining the study in Python."""
        with self.connect() as db:
            db.execute("BEGIN")
            profiles = db.execute("SELECT * FROM profiles WHERE id IN (" + placeholders(profile_ids) + ")",
                                  profile_ids).fetchall()
            if len(profiles) != len(profile_ids):
                raise ValueError("One or more selected profiles are not captured")
            if post_ids is None:
                posts = db.execute("SELECT * FROM posts WHERE profile_id IN (" + placeholders(profile_ids) +
                                   ") ORDER BY profile_id,ordinal,id", profile_ids).fetchall()
            else:
                posts = db.execute("SELECT * FROM posts WHERE id IN (" + placeholders(post_ids) +
                                   ") ORDER BY profile_id,ordinal,id", post_ids).fetchall()
                if len(posts) != len(post_ids) or any(row["profile_id"] not in profile_ids for row in posts):
                    raise ValueError("Selected posts must exist beneath the selected profiles")
            revisions = self._revisions(db, [row["id"] for row in posts])
            for row in profiles:
                emit(self._profile(row))
            for row in posts:
                emit(self._post(row, revisions.get(row["id"])))
            selected_posts = [row["id"] for row in posts]
            comment_posts = {}
            captured_source_ids = set()
            parent_refs = []
            comment_count = 0
            for start in range(0, len(selected_posts), 500):
                batch = selected_posts[start:start + 500]
                query = "SELECT c.*,p.source_url FROM comments c JOIN posts p ON p.id=c.post_id WHERE c.post_id IN (" + placeholders(batch) + ") ORDER BY c.post_id,c.ordinal,c.id"
                for row in db.execute(query, batch):
                    emit(self._comment(row, revisions.get(row["post_id"])))
                    comment_posts[row["id"]] = row["post_id"]
                    captured_source_ids.add((row["post_id"], row["source_id"]))
                    if row["parent_source_id"]:
                        parent_refs.append((row["post_id"], row["parent_source_id"]))
                    comment_count += 1
            author_entities = set()
            comment_ids = list(comment_posts)
            for start in range(0, len(comment_ids), 500):
                batch = comment_ids[start:start + 500]
                query = "SELECT to_id FROM relationships WHERE kind='authored_by' AND from_id IN (" + placeholders(batch) + ")"
                author_entities.update(row["to_id"] for row in db.execute(query, batch))
            owner_ids = profile_ids + selected_posts + comment_ids + sorted(author_entities)
            post_urls = {row["id"]: row["source_url"] for row in posts}
            asset_count = 0
            for start in range(0, len(owner_ids), 500):
                batch = owner_ids[start:start + 500]
                query = "SELECT a.*,f.path,f.sha256,f.bytes,f.verification FROM assets a LEFT JOIN files f ON f.id=a.file_id WHERE a.owner_id IN (" + placeholders(batch) + ") ORDER BY a.owner_id,a.role,a.slot,a.id"
                for row in db.execute(query, batch):
                    post_id = row["owner_id"] if row["owner_id"] in post_urls else comment_posts.get(row["owner_id"])
                    emit(self._asset(row, post_id, revisions.get(post_id)))
                    asset_count += 1
            statuses = {}
            for post in posts:
                status = decode_json(post["coverage"]).get("status", "unknown")
                statuses[status] = statuses.get(status, 0) + 1
            return {"archive_path": str(self.path), "post_coverage_statuses": statuses,
                    "unresolved_comment_parents": sum(ref not in captured_source_ids for ref in parent_refs),
                    "selected_profile_count": len(profiles), "selected_post_count": len(posts),
                    "captured_comment_count": comment_count, "captured_asset_count": asset_count,
                    "comment_author_asset_owners": len(author_entities),
                    "warning": "Pins are content hashes, not a media copy. Changed or missing source records are reported explicitly."}

    def read(self, kind: str, entity_id: str) -> dict | None:
        with self.connect() as db:
            if kind == "profile":
                row = db.execute("SELECT * FROM profiles WHERE id=?", (entity_id,)).fetchone()
                return self._profile(row) if row else None
            if kind == "post":
                row = db.execute("SELECT * FROM posts WHERE id=?", (entity_id,)).fetchone()
                revision = self._revisions(db, [entity_id]).get(entity_id)
                return self._post(row, revision) if row else None
            if kind == "comment":
                row = db.execute("SELECT c.*,p.source_url FROM comments c JOIN posts p ON p.id=c.post_id WHERE c.id=?", (entity_id,)).fetchone()
                revision = self._revisions(db, [row["post_id"]]).get(row["post_id"]) if row else None
                return self._comment(row, revision) if row else None
            if kind == "asset":
                row = db.execute("SELECT a.*,f.path,f.sha256,f.bytes,f.verification FROM assets a LEFT JOIN files f ON f.id=a.file_id WHERE a.id=?", (entity_id,)).fetchone()
                if not row:
                    return None
                owner = db.execute("SELECT id FROM posts WHERE id=?", (row["owner_id"],)).fetchone()
                post_id = owner["id"] if owner else None
                if not post_id:
                    comment = db.execute("SELECT post_id FROM comments WHERE id=?", (row["owner_id"],)).fetchone()
                    post_id = comment["post_id"] if comment else None
                revision = self._revisions(db, [post_id]).get(post_id) if post_id else None
                return self._asset(row, post_id, revision)
            raise ValueError("Unsupported record kind")

    def derived_reading(self, entity_id: str) -> tuple[list[dict], str | None, str | None]:
        """Read optional derivatives and the verified media hashes they depend on."""
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT id,kind,language,source_hash,engine,engine_revision,content,created_at "
                "FROM derivatives WHERE entity_id=? AND kind IN ('translation','ocr','transcript') "
                "ORDER BY created_at DESC,id DESC LIMIT 30", (entity_id,))]
            assets = db.execute(
                "SELECT a.role,f.path,f.sha256,f.verification FROM assets a "
                "JOIN files f ON f.id=a.file_id WHERE a.owner_id=? "
                "AND a.role IN ('image','document','video') "
                "ORDER BY a.role,CAST(a.slot AS INTEGER),a.slot", (entity_id,)).fetchall()
        hashes = []
        video_hash = None
        for asset in assets:
            relative = asset["path"]
            if not relative or asset["verification"] != "verified":
                continue
            path = (self.path.parent.parent / relative).resolve()
            try:
                path.relative_to(self.path.parent.parent.resolve())
            except ValueError:
                continue
            if not path.is_file():
                continue
            hashes.append(asset["sha256"])
            if asset["role"] == "video" and video_hash is None:
                video_hash = asset["sha256"]
        media_hash = hashlib.sha256(json.dumps(hashes).encode()).hexdigest() if hashes else None
        return rows, media_hash, video_hash

    def ocr_asset_ids(self, post_id: str) -> list[str]:
        """Return asset IDs in the same local-media order used by the OCR worker."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT a.id,f.path,f.verification FROM assets a JOIN files f ON f.id=a.file_id "
                "WHERE a.owner_id=? AND a.role IN ('image','document','video') "
                "ORDER BY a.role,CAST(a.slot AS INTEGER),a.slot", (post_id,)).fetchall()
        result = []
        for row in rows:
            if row["verification"] not in {"verified", "unverified"}:
                continue
            path = (self.path.parent.parent / row["path"]).resolve()
            try:
                path.relative_to(self.path.parent.parent.resolve())
            except ValueError:
                continue
            if path.is_file():
                result.append(row["id"])
        return result


class SqliteStudies:
    """Stores study pins and a rebuildable, revision-gated record projection."""

    def __init__(self, path: Path = STUDY_DB):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            db.commit()
        finally:
            db.close()

    def validated_output_count(self, provider: str) -> int:
        """Historical proposals that passed source/quote validation on save."""
        with self.connect() as db:
            return int(db.execute("""SELECT count(*) FROM analysis_outputs o
                JOIN analysis_jobs j ON j.id=o.job_id WHERE j.provider=?""",
                (provider,)).fetchone()[0])

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS studies (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL,
                    preset_id TEXT, profile_ids_json TEXT NOT NULL, post_ids_json TEXT,
                    created_at TEXT NOT NULL, counts_json TEXT NOT NULL,
                    coverage_json TEXT NOT NULL, manifest_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS study_members (
                    study_id TEXT NOT NULL REFERENCES studies(id),
                    kind TEXT NOT NULL CHECK(kind IN ('profile','post','comment','asset')),
                    entity_id TEXT NOT NULL, post_id TEXT, ordinal INTEGER, content_hash TEXT NOT NULL,
                    source_revision TEXT, PRIMARY KEY(study_id,kind,entity_id)
                );
                CREATE INDEX IF NOT EXISTS study_members_thread ON study_members(study_id,kind,post_id,entity_id);
                CREATE TABLE IF NOT EXISTS study_projection (
                    study_id TEXT NOT NULL REFERENCES studies(id), kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL, record_json TEXT NOT NULL,
                    PRIMARY KEY(study_id,kind,entity_id));
                CREATE TABLE IF NOT EXISTS study_source_state (
                    study_id TEXT PRIMARY KEY REFERENCES studies(id), archive_generation TEXT NOT NULL,
                    verified_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS study_index_validation (
                    index_generation TEXT NOT NULL, archive_generation TEXT NOT NULL,
                    mirror_sha256 TEXT NOT NULL,
                    PRIMARY KEY(index_generation,archive_generation));
                CREATE TABLE IF NOT EXISTS measurement_runs (
                    id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id),
                    created_at TEXT NOT NULL, algorithm TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL, result_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS measurement_runs_study ON measurement_runs(study_id,created_at);
                CREATE TABLE IF NOT EXISTS measurement_cache (
                    study_id TEXT NOT NULL REFERENCES studies(id), algorithm TEXT NOT NULL,
                    input_hash TEXT NOT NULL, manifest_hash TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES measurement_runs(id),
                    PRIMARY KEY(study_id,algorithm,input_hash,manifest_hash));
                CREATE TABLE IF NOT EXISTS evidence_contexts (
                    id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id),
                    created_at TEXT NOT NULL, manifest_hash TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS evidence_contexts_study ON evidence_contexts(study_id,created_at);
                CREATE TABLE IF NOT EXISTS analysis_jobs (
                    id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id),
                    context_id TEXT NOT NULL REFERENCES evidence_contexts(id),
                    provider TEXT NOT NULL CHECK(provider IN ('codex','gemini')),
                    requested_model TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_job_events (
                    job_id TEXT NOT NULL REFERENCES analysis_jobs(id),
                    sequence INTEGER NOT NULL, state TEXT NOT NULL,
                    created_at TEXT NOT NULL, detail_json TEXT NOT NULL,
                    PRIMARY KEY(job_id,sequence)
                );
                CREATE INDEX IF NOT EXISTS analysis_jobs_study ON analysis_jobs(study_id,created_at);
                CREATE TABLE IF NOT EXISTS analysis_outputs (
                    job_id TEXT PRIMARY KEY REFERENCES analysis_jobs(id),
                    created_at TEXT NOT NULL, model TEXT NOT NULL,
                    thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    output_hash TEXT NOT NULL, output_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_job_leases (
                    job_id TEXT PRIMARY KEY REFERENCES analysis_jobs(id),
                    owner_pid INTEGER NOT NULL, started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS red_findings (
                    id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id),
                    created_at TEXT NOT NULL, claim TEXT NOT NULL,
                    scope_note TEXT NOT NULL, limitations TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL, source_job_id TEXT REFERENCES analysis_jobs(id),
                    finding_hash TEXT NOT NULL, counter_search_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS red_finding_evidence (
                    finding_id TEXT NOT NULL REFERENCES red_findings(id),
                    ordinal INTEGER NOT NULL, role TEXT NOT NULL CHECK(role IN ('support','counter')),
                    source_kind TEXT NOT NULL CHECK(source_kind IN ('post','comment','asset')),
                    source_id TEXT NOT NULL, source_hash TEXT NOT NULL,
                    exact_quote TEXT, note TEXT NOT NULL,
                    PRIMARY KEY(finding_id,ordinal)
                );
                CREATE TABLE IF NOT EXISTS red_finding_events (
                    finding_id TEXT NOT NULL REFERENCES red_findings(id),
                    sequence INTEGER NOT NULL, state TEXT NOT NULL,
                    created_at TEXT NOT NULL, reviewer TEXT NOT NULL,
                    rationale TEXT NOT NULL, PRIMARY KEY(finding_id,sequence)
                );
                CREATE INDEX IF NOT EXISTS red_findings_study ON red_findings(study_id,created_at);
                CREATE INDEX IF NOT EXISTS red_finding_evidence_source ON red_finding_evidence(source_kind,source_id);
                CREATE TRIGGER IF NOT EXISTS studies_no_update BEFORE UPDATE ON studies BEGIN SELECT RAISE(ABORT,'Study snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS members_no_update BEFORE UPDATE ON study_members BEGIN SELECT RAISE(ABORT,'Source pins are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS measurement_runs_no_update BEFORE UPDATE ON measurement_runs BEGIN SELECT RAISE(ABORT,'Measurement runs are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS evidence_contexts_no_update BEFORE UPDATE ON evidence_contexts BEGIN SELECT RAISE(ABORT,'Evidence contexts are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS analysis_jobs_no_update BEFORE UPDATE ON analysis_jobs BEGIN SELECT RAISE(ABORT,'Analysis requests are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS analysis_job_events_no_update BEFORE UPDATE ON analysis_job_events BEGIN SELECT RAISE(ABORT,'Analysis job events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS analysis_outputs_no_update BEFORE UPDATE ON analysis_outputs BEGIN SELECT RAISE(ABORT,'Analysis outputs are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS red_findings_no_update BEFORE UPDATE ON red_findings BEGIN SELECT RAISE(ABORT,'Findings are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS red_finding_evidence_no_update BEFORE UPDATE ON red_finding_evidence BEGIN SELECT RAISE(ABORT,'Finding evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS red_finding_events_no_update BEFORE UPDATE ON red_finding_events BEGIN SELECT RAISE(ABORT,'Finding review events are immutable'); END;
            """)
            evidence_columns = {row["name"] for row in db.execute("PRAGMA table_info(red_finding_evidence)")}
            for name, sql_type in (("derived_id", "TEXT"), ("derived_hash", "TEXT"), ("time_ms", "INTEGER")):
                if name not in evidence_columns:
                    db.execute(f"ALTER TABLE red_finding_evidence ADD COLUMN {name} {sql_type}")

    @staticmethod
    def _study(row) -> dict:
        value = dict(row)
        for key in ("profile_ids", "post_ids", "counts", "coverage"):
            value[key] = json.loads(value.pop(key + "_json")) if value.get(key + "_json") else None
        return value

    def create(self, study: dict, members: list[dict], records: list[dict] | None = None,
               archive_generation: str | None = None):
        with self.connect() as db:
            db.execute("INSERT INTO studies VALUES (?,?,?,?,?,?,?,?,?,?)", (
                study["id"], study["title"], study["question"], study["preset_id"],
                json.dumps(study["profile_ids"]), json.dumps(study["post_ids"]),
                study["created_at"], json.dumps(study["counts"]),
                json.dumps(study["coverage"]), study["manifest_hash"] ))
            db.executemany("INSERT INTO study_members VALUES (?,?,?,?,?,?,?)", [
                (study["id"], m["kind"], m["id"], m["post_id"], m["ordinal"], m["content_hash"], m["source_revision"])
                for m in members])
            if records is not None and archive_generation is not None:
                self._write_projection(db, study["id"], records, archive_generation)

    def create_stream(self, archive, profile_ids, post_ids, generation, hash_record, finalize):
        """Stage pins and projection in SQLite, then publish one immutable study."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TEMP TABLE pending_members (kind TEXT,id TEXT,post_id TEXT,"
                       "ordinal INTEGER,content_hash TEXT,source_revision TEXT)")
            db.execute("CREATE TEMP TABLE pending_projection (kind TEXT,id TEXT,record_json TEXT)")
            counts = {kind: 0 for kind in ("profile", "post", "comment", "asset")}
            manifest = hashlib.sha256()
            manifest.update(b'{"members":[')
            first = True
            member_batch = []
            projection_batch = []

            def flush():
                if not member_batch:
                    return
                db.executemany("INSERT INTO pending_members VALUES (?,?,?,?,?,?)", member_batch)
                db.executemany("INSERT INTO pending_projection VALUES (?,?,?)", projection_batch)
                member_batch.clear()
                projection_batch.clear()

            def emit(record):
                nonlocal first
                kind = record["kind"]
                counts[kind] += 1
                member = {"kind": kind, "id": record["id"],
                          "post_id": record.get("post_id"), "ordinal": record.get("ordinal"),
                          "content_hash": hash_record(record),
                          "source_revision": record.get("source_revision")}
                if not first:
                    manifest.update(b",")
                first = False
                manifest.update(json.dumps(member, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode("utf-8"))
                member_batch.append((kind, record["id"], member["post_id"], member["ordinal"],
                                     member["content_hash"], member["source_revision"]))
                projection_batch.append((kind, record["id"],
                                         json.dumps(record, ensure_ascii=False, sort_keys=True)))
                if len(member_batch) >= 500:
                    flush()

            scope = archive.capture_into(profile_ids, post_ids, emit)
            flush()
            if archive.generation() != generation:
                raise RuntimeError("Captured archive changed while pinning the study; retry")
            if not counts["post"]:
                raise ValueError("Selected profiles have no captured posts")
            manifest.update(b'],"scope":')
            manifest.update(json.dumps(scope, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8"))
            manifest.update(b"}")
            study = finalize(counts, scope, manifest.hexdigest())
            db.execute("INSERT INTO studies VALUES (?,?,?,?,?,?,?,?,?,?)", (
                study["id"], study["title"], study["question"], study["preset_id"],
                json.dumps(study["profile_ids"]), json.dumps(study["post_ids"]),
                study["created_at"], json.dumps(study["counts"]),
                json.dumps(study["coverage"]), study["manifest_hash"]))
            db.execute("INSERT INTO study_members SELECT ?,kind,id,post_id,ordinal,content_hash,source_revision FROM pending_members",
                       (study["id"],))
            db.execute("INSERT INTO study_projection SELECT ?,kind,id,record_json FROM pending_projection",
                       (study["id"],))
            db.execute("INSERT INTO study_source_state VALUES (?,?,datetime('now'))",
                       (study["id"], generation))
            return study

    @staticmethod
    def _write_projection(db, study_id: str, records: list[dict], generation: str):
        db.execute("DELETE FROM study_projection WHERE study_id=?", (study_id,))
        db.executemany("INSERT INTO study_projection VALUES (?,?,?,?)", [
            (study_id, row["kind"], row["id"], json.dumps(row, ensure_ascii=False, sort_keys=True))
            for row in records])
        db.execute("INSERT INTO study_source_state VALUES (?,?,datetime('now')) "
                   "ON CONFLICT(study_id) DO UPDATE SET archive_generation=excluded.archive_generation,verified_at=excluded.verified_at",
                   (study_id, generation))

    def save_projection(self, study_id: str, records: list[dict], generation: str):
        with self.connect() as db:
            self._write_projection(db, study_id, records, generation)

    def projection(self, study_id: str, generation: str) -> list[dict] | None:
        with self.connect() as db:
            state = db.execute("SELECT archive_generation FROM study_source_state WHERE study_id=?", (study_id,)).fetchone()
            if not state or state[0] != generation:
                return None
            return [json.loads(row[0]) for row in db.execute(
                "SELECT record_json FROM study_projection WHERE study_id=? ORDER BY kind,entity_id", (study_id,))]

    def projection_current(self, study_id: str, generation: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT archive_generation FROM study_source_state WHERE study_id=?", (study_id,)).fetchone()
            return bool(row and row[0] == generation)

    def search_indexed(self, study_id: str, query: str, kind: str, cursor: int,
                       limit: int, archive_path: Path, archive_generation: str) -> dict:
        from source_index_incremental import refresh_index
        refresh_index(INDEX_DB, archive_path)
        if not INDEX_DB.is_file():
            raise FileNotFoundError("Derived source text index is missing; rebuild it before study search")
        with self.connect() as db:
            db.execute("ATTACH DATABASE ? AS source_index", (str(INDEX_DB),))
            recorded = db.execute("SELECT value FROM source_index.metadata WHERE key='mirror_generation'").fetchone()
            if not recorded or recorded[0] != archive_generation:
                raise RuntimeError("Derived source index is stale after refresh")
            # FTS5 trigram narrows candidates of 3+ characters. Short terms
            # still join the derived index to the study membership index.
            match = ("AND r.rowid IN (SELECT rowid FROM source_index.records_fts "
                     "WHERE records_fts MATCH ?)" if len(query) >= 3 else "")
            sql = ("SELECT r.kind,r.id,r.post_id,r.title,r.body,r.source_url,"
                   "m.content_hash,m.source_revision FROM source_index.records r "
                   "JOIN study_members m ON m.kind=r.kind AND m.entity_id=r.id " +
                   " WHERE m.study_id=? AND (?='all' OR r.kind=?) " + match +
                   " ORDER BY r.kind,r.id")
            args = [study_id, kind, kind]
            if len(query) >= 3:
                args.append('"' + query.replace('"', '""') + '"')
            needle = query.casefold()
            total, items = 0, []
            for row in db.execute(sql, args):
                original = (row["title"] + "\n" if row["kind"] == "post" else "") + row["body"]
                folded = original.casefold()
                at = folded.find(needle)
                if at < 0:
                    continue
                if cursor <= total < cursor + limit:
                    start, end = max(0, at - 70), min(len(original), at + len(query) + 170)
                    items.append({"kind": row["kind"], "id": row["id"], "post_id": row["post_id"],
                                  "source_url": row["source_url"], "source_revision": row["source_revision"],
                                  "content_hash": row["content_hash"],
                                  "excerpt": ("…" if start else "") + original[start:end] + ("…" if end < len(original) else ""),
                                  "match_count": folded.count(needle)})
                total += 1
            return {"items": items, "total": total,
                    "next_cursor": cursor + len(items) if cursor + len(items) < total else None}

    def list(self) -> list[dict]:
        with self.connect() as db:
            return [self._study(row) for row in db.execute("SELECT * FROM studies ORDER BY created_at DESC")]

    def get(self, study_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM studies WHERE id=?", (study_id,)).fetchone()
            return self._study(row) if row else None

    def member(self, study_id: str, kind: str, entity_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM study_members WHERE study_id=? AND kind=? AND entity_id=?",
                             (study_id, kind, entity_id)).fetchone()
            return dict(row) if row else None

    def members(self, study_id: str, kind: str, cursor: int, limit: int) -> dict:
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM study_members WHERE study_id=? AND kind=?", (study_id, kind)).fetchone()[0]
            rows = db.execute("SELECT entity_id AS id,post_id,content_hash,source_revision FROM study_members WHERE study_id=? AND kind=? ORDER BY entity_id LIMIT ? OFFSET ?",
                              (study_id, kind, limit, cursor)).fetchall()
            return {"items": [dict(row) for row in rows], "total": total,
                    "next_cursor": cursor + len(rows) if cursor + len(rows) < total else None}

    def thread_members(self, study_id: str, post_id: str, cursor: int, limit: int) -> dict:
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM study_members WHERE study_id=? AND kind='comment' AND post_id=?",
                               (study_id, post_id)).fetchone()[0]
            rows = db.execute("SELECT entity_id AS id FROM study_members WHERE study_id=? AND kind='comment' AND post_id=? ORDER BY ordinal,entity_id LIMIT ? OFFSET ?",
                              (study_id, post_id, limit, cursor)).fetchall()
            return {"items": [dict(row) for row in rows], "total": total,
                    "next_cursor": cursor + len(rows) if cursor + len(rows) < total else None}

    def all_members(self, study_id: str) -> dict[tuple[str, str], dict]:
        with self.connect() as db:
            rows = db.execute("SELECT kind,entity_id,content_hash FROM study_members WHERE study_id=?", (study_id,))
            return {(row["kind"], row["entity_id"]): dict(row) for row in rows}

    def cached_measurement(self, study_id: str, algorithm: str, input_hash: str,
                           manifest_hash: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT run_id FROM measurement_cache WHERE study_id=? AND algorithm=? "
                             "AND input_hash=? AND manifest_hash=?",
                             (study_id, algorithm, input_hash, manifest_hash)).fetchone()
        return self.measurement(study_id, row[0]) if row else None

    def save_measurement(self, run: dict, input_hash: str | None = None) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO measurement_runs VALUES (?,?,?,?,?,?,?)", (
                run["id"], run["study_id"], run["created_at"], run["algorithm"],
                run["manifest_hash"], run["result_hash"],
                json.dumps(run["result"], ensure_ascii=False, sort_keys=True)))
            if input_hash is not None:
                db.execute("INSERT OR IGNORE INTO measurement_cache VALUES (?,?,?,?,?)",
                           (run["study_id"], run["algorithm"], input_hash,
                            run["manifest_hash"], run["id"]))

    def measurements(self, study_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id,study_id,created_at,algorithm,manifest_hash,result_hash FROM measurement_runs WHERE study_id=? ORDER BY created_at DESC,id DESC", (study_id,))
            return [dict(row) for row in rows]

    def measurement(self, study_id: str, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM measurement_runs WHERE study_id=? AND id=?", (study_id, run_id)).fetchone()
            if not row:
                return None
            value = dict(row)
            value["result"] = json.loads(value.pop("result_json"))
            return value

    def save_context(self, context: dict) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO evidence_contexts VALUES (?,?,?,?,?,?)", (
                context["id"], context["study_id"], context["created_at"],
                context["manifest_hash"], context["payload_hash"],
                json.dumps(context["payload"], ensure_ascii=False, sort_keys=True)))

    def contexts(self, study_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id,study_id,created_at,manifest_hash,payload_hash FROM evidence_contexts WHERE study_id=? ORDER BY created_at DESC,id DESC", (study_id,))
            return [dict(row) for row in rows]

    def context(self, study_id: str, context_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM evidence_contexts WHERE study_id=? AND id=?", (study_id, context_id)).fetchone()
            if not row:
                return None
            value = dict(row)
            value["payload"] = json.loads(value.pop("payload_json"))
            return value

    def create_job(self, job: dict) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO analysis_jobs VALUES (?,?,?,?,?,?)", (
                job["id"], job["study_id"], job["context_id"],
                job["provider"], job["requested_model"], job["created_at"]))
            db.execute("INSERT INTO analysis_job_events VALUES (?,?,?,?,?)", (
                job["id"], 1, "awaiting_session", job["created_at"],
                json.dumps({"sent": False}, sort_keys=True)))

    def jobs(self, study_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT j.*,e.state FROM analysis_jobs j
                JOIN analysis_job_events e ON e.job_id=j.id AND e.sequence=(
                    SELECT max(sequence) FROM analysis_job_events WHERE job_id=j.id)
                WHERE j.study_id=? ORDER BY j.created_at DESC,j.id DESC""", (study_id,))
            return [dict(row) for row in rows]

    def job(self, study_id: str, job_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM analysis_jobs WHERE study_id=? AND id=?", (study_id, job_id)).fetchone()
            if not row:
                return None
            value = dict(row)
            value["events"] = [dict(event) for event in db.execute(
                "SELECT sequence,state,created_at,detail_json FROM analysis_job_events WHERE job_id=? ORDER BY sequence", (job_id,))]
            for event in value["events"]:
                event["detail"] = json.loads(event.pop("detail_json"))
            value["state"] = value["events"][-1]["state"]
            return value

    def cancel_awaiting_job(self, study_id: str, job_id: str, now: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT e.sequence,e.state FROM analysis_jobs j
                JOIN analysis_job_events e ON e.job_id=j.id AND e.sequence=(
                    SELECT max(sequence) FROM analysis_job_events WHERE job_id=j.id)
                WHERE j.study_id=? AND j.id=?""", (study_id, job_id)).fetchone()
            if not row:
                raise LookupError("Analysis job not found")
            if row["state"] in {"cancelled", "cancel_requested"}:
                return False
            if row["state"] not in {"awaiting_session", "running"}:
                raise ValueError("Only an awaiting or running job can be cancelled")
            state = "cancelled" if row["state"] == "awaiting_session" else "cancel_requested"
            db.execute("INSERT INTO analysis_job_events VALUES (?,?,?,?,?)", (
                job_id, row["sequence"] + 1, state, now,
                json.dumps({"sent": row["state"] == "running", "reason": "user_cancelled"}, sort_keys=True)))
            return True

    def begin_job(self, study_id: str, job_id: str, model: str, owner_pid: int, now: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT e.sequence,e.state FROM analysis_jobs j
                JOIN analysis_job_events e ON e.job_id=j.id AND e.sequence=(
                    SELECT max(sequence) FROM analysis_job_events WHERE job_id=j.id)
                WHERE j.study_id=? AND j.id=?""", (study_id, job_id)).fetchone()
            if not row:
                raise LookupError("Analysis job not found")
            if row["state"] != "awaiting_session":
                raise ValueError(f"Analysis job is {row['state']}, expected awaiting_session")
            db.execute("INSERT INTO analysis_job_events VALUES (?,?,?,?,?)", (
                job_id, row["sequence"] + 1, "running", now,
                json.dumps({"model": model, "sent": False, "owner_pid": owner_pid}, sort_keys=True)))
            db.execute("INSERT OR REPLACE INTO analysis_job_leases VALUES (?,?,?,?)",
                       (job_id, owner_pid, now, now))

    def heartbeat_job(self, job_id: str, owner_pid: int, now: str) -> bool:
        with self.connect() as db:
            row = db.execute("UPDATE analysis_job_leases SET heartbeat_at=? WHERE job_id=? AND owner_pid=?",
                             (now, job_id, owner_pid))
            return row.rowcount == 1

    def release_job(self, job_id: str, owner_pid: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM analysis_job_leases WHERE job_id=? AND owner_pid=?", (job_id, owner_pid))

    def retry_job(self, study_id: str, job_id: str, now: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT e.sequence,e.state FROM analysis_jobs j
                JOIN analysis_job_events e ON e.job_id=j.id AND e.sequence=(
                    SELECT max(sequence) FROM analysis_job_events WHERE job_id=j.id)
                WHERE j.study_id=? AND j.id=?""", (study_id, job_id)).fetchone()
            if not row:
                raise LookupError("Analysis job not found")
            if row["state"] not in {"failed", "interrupted", "cancelled"}:
                raise ValueError("Only a failed, interrupted or cancelled job can be retried")
            if db.execute("SELECT 1 FROM analysis_outputs WHERE job_id=?", (job_id,)).fetchone():
                raise ValueError("A saved output cannot be retried")
            db.execute("INSERT INTO analysis_job_events VALUES (?,?,?,?,?)", (
                job_id, row["sequence"] + 1, "awaiting_session", now,
                json.dumps({"sent": False, "retry_of_state": row["state"],
                            "explicit_retry": True}, sort_keys=True)))

    def recover_stale_jobs(self, stale_seconds: int = 60) -> list[str]:
        """Mark lost attempts; never auto-resend a possibly delivered model request."""
        recovered = []
        now = datetime.now(timezone.utc)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("""SELECT j.id,e.sequence,e.state,l.heartbeat_at
                FROM analysis_jobs j JOIN analysis_job_events e ON e.job_id=j.id AND
                    e.sequence=(SELECT max(sequence) FROM analysis_job_events WHERE job_id=j.id)
                LEFT JOIN analysis_job_leases l ON l.job_id=j.id
                WHERE e.state IN ('running','cancel_requested')""").fetchall()
            for row in rows:
                if row["heartbeat_at"]:
                    try:
                        age = (now - datetime.fromisoformat(row["heartbeat_at"])).total_seconds()
                    except ValueError:
                        age = stale_seconds + 1
                    if age < stale_seconds:
                        continue
                new_state = "cancelled" if row["state"] == "cancel_requested" else "interrupted"
                db.execute("INSERT INTO analysis_job_events VALUES (?,?,?,?,?)", (
                    row["id"], row["sequence"] + 1, new_state, now.isoformat(),
                    json.dumps({"reason": "worker_lease_expired", "provider_outcome": "unknown",
                                "auto_retry": False}, sort_keys=True)))
                db.execute("DELETE FROM analysis_job_leases WHERE job_id=?", (row["id"],))
                recovered.append(row["id"])
        return recovered

    def advance_job(self, study_id: str, job_id: str, expected: str,
                    state: str, now: str, detail: dict) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT e.sequence,e.state FROM analysis_jobs j
                JOIN analysis_job_events e ON e.job_id=j.id AND e.sequence=(
                    SELECT max(sequence) FROM analysis_job_events WHERE job_id=j.id)
                WHERE j.study_id=? AND j.id=?""", (study_id, job_id)).fetchone()
            if not row:
                raise LookupError("Analysis job not found")
            if row["state"] != expected:
                raise ValueError(f"Analysis job is {row['state']}, expected {expected}")
            db.execute("INSERT INTO analysis_job_events VALUES (?,?,?,?,?)", (
                job_id, row["sequence"] + 1, state, now,
                json.dumps(detail, ensure_ascii=False, sort_keys=True)))

    def save_job_output(self, study_id: str, job_id: str, output: dict) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT e.sequence,e.state FROM analysis_jobs j
                JOIN analysis_job_events e ON e.job_id=j.id AND e.sequence=(
                    SELECT max(sequence) FROM analysis_job_events WHERE job_id=j.id)
                WHERE j.study_id=? AND j.id=?""", (study_id, job_id)).fetchone()
            if not row:
                raise LookupError("Analysis job not found")
            if row["state"] != "running":
                raise ValueError("Analysis job is not running")
            db.execute("INSERT INTO analysis_outputs VALUES (?,?,?,?,?,?,?)", (
                job_id, output["created_at"], output["model"],
                output["thread_id"], output["turn_id"], output["output_hash"],
                json.dumps(output["result"], ensure_ascii=False, sort_keys=True)))
            db.execute("INSERT INTO analysis_job_events VALUES (?,?,?,?,?)", (
                job_id, row["sequence"] + 1, "awaiting_review", output["created_at"],
                json.dumps({"output_hash": output["output_hash"], "sent": True}, sort_keys=True)))

    def job_output(self, study_id: str, job_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("""SELECT o.* FROM analysis_outputs o
                JOIN analysis_jobs j ON j.id=o.job_id WHERE j.study_id=? AND j.id=?""",
                (study_id, job_id)).fetchone()
            if not row:
                return None
            value = dict(row)
            value["result"] = json.loads(value.pop("output_json"))
            return value

    def save_finding(self, finding: dict, evidence: list[dict]) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO red_findings VALUES (?,?,?,?,?,?,?,?,?,?)", (
                finding["id"], finding["study_id"], finding["created_at"],
                finding["claim"], finding["scope_note"], finding["limitations"],
                finding["manifest_hash"], finding.get("source_job_id"), finding["finding_hash"],
                json.dumps(finding["counter_search"], ensure_ascii=False, sort_keys=True)))
            db.executemany("INSERT INTO red_finding_evidence "
                           "(finding_id,ordinal,role,source_kind,source_id,source_hash,exact_quote,note,derived_id,derived_hash,time_ms) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
                (finding["id"], index, item["role"], item["source_kind"], item["source_id"],
                 item["source_hash"], item.get("exact_quote"), item["note"],
                 item.get("derived_id"), item.get("derived_hash"), item.get("time_ms"))
                for index, item in enumerate(evidence)])
            db.execute("INSERT INTO red_finding_events VALUES (?,?,?,?,?,?)", (
                finding["id"], 1, "proposed", finding["created_at"], "", "Awaiting human review"))

    def findings(self, study_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT f.id,f.study_id,f.created_at,f.claim,f.manifest_hash,
                e.state FROM red_findings f JOIN red_finding_events e ON e.finding_id=f.id AND
                e.sequence=(SELECT max(sequence) FROM red_finding_events WHERE finding_id=f.id)
                WHERE f.study_id=? ORDER BY f.created_at DESC,f.id DESC""", (study_id,))
            return [dict(row) for row in rows]

    def finding(self, study_id: str, finding_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM red_findings WHERE study_id=? AND id=?",
                             (study_id, finding_id)).fetchone()
            if not row:
                return None
            value = dict(row)
            value["counter_search"] = json.loads(value.pop("counter_search_json"))
            value["evidence"] = []
            for item in db.execute(
                "SELECT role,source_kind,source_id,source_hash,exact_quote,note,derived_id,derived_hash,time_ms "
                "FROM red_finding_evidence WHERE finding_id=? ORDER BY ordinal", (finding_id,)):
                evidence = dict(item)
                evidence = {key: value for key, value in evidence.items()
                            if value is not None or key in {"exact_quote"}}
                value["evidence"].append(evidence)
            value["events"] = [dict(item) for item in db.execute(
                "SELECT sequence,state,created_at,reviewer,rationale FROM red_finding_events WHERE finding_id=? ORDER BY sequence",
                (finding_id,))]
            value["state"] = value["events"][-1]["state"]
            return value

    def review_finding(self, study_id: str, finding_id: str, state: str,
                       reviewer: str, rationale: str, now: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT e.sequence,e.state FROM red_findings f
                JOIN red_finding_events e ON e.finding_id=f.id AND e.sequence=(
                    SELECT max(sequence) FROM red_finding_events WHERE finding_id=f.id)
                WHERE f.study_id=? AND f.id=?""", (study_id, finding_id)).fetchone()
            if not row:
                raise LookupError("Finding not found")
            if row["state"] != "proposed":
                raise ValueError("Only a proposed finding can receive this review")
            db.execute("INSERT INTO red_finding_events VALUES (?,?,?,?,?,?)", (
                finding_id, row["sequence"] + 1, state, now, reviewer, rationale))
