"""Isolated SQLite-copy check for revision-based source index refresh."""

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from contextlib import closing

from source_index_incremental import refresh_index
from study_storage import INDEX_DB, MIRROR_DB


def run():
    with tempfile.TemporaryDirectory(prefix="red-index-refresh-") as folder:
        mirror, index = Path(folder) / "mirror.sqlite", Path(folder) / "index.sqlite"
        with closing(sqlite3.connect(MIRROR_DB.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(mirror)) as target:
                source.backup(target)
        shutil.copy2(INDEX_DB, index)
        baseline = refresh_index(index, mirror)
        with closing(sqlite3.connect(mirror)) as db:
            post = db.execute("SELECT id FROM posts ORDER BY id LIMIT 1").fetchone()[0]
            comment = db.execute(
                "SELECT id FROM comments WHERE post_id<>? ORDER BY id LIMIT 1", (post,)).fetchone()[0]
            db.execute("UPDATE posts SET title='incremental probe orchid' WHERE id=?", (post,))
            db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?)",
                       ("incremental-probe-observation", post, "incremental-probe-revision",
                        "probe", "probe", "{}"))
            db.execute("UPDATE comments SET body='incremental probe marigold' WHERE id=?", (comment,))
            db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?)",
                       ("incremental-probe-comment-observation", comment, "incremental-probe-comment-revision",
                        "probe", "probe", "{}"))
            db.commit()
        changed = refresh_index(index, mirror)
        with closing(sqlite3.connect(index)) as db:
            hit = db.execute("SELECT r.id FROM records_fts f JOIN records r ON r.rowid=f.rowid "
                             "WHERE records_fts MATCH ?", ('"incremental probe orchid"',)).fetchone()
            comment_hit = db.execute("SELECT r.id FROM records_fts f JOIN records r ON r.rowid=f.rowid "
                                     "WHERE records_fts MATCH ?", ('"incremental probe marigold"',)).fetchone()
        if not hit or hit[0] != post or not comment_hit or comment_hit[0] != comment or changed["changed_posts"] != 2:
            raise AssertionError("Incremental refresh failed to update changed post/comment FTS")
        with closing(sqlite3.connect(mirror)) as db:
            db.execute("DELETE FROM comments WHERE id=?", (comment,))
            db.commit()
        removed = refresh_index(index, mirror)
        with closing(sqlite3.connect(index)) as db:
            deleted_hit = db.execute("SELECT id FROM records WHERE kind='comment' AND id=?", (comment,)).fetchone()
        if deleted_hit or removed["changed_posts"] != 1:
            raise AssertionError("Incremental refresh failed to remove deleted comment")
        return {"baseline": baseline["status"], "changed_posts": changed["changed_posts"],
                "comment_updates": True, "comment_deletes": True,
                "fts_updated": True, "sources_modified": False}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
