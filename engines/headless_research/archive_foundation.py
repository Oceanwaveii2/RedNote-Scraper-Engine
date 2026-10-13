"""Read-only archive reconciliation and a disposable Red Engine search projection.

The captured SQLite databases and media are never modified by this command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "Red Note Gemini"
MIRROR = ARCHIVE / "data_v2" / "rednote-v2.sqlite"
LEGACY = ROOT / "01_Mining_App" / "Reel_Harvest" / "rednote.sqlite"
INDEX = Path(__file__).resolve().parent / "data" / "source-index.sqlite"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def source_ids(db: sqlite3.Connection, table: str, column: str) -> set[str]:
    return {str(row[0]) for row in db.execute(f"SELECT {column} FROM {table}")}


def audit(verify_media: bool = True) -> dict:
    """Compare source databases, imported JSON, coverage and actual local media."""
    started = time.monotonic()
    mirror_hash_before = digest(MIRROR)
    legacy_hash_before = digest(LEGACY)
    with readonly(MIRROR) as db, readonly(LEGACY) as old:
        counts = {name: db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                  for name in ("profiles", "posts", "comments", "assets", "files", "imports", "observations")}
        legacy_counts = {name: old.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                         for name in ("rednote_profiles", "rednote_posts", "rednote_comments", "rednote_media")}
        current_profiles = source_ids(db, "entities WHERE kind='profile'", "source_id")
        current_posts = source_ids(db, "posts", "source_id")
        old_profiles = source_ids(old, "rednote_profiles", "profile_id")
        old_posts = source_ids(old, "rednote_posts", "post_id")
        current_comment_keys = {(row[0], row[1]) for row in db.execute(
            "SELECT p.source_id,c.source_id FROM comments c JOIN posts p ON p.id=c.post_id")}
        old_comment_keys = {(row[0], row[1]) for row in old.execute(
            "SELECT post_id,comment_id FROM rednote_comments")}
        imported = list(db.execute("SELECT path,fingerprint,revision FROM imports ORDER BY path"))
        imports = Counter()
        import_issues = []
        for item in imported:
            path = (ARCHIVE / item["path"]).resolve()
            if not path.is_relative_to(ARCHIVE) or not path.is_file():
                imports["missing"] += 1
                if len(import_issues) < 30:
                    import_issues.append({"path": item["path"], "reason": "missing"})
                continue
            actual = digest(path)
            if actual != item["revision"]:
                imports["changed"] += 1
                if len(import_issues) < 30:
                    import_issues.append({"path": item["path"], "reason": "revision_mismatch"})
            else:
                imports["matching"] += 1
        files = Counter()
        file_issues = []
        media_bytes = 0
        for item in db.execute("SELECT path,sha256,bytes,verification FROM files ORDER BY path"):
            path = (ARCHIVE / item["path"]).resolve()
            if not path.is_relative_to(ARCHIVE) or not path.is_file():
                status = "missing"
            else:
                actual_bytes = path.stat().st_size
                media_bytes += actual_bytes
                if actual_bytes != item["bytes"]:
                    status = "size_mismatch"
                elif verify_media and digest(path) != item["sha256"]:
                    status = "hash_mismatch"
                else:
                    status = "matching" if verify_media else "size_only"
            files[status] += 1
            if status not in {"matching", "size_only"} and len(file_issues) < 30:
                file_issues.append({"path": item["path"], "reason": status})
        coverage = Counter()
        reasons = Counter()
        for row in db.execute("SELECT coverage FROM posts"):
            try:
                value = json.loads(row[0])
            except (ValueError, TypeError):
                value = {}
            coverage[str(value.get("status") or "unknown")] += 1
            if value.get("reason"):
                reasons[str(value["reason"])] += 1
        unresolved = db.execute("""
            SELECT count(*) FROM comments c WHERE c.parent_source_id IS NOT NULL
            AND c.parent_source_id <> '' AND NOT EXISTS (
                SELECT 1 FROM comments p WHERE p.post_id=c.post_id
                AND p.source_id=c.parent_source_id)
        """).fetchone()[0]
        orphan_comments = db.execute("""
            SELECT count(*) FROM comments c LEFT JOIN posts p ON p.id=c.post_id WHERE p.id IS NULL
        """).fetchone()[0]
        orphan_assets = db.execute("""
            SELECT count(*) FROM assets a LEFT JOIN entities e ON e.id=a.owner_id WHERE e.id IS NULL
        """).fetchone()[0]
        media_roles = [dict(row) for row in db.execute("""
            SELECT role,count(*) AS total,
                   sum(CASE WHEN file_id IS NOT NULL THEN 1 ELSE 0 END) AS with_file,
                   sum(CASE WHEN source_available=1 THEN 1 ELSE 0 END) AS source_available
            FROM assets GROUP BY role ORDER BY role
        """)]
        source_json = list((ARCHIVE / "profile_archives").glob("*/posts/*.json"))
        imported_paths = {row["path"] for row in imported}
        unindexed_json = [p.relative_to(ARCHIVE).as_posix() for p in source_json
                          if p.relative_to(ARCHIVE).as_posix() not in imported_paths]
        result = {
            "schema": "red-engine.archive-audit/1",
            "source": {"mirror": str(MIRROR), "mirror_sha256": mirror_hash_before,
                       "legacy": str(LEGACY), "legacy_sha256": legacy_hash_before,
                       "mirror_journal": db.execute("PRAGMA journal_mode").fetchone()[0]},
            "mirror_counts": counts, "legacy_counts": legacy_counts,
            "identity_reconciliation": {
                "profile_overlap": len(current_profiles & old_profiles),
                "legacy_only_profiles": sorted(old_profiles - current_profiles),
                "mirror_only_profiles": sorted(current_profiles - old_profiles),
                "post_overlap": len(current_posts & old_posts),
                "legacy_only_posts": sorted(old_posts - current_posts),
                "mirror_only_post_count": len(current_posts - old_posts),
                "comment_overlap": len(current_comment_keys & old_comment_keys),
                "legacy_only_comment_count": len(old_comment_keys - current_comment_keys),
                "action": "Legacy-only entities require reviewed import; no source records were merged."},
            "imports": {**dict(imports), "source_post_json": len(source_json),
                        "unindexed_source_json_count": len(unindexed_json),
                        "unindexed_source_json_sample": unindexed_json[:30],
                        "issues_sample": import_issues},
            "media": {**dict(files), "checked_bytes": media_bytes,
                      "hashes_checked": verify_media, "issues_sample": file_issues,
                      "asset_roles": media_roles,
                      "assets_without_file": db.execute("SELECT count(*) FROM assets WHERE file_id IS NULL").fetchone()[0]},
            "coverage": {"post_statuses": dict(coverage), "post_reasons": dict(reasons),
                         "unresolved_comment_parents": unresolved,
                         "orphan_comments": orphan_comments, "orphan_assets": orphan_assets},
            "integrity_check": db.execute("PRAGMA quick_check").fetchone()[0],
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
    queue = ARCHIVE / "clone_queue.json"
    if queue.is_file():
        try:
            raw = json.loads(queue.read_text(encoding="utf-8"))
            result["capture_checkpoint"] = {
                "path": str(queue), "bytes": queue.stat().st_size,
                "jobs": [{"status": job.get("status"), "phase": job.get("phase"),
                          "progress": job.get("progress"), "performance": job.get("performance"),
                          "options": {key: job.get("options", {}).get(key) for key in
                                      ("postPacingSeconds", "mediaPacingSeconds", "pageDelaySeconds")}}
                         for job in raw.get("jobs", [])]}
        except (OSError, ValueError, TypeError):
            result["capture_checkpoint"] = {"unreadable": True}
    if digest(MIRROR) != mirror_hash_before or digest(LEGACY) != legacy_hash_before:
        result["source_changed_during_audit"] = True
    else:
        result["source_changed_during_audit"] = False
    return result


def build_index(destination: Path = INDEX) -> dict:
    """Atomically replace a derived, disposable index after a stable source read."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = digest(MIRROR)
    legacy_hash = digest(LEGACY)
    temporary = destination.with_name(destination.name + ".partial")
    if temporary.exists():
        raise FileExistsError(f"Unfinished index exists; inspect it before replacement: {temporary}")
    source = readonly(MIRROR)
    legacy = readonly(LEGACY)
    target = sqlite3.connect(temporary)
    counts = Counter()
    try:
        target.executescript("""
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE records(kind TEXT NOT NULL,id TEXT NOT NULL,profile_id TEXT,
                post_id TEXT,source_id TEXT,title TEXT NOT NULL,body TEXT NOT NULL,
                source_url TEXT,PRIMARY KEY(kind,id));
            CREATE INDEX records_scope ON records(profile_id,post_id,kind);
            CREATE INDEX records_source_id ON records(kind,source_id);
            CREATE VIRTUAL TABLE records_fts USING fts5(title,body,content='records',content_rowid='rowid',tokenize='trigram');
            CREATE TABLE legacy_entities(kind TEXT NOT NULL,source_id TEXT NOT NULL,
                profile_id TEXT,post_id TEXT,parent_id TEXT,reconciliation TEXT NOT NULL,
                row_sha256 TEXT NOT NULL,raw_json TEXT NOT NULL,PRIMARY KEY(kind,source_id));
            CREATE INDEX legacy_entities_scope ON legacy_entities(profile_id,post_id,kind);
        """)
        for row in source.execute("SELECT p.id,p.profile_id,p.source_id,p.title,p.body,p.source_url FROM posts p"):
            target.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?)",
                           ("post", row["id"], row["profile_id"], row["id"], row["source_id"],
                            row["title"], row["body"], row["source_url"]))
            counts["posts"] += 1
        for row in source.execute("""
            SELECT c.id,p.profile_id,c.post_id,c.source_id,c.body,p.source_url
            FROM comments c JOIN posts p ON p.id=c.post_id
        """):
            target.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?)",
                           ("comment", row["id"], row["profile_id"], row["post_id"],
                            row["source_id"], "", row["body"], row["source_url"]))
            counts["comments"] += 1
        target.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')")
        mirror_profiles = source_ids(source, "entities WHERE kind='profile'", "source_id")
        mirror_posts = source_ids(source, "posts", "source_id")
        mirror_comments = {(row[0], row[1]) for row in source.execute(
            "SELECT p.source_id,c.source_id FROM comments c JOIN posts p ON p.id=c.post_id")}
        for kind, table, id_field in (
            ("profile", "rednote_profiles", "profile_id"),
            ("post", "rednote_posts", "post_id"),
            ("comment", "rednote_comments", "comment_id"),
            ("media", "rednote_media", "media_id"),
        ):
            for row in legacy.execute(f"SELECT * FROM {table}"):
                data = dict(row)
                sid = str(data[id_field])
                profile_id = data.get("profile_id")
                post_id = data.get("post_id")
                parent_id = data.get("parent_comment_id")
                overlap = (sid in mirror_profiles if kind == "profile" else
                           sid in mirror_posts if kind == "post" else
                           (post_id, sid) in mirror_comments if kind == "comment" else False)
                state = "overlap_unmerged" if overlap else "legacy_only_unmerged"
                raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                target.execute("INSERT INTO legacy_entities VALUES(?,?,?,?,?,?,?,?)",
                               (kind, sid, profile_id, post_id, parent_id, state,
                                hashlib.sha256(raw.encode("utf-8")).hexdigest(), raw))
                counts["legacy_" + kind + "_" + state] += 1
        from study_storage import SqliteArchive
        target.executemany("INSERT INTO metadata VALUES(?,?)",
                           [("schema", "red-engine.source-index/1"), ("mirror_sha256", source_hash),
                            ("mirror_path", str(MIRROR)), ("legacy_sha256", legacy_hash),
                            ("legacy_path", str(LEGACY)),
                            ("mirror_generation", SqliteArchive(MIRROR).generation()),
                            ("observations_max_rowid", str(source.execute(
                                "SELECT coalesce(max(rowid),0) FROM observations").fetchone()[0])),
                            ("comments_max_rowid", str(source.execute(
                                "SELECT coalesce(max(rowid),0) FROM comments").fetchone()[0]))])
        target.commit()
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Derived index integrity check failed")
    except BaseException:
        target.close()
        temporary.unlink(missing_ok=True)
        raise
    finally:
        source.close()
        legacy.close()
    target.close()
    if digest(MIRROR) != source_hash or digest(LEGACY) != legacy_hash:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("A source archive changed while indexing; index was discarded")
    os.replace(temporary, destination)
    return {"index": str(destination), "mirror_sha256": source_hash,
            "legacy_sha256": legacy_hash, "records": dict(counts), "index_bytes": destination.stat().st_size,
            "note": "Derived index only; the captured mirror was not modified."}


