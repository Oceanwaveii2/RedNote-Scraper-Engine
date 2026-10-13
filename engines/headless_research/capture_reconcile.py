"""Read-only per-post reconciliation of the mirror and paused clone checkpoint."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from study_storage import MIRROR_DB


ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "Red Note Gemini" / "clone_queue.json"


def run() -> dict:
    raw = json.loads(QUEUE.read_text(encoding="utf-8"))
    jobs = raw.get("jobs") or []
    queue_posts = {post["postId"]: post for job in jobs for post in job.get("posts", [])
                   if isinstance(post, dict) and post.get("postId")}
    db = sqlite3.connect(MIRROR_DB.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = list(db.execute("""SELECT p.id,p.source_id,p.title,p.coverage,p.captured_at,
            (SELECT count(*) FROM comments c WHERE c.post_id=p.id) AS comments,
            (SELECT count(*) FROM comments c WHERE c.post_id=p.id AND c.parent_source_id IS NOT NULL
             AND c.parent_source_id!='' AND NOT EXISTS
             (SELECT 1 FROM comments parent WHERE parent.post_id=c.post_id
              AND parent.source_id=c.parent_source_id)) AS missing_parents,
            (SELECT count(*) FROM assets a WHERE a.owner_id=p.id AND a.role IN ('image','video','cover')) AS post_media,
            (SELECT count(*) FROM assets a JOIN files f ON f.id=a.file_id WHERE a.owner_id=p.id
             AND a.role IN ('image','video','cover') AND f.path IS NOT NULL) AS post_media_files,
            (SELECT count(*) FROM assets a JOIN comments c ON c.id=a.owner_id
             WHERE c.post_id=p.id) AS comment_assets,
            (SELECT count(*) FROM assets a JOIN comments c ON c.id=a.owner_id
             JOIN files f ON f.id=a.file_id WHERE c.post_id=p.id AND f.path IS NOT NULL) AS comment_asset_files
            ,(SELECT count(DISTINCT a.id) FROM assets a
              JOIN relationships rel ON rel.to_id=a.owner_id AND rel.kind='authored_by'
              JOIN comments c ON c.id=rel.from_id WHERE c.post_id=p.id AND a.role='avatar') AS comment_avatar_records
            ,(SELECT count(DISTINCT a.id) FROM assets a
              JOIN relationships rel ON rel.to_id=a.owner_id AND rel.kind='authored_by'
              JOIN comments c ON c.id=rel.from_id JOIN files f ON f.id=a.file_id
              WHERE c.post_id=p.id AND a.role='avatar' AND f.path IS NOT NULL) AS comment_avatar_files
            FROM posts p ORDER BY p.ordinal,p.id"""))
        details = []
        totals = Counter()
        for row in rows:
            saved = json.loads(row["coverage"] or "{}")
            queued = queue_posts.get(row["source_id"], {})
            qcoverage = queued.get("coverage") or {}
            qcomments = qcoverage.get("comments") or {}
            qmedia = qcoverage.get("media") or {}
            item = {"source_id": row["source_id"], "post_id": row["id"],
                    "title": row["title"], "mirror_status": saved.get("status", "unknown"),
                    "queue_status": queued.get("status", "absent"),
                    "comments_captured": row["comments"],
                    "queue_comments_expected": qcomments.get("expected"),
                    "queue_comment_status": qcomments.get("status", "unknown"),
                    "missing_comment_parents": row["missing_parents"],
                    "post_media_records": row["post_media"],
                    "post_media_files": row["post_media_files"],
                    "queue_post_media_expected": qmedia.get("expected"),
                    "comment_asset_records": row["comment_assets"],
                    "comment_asset_files": row["comment_asset_files"],
                    "comment_avatar_records": row["comment_avatar_records"],
                    "comment_avatar_files": row["comment_avatar_files"],
                    "post_uncaptured": not bool(row["captured_at"])}
            item["discussion_asset_records"] = row["comment_assets"] + row["comment_avatar_records"]
            item["discussion_asset_files"] = row["comment_asset_files"] + row["comment_avatar_files"]
            item["comments_need_review"] = (qcomments.get("status") != "complete" or
                                             item["post_uncaptured"] or
                                             row["missing_parents"] > 0 or
                                             (isinstance(qcomments.get("expected"), int) and
                                              row["comments"] < qcomments["expected"]))
            item["post_media_missing"] = row["post_media_files"] < row["post_media"]
            item["discussion_media_missing"] = item["discussion_asset_files"] < item["discussion_asset_records"]
            details.append(item)
            for key in ("comments_captured", "missing_comment_parents", "post_media_records",
                        "post_media_files", "comment_asset_records", "comment_asset_files",
                        "comment_avatar_records", "comment_avatar_files",
                        "discussion_asset_records", "discussion_asset_files"):
                totals[key] += item[key]
            for key in ("comments_need_review", "post_media_missing", "discussion_media_missing", "post_uncaptured"):
                totals[key] += bool(item[key])
        unqueued = [item["source_id"] for item in details if item["queue_status"] == "absent"]
        return {"schema": "red-engine.capture-reconciliation/1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "sources": {"mirror": str(MIRROR_DB), "checkpoint": str(QUEUE)},
                "summary": {"posts": len(details), "queue_posts": len(queue_posts),
                            "mirror_posts_without_queue": len(unqueued), **dict(totals)},
                "posts": details,
                "note": "Missing remote discussion media is a coverage gap, not a corrupt local file. "
                        "Avatar totals sum per-post memberships, so a shared author avatar can be counted on multiple posts. "
                        "This report did not start capture or download."}
    finally:
        db.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run(), ensure_ascii=False, indent=2))
