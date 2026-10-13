"""Build a reviewable, read-only backfill plan from capture reconciliation."""

from __future__ import annotations

import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPORT = HERE / "data" / "capture-reconciliation-2026-10-11.json"


def build(report: dict, source_report: Path = REPORT) -> dict:
    tasks = []
    for post in report["posts"]:
        reasons = []
        if post["missing_comment_parents"]:
            reasons.append("missing_comment_parents")
        if post["comments_need_review"]:
            reasons.append("partial_comment_tree")
        if post.get("post_uncaptured"):
            reasons.append("uncaptured_post_stub")
        if post["post_media_missing"]:
            reasons.append("post_media_missing")
        if post["discussion_media_missing"]:
            reasons.append("discussion_media_unarchived")
        if not reasons:
            continue
        tasks.append({
            "source_id": post["source_id"], "post_id": post["post_id"],
            "priority": 0 if "missing_comment_parents" in reasons else
                        1 if "partial_comment_tree" in reasons else
                        2 if "post_media_missing" in reasons else 3,
            "reasons": reasons,
            "comments_captured": post["comments_captured"],
            "queue_comments_expected": post["queue_comments_expected"],
            "comment_asset_records": post["comment_asset_records"],
            "comment_asset_files": post["comment_asset_files"],
            "comment_avatar_records": post.get("comment_avatar_records", 0),
            "comment_avatar_files": post.get("comment_avatar_files", 0),
        })
    tasks.sort(key=lambda row: (row["priority"], -row["comments_captured"], row["source_id"]))
    return {
        "schema": "red-engine.capture-repair-plan/1",
        "source_report": str(source_report),
        "source_created_at": report["created_at"],
        "task_count": len(tasks),
        "tasks": tasks,
        "handling": "Review these captured post IDs before explicit, paced backfill in the app-owned browser. "
                    "This file does not resume capture, assume missing remote media is downloadable, "
                    "or change the mirror.",
    }


if __name__ == "__main__":
    result = build(json.loads(REPORT.read_text(encoding="utf-8")))
    target = HERE / "data" / "capture-repair-plan-2026-10-11.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(target), "task_count": result["task_count"],
                      "priority_zero": sum(row["priority"] == 0 for row in result["tasks"]),
                      "priority_one": sum(row["priority"] == 1 for row in result["tasks"])}, indent=2))