def search_index(query: str, profile_id: str | None = None, limit: int = 30) -> dict:
    """Find exact original-language substrings; reject a stale projection."""
    if not 1 <= len(query.strip()) <= 120 or not 1 <= limit <= 100:
        raise ValueError("Query must contain 1–120 characters and limit must be 1–100")
    if not INDEX.is_file():
        raise FileNotFoundError("Build the source index first")
    from source_index_incremental import refresh_index
    refresh_index(INDEX, MIRROR)
    with readonly(INDEX) as db:
        from study_storage import SqliteArchive
        recorded = db.execute("SELECT value FROM metadata WHERE key='mirror_generation'").fetchone()
        if not recorded or recorded[0] != SqliteArchive(MIRROR).generation():
            raise RuntimeError("Source index changed during search; retry")
        # Trigram FTS narrows longer terms. For one or two Chinese characters,
        # an indexed trigram has no candidates, so scan the small derived table.
        if len(query.strip()) >= 3:
            candidate_ids = [row[0] for row in db.execute(
                "SELECT rowid FROM records_fts WHERE records_fts MATCH ?", ('"' + query.strip().replace('"', '""') + '"',))]
            rows = []
            for start in range(0, len(candidate_ids), 500):
                batch = candidate_ids[start:start + 500]
                rows.extend(db.execute("SELECT * FROM records WHERE rowid IN (" +
                                       ",".join("?" for _ in batch) + ")", batch))
        else:
            rows = db.execute("SELECT * FROM records")
        needle = query.strip().casefold()
        matches = []
        total = 0
        for row in rows:
            if profile_id and row["profile_id"] != profile_id:
                continue
            original = (row["title"] + "\n" if row["title"] else "") + row["body"]
            offset = original.casefold().find(needle)
            if offset < 0:
                continue
            total += 1
            if len(matches) < limit:
                start = max(0, offset - 70)
                end = min(len(original), offset + len(needle) + 170)
                matches.append({"kind": row["kind"], "id": row["id"],
                                "post_id": row["post_id"], "source_id": row["source_id"],
                                "source_url": row["source_url"],
                                "excerpt": ("…" if start else "") + original[start:end] +
                                           ("…" if end < len(original) else "")})
        return {"query": query.strip(), "profile_id": profile_id, "total_matches": total,
                "items": matches, "mirror_generation": recorded[0],
                "note": "Exact captured-text matches, not semantic or population findings."}


