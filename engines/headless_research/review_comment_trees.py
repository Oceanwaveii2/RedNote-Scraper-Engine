"""Classify incomplete captured comment trees from saved visibility evidence."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MIRROR = ROOT.parent / "Red Note Gemini" / "data_v2" / "rednote-v2.sqlite"
OUTPUT = ROOT / "data" / "partial-comment-tree-review-2026-10-11.csv"


def review():
    with sqlite3.connect(MIRROR.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT id,source_id,source_url,coverage FROM posts ORDER BY id").fetchall()
        report = []
        for row in rows:
            coverage = json.loads(row["coverage"] or "{}")
            comments = coverage.get("comments") or {}
            if comments.get("status") != "partial" or comments.get("reason") not in {
                    "source_count_gap", "paused", "scroll_budget"}:
                continue
            diagnostics = comments.get("diagnostics") or {}
            reason = comments["reason"]
            hidden = (comments.get("unexpandedReplies") or 0) > 0 or (diagnostics.get("blockedBranches") or 0) > 0
            if hidden:
                category = "hidden_reply_control_reported"
            elif reason == "source_count_gap" and diagnostics.get("atBottom") is True and not comments.get("commentsLoading"):
                category = "counter_gap_after_visible_end"
            elif reason == "scroll_budget":
                category = "scroll_budget_unfinished"
            else:
                category = "paused_visibility_unknown"
            members = db.execute("SELECT source_id,parent_source_id FROM comments WHERE post_id=?", (row["id"],)).fetchall()
            source_ids = {item["source_id"] for item in members}
            missing_parents = sum(bool(item["parent_source_id"]) and item["parent_source_id"] not in source_ids
                                  for item in members)
            captured, expected = comments.get("captured") or 0, comments.get("expected")
            report.append({"post_id": row["id"], "source_post_id": row["source_id"],
                           "source_url": row["source_url"], "reason": reason,
                           "category": category, "captured": captured,
                           "reported_count": expected, "count_gap": max(0, expected - captured) if isinstance(expected, int) else "",
                           "at_bottom": diagnostics.get("atBottom"),
                           "unexpanded_replies": comments.get("unexpandedReplies"),
                           "blocked_branches": diagnostics.get("blockedBranches"),
                           "missing_parent_references": missing_parents,
                           "note": "A count gap does not prove a recoverable hidden reply or a correct website counter."})
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(report[0]))
        writer.writeheader()
        writer.writerows(report)
    return {"output": str(OUTPUT), "posts": len(report),
            "categories": dict(Counter(item["category"] for item in report)),
            "reported_gap": sum(item["count_gap"] for item in report if isinstance(item["count_gap"], int)),
            "missing_parent_references": sum(item["missing_parent_references"] for item in report)}


if __name__ == "__main__":
    print(json.dumps(review(), indent=2))
