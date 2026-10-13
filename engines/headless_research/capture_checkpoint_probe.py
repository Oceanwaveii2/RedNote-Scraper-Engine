"""Measure queue counter and checkpoint costs without touching the live queue."""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path


QUEUE = Path(__file__).resolve().parent.parent / "Red Note Gemini" / "clone_queue.json"


def _counters(posts):
    return {
        "discoveredPosts": len(posts),
        "capturedPosts": sum(p.get("status") in {"complete", "partial"} for p in posts),
        "completePosts": sum(p.get("status") == "complete" for p in posts),
        "failedPosts": sum(p.get("status") in {"failed", "needs_attention"} for p in posts),
        "savedMedia": sum(sum(m.get("status") == "saved" and m.get("kind") in {"photo", "video"}
                              for m in p.get("media", [])) for p in posts),
        "comments": sum(p.get("commentCount", 0) for p in posts),
        "bytes": sum(sum(m.get("bytes", 0) for m in p.get("media", [])
                         if m.get("status") == "saved") for p in posts),
        "retainedPosts": sum(bool(p.get("commentCount") or any(m.get("status") == "saved"
                                  for m in p.get("media", []))) for p in posts),
        "contentCompletePosts": sum(bool(p.get("contentComplete")) for p in posts),
        "partialCommentTrees": sum(p.get("coverage", {}).get("comments", {}).get("status") == "partial"
                                   for p in posts),
    }


def run(iterations=8):
    snapshot = json.loads(QUEUE.read_text(encoding="utf-8"))
    jobs = snapshot.get("jobs", [])
    if not jobs:
        raise ValueError("No retained queue jobs")
    counter_times, encode_times, write_times = [], [], []
    with tempfile.TemporaryDirectory(prefix="red-checkpoint-probe-") as folder:
        temporary = Path(folder) / "queue.json"
        for _ in range(iterations):
            start = time.perf_counter()
            counters = _counters(jobs[0].get("posts", []))
            counter_times.append(time.perf_counter() - start)
            start = time.perf_counter()
            encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
            encode_times.append(time.perf_counter() - start)
            start = time.perf_counter()
            temporary.write_text(encoded, encoding="utf-8")
            write_times.append(time.perf_counter() - start)
    return {
        "schema": "red-engine.capture-checkpoint-probe/1",
        "queue_path": str(QUEUE), "queue_bytes": QUEUE.stat().st_size,
        "compact_bytes": len(encoded.encode("utf-8")),
        "posts": len(jobs[0].get("posts", [])), "iterations": iterations,
        "counter_median_ms": round(statistics.median(counter_times) * 1000, 3),
        "encode_median_ms": round(statistics.median(encode_times) * 1000, 3),
        "write_median_ms": round(statistics.median(write_times) * 1000, 3),
        "counters": counters,
        "note": "Offline snapshot timing; no live queue changes or site navigation."
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