def snapshot_metadata() -> dict:
    """Back up both SQLite databases and imported JSON without touching sources.

    Media bytes remain in the original archive; the manifest records their
    verified locations and hashes so missing backup coverage stays explicit.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(__file__).resolve().parent / "data" / "snapshots" / stamp
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": "red-engine.metadata-snapshot/1", "created_utc": stamp,
                "source": {}, "json": {"copied": 0, "bytes": 0},
                "media_bytes_copied": False}
    for label, path in (("mirror", MIRROR), ("legacy", LEGACY)):
        destination = folder / (label + ".sqlite")
        before = digest(path)
        with closing(readonly(path)) as source, sqlite3.connect(destination) as target:
            source.backup(target)
        if digest(path) != before:
            raise RuntimeError("Source database changed during snapshot; inspect snapshot before use")
        manifest["source"][label] = {"path": str(path), "source_sha256": before,
                                     "backup_sha256": digest(destination),
                                     "backup_bytes": destination.stat().st_size}
    with closing(readonly(MIRROR)) as db:
        media = [dict(row) for row in db.execute("SELECT path,sha256,bytes,verification FROM files ORDER BY path")]
        imports = list(db.execute("SELECT path,revision FROM imports ORDER BY path"))
    for row in imports:
        path = (ARCHIVE / row["path"]).resolve()
        if not path.is_relative_to(ARCHIVE) or not path.is_file() or digest(path) != row["revision"]:
            raise RuntimeError("Imported JSON changed during snapshot; snapshot requires review")
        target = folder / "imported-json" / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if digest(target) != row["revision"]:
            raise RuntimeError("Copied JSON failed integrity check")
        manifest["json"]["copied"] += 1
        manifest["json"]["bytes"] += target.stat().st_size
    manifest["media_manifest"] = "media-manifest.json"
    (folder / "media-manifest.json").write_text(json.dumps(media, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "snapshot-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"snapshot": str(folder), **manifest}


def decode_media() -> dict:
    """Read every local image and decode one video frame without editing files."""
    from PIL import Image

    counts = Counter()
    failures = []
    with closing(readonly(MIRROR)) as db:
        rows = list(db.execute("SELECT path,mime FROM files ORDER BY path"))
    for row in rows:
        path = (ARCHIVE / row["path"]).resolve()
        if not path.is_relative_to(ARCHIVE) or not path.is_file():
            counts["missing"] += 1
            continue
        mime = row["mime"] or ""
        try:
            if mime.startswith("image/") or path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
                with Image.open(path) as image:
                    image.load()
                counts["image_decoded"] += 1
            elif mime.startswith("video/") or path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}:
                result = subprocess.run(
                    ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
                     "-an", "-frames:v", "1", "-f", "null", "-"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    timeout=45, check=False)
                if result.returncode:
                    raise ValueError("ffmpeg first-frame decode failed")
                counts["video_first_frame_decoded"] += 1
            else:
                counts["unsupported_mime"] += 1
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            counts["decode_failed"] += 1
            if len(failures) < 30:
                failures.append({"path": row["path"], "error_type": type(exc).__name__})
    return {"schema": "red-engine.media-decode/1", "files": len(rows),
            "counts": dict(counts), "failures_sample": failures,
            "scope": "Images fully loaded; one video frame decoded per file. Full video playback and audio are not established."}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("audit", "index", "search", "snapshot", "decode"))
    parser.add_argument("--size-only", action="store_true", help="Skip media SHA-256 verification")
    parser.add_argument("--output", type=Path, help="Write JSON report outside the source archive")
    parser.add_argument("--query", help="Exact text to find in the derived index")
    parser.add_argument("--profile-id", help="Limit an index search to one captured profile ID")
    parser.add_argument("--limit", type=int, default=30)
    arguments = parser.parse_args()
    if arguments.action == "audit":
        result = audit(not arguments.size_only)
    elif arguments.action == "index":
        result = build_index()
    elif arguments.action == "snapshot":
        result = snapshot_metadata()
    elif arguments.action == "decode":
        result = decode_media()
    else:
        if not arguments.query:
            parser.error("search requires --query")
        result = search_index(arguments.query, arguments.profile_id, arguments.limit)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
