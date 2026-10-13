"""Refresh the disposable text index from archive revisions without rehashing the DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from study_storage import INDEX_DB, MIRROR_DB, SqliteArchive


def refresh_index(index: Path = INDEX_DB, mirror: Path = MIRROR_DB) -> dict:
    if not index.is_file():
        raise FileNotFoundError("Derived text index is missing; build it once")
    archive = SqliteArchive(mirror)
    generation = archive.generation()
    db = sqlite3.connect(index, timeout=30, uri=True)
    db.row_factory = sqlite3.Row
    try:
        meta = {row[0]: row[1] for row in db.execute("SELECT key,value FROM metadata")}
        if meta.get("mirror_generation") == generation:
            return {"status": "current", "changed_posts": 0, "generation": generation}
        if "observations_max_rowid" not in meta or "comments_max_rowid" not in meta:
            raise RuntimeError("Derived index predates incremental metadata; rebuild it once")
        db.execute("ATTACH DATABASE ? AS mirror", (mirror.as_uri() + "?mode=ro",))
        db.execute("BEGIN IMMEDIATE")
        old_observation = int(meta["observations_max_rowid"])
        old_comment = int(meta["comments_max_rowid"])
        changed = {row[0] for row in db.execute(
            "SELECT DISTINCT coalesce(p.id,c.post_id) "
            "FROM mirror.observations o "
            "LEFT JOIN mirror.posts p ON p.id=o.entity_id "
            "LEFT JOIN mirror.comments c ON c.id=o.entity_id "
            "WHERE o.rowid>? AND coalesce(p.id,c.post_id) IS NOT NULL",
            (old_observation,))}
        changed.update(row[0] for row in db.execute(
            "SELECT p.id FROM mirror.posts p LEFT JOIN records r ON r.kind='post' AND r.id=p.id "
            "WHERE r.id IS NULL"))
        changed.update(row[0] for row in db.execute(
            "SELECT DISTINCT post_id FROM mirror.comments WHERE rowid>?", (old_comment,)))
        changed.update(row[0] for row in db.execute(
            "SELECT DISTINCT r.post_id FROM records r "
            "LEFT JOIN mirror.comments c ON c.id=r.id "
            "WHERE r.kind='comment' AND c.id IS NULL"))
        changed.update(row[0] for row in db.execute(
            "SELECT r.post_id FROM records r LEFT JOIN mirror.posts p ON p.id=r.post_id "
            "WHERE r.kind='post' AND p.id IS NULL"))
        for post_id in sorted(changed):
            old_rows = list(db.execute("SELECT rowid,title,body FROM records WHERE post_id=?", (post_id,)))
            db.executemany("INSERT INTO records_fts(records_fts,rowid,title,body) "
                           "VALUES('delete',?,?,?)", old_rows)
            db.execute("DELETE FROM records WHERE post_id=?", (post_id,))
            post = db.execute("SELECT id,profile_id,source_id,title,body,source_url "
                              "FROM mirror.posts WHERE id=?", (post_id,)).fetchone()
            if post:
                db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?)",
                           ("post", post["id"], post["profile_id"], post["id"],
                            post["source_id"], post["title"], post["body"], post["source_url"]))
                for row in db.execute("SELECT id,source_id,body FROM mirror.comments "
                                      "WHERE post_id=? ORDER BY ordinal,id", (post_id,)):
                    db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?)",
                               ("comment", row["id"], post["profile_id"], post_id,
                                row["source_id"], "", row["body"], post["source_url"]))
            new_rows = list(db.execute("SELECT rowid,title,body FROM records WHERE post_id=?", (post_id,)))
            db.executemany("INSERT INTO records_fts(rowid,title,body) VALUES(?,?,?)", new_rows)
        new_observation = db.execute(
            "SELECT coalesce(max(rowid),0) FROM mirror.observations").fetchone()[0]
        new_comment = db.execute(
            "SELECT coalesce(max(rowid),0) FROM mirror.comments").fetchone()[0]
        if archive.generation() != generation:
            raise RuntimeError("Captured archive changed while refreshing its text index")
        db.executemany("INSERT INTO metadata(key,value) VALUES(?,?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       [("mirror_generation", generation),
                        ("observations_max_rowid", str(new_observation)),
                        ("comments_max_rowid", str(new_comment)),
                        ("mirror_sha256", "incremental; see mirror_generation")])
        db.commit()
        return {"status": "refreshed", "changed_posts": len(changed),
                "generation": generation, "observation_cursor": new_observation,
                "comment_cursor": new_comment}
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
