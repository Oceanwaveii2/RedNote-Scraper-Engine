"""Read-only timing and peak-memory probe for the captured study path."""

from __future__ import annotations

import json
import time
import tracemalloc
import gc
import tempfile
import uuid
from pathlib import Path

from study_core import StudyEngine, canonical_hash
from study_storage import SqliteArchive, SqliteStudies


def run() -> dict:
    archive = SqliteArchive()
    with archive.connect() as db:
        profile_ids = [row[0] for row in db.execute("SELECT id FROM profiles ORDER BY id")]
        counts = {name: db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                  for name in ("profiles", "posts", "comments", "assets")}
    sample = profile_ids[:1]
    with tempfile.TemporaryDirectory(prefix="red-study-legacy-") as temp:
        tracemalloc.start()
        started = time.perf_counter()
        records, scope = archive.capture(sample, None)
        capture_seconds = time.perf_counter() - started
        _, capture_peak = tracemalloc.get_traced_memory()
        members = [{"kind": row["kind"], "id": row["id"],
                    "post_id": row.get("post_id"), "ordinal": row.get("ordinal"),
                    "content_hash": canonical_hash(row),
                    "source_revision": row.get("source_revision")}
                   for row in records]
        legacy_manifest = canonical_hash({"members": members, "scope": scope})
        record_count = len(records)
        legacy_study = {"id": "study-" + uuid.uuid4().hex, "title": "Legacy probe",
                        "question": "Compare study construction", "preset_id": None,
                        "profile_ids": sample, "post_ids": None, "created_at": "probe",
                        "counts": {kind: sum(row["kind"] == kind for row in records)
                                   for kind in ("profile", "post", "comment", "asset")},
                        "coverage": scope, "manifest_hash": legacy_manifest}
        SqliteStudies(Path(temp) / "studies.sqlite").create(
            legacy_study, members, records, archive.generation())
        legacy_full_seconds = time.perf_counter() - started
        _, legacy_full_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        del records, members
    gc.collect()
    with tempfile.TemporaryDirectory(prefix="red-study-stream-") as temp:
        tracemalloc.start()
        started = time.perf_counter()
        study = StudyEngine(archive, SqliteStudies(Path(temp) / "studies.sqlite")).create({
            "title": "Streaming probe", "question": "Compare study construction",
            "profile_ids": sample})
        stream_seconds = time.perf_counter() - started
        _, stream_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if study["manifest_hash"] != legacy_manifest or study["coverage"] != scope:
            changed = {key: [scope.get(key), study["coverage"].get(key)]
                       for key in scope.keys() | study["coverage"].keys()
                       if scope.get(key) != study["coverage"].get(key)}
            raise AssertionError("Streaming capture differs: " +
                                 json.dumps({"scope": changed,
                                             "manifest_equal": study["manifest_hash"] == legacy_manifest},
                                            ensure_ascii=False))
    return {"archive": str(archive.path), "counts": counts,
            "sample_profiles": sample, "capture_record_count": record_count,
            "capture_seconds": round(capture_seconds, 3),
            "capture_peak_python_bytes": capture_peak,
            "legacy_full_seconds": round(legacy_full_seconds, 3),
            "legacy_full_peak_python_bytes": legacy_full_peak,
            "stream_seconds": round(stream_seconds, 3),
            "stream_peak_python_bytes": stream_peak,
            "stream_manifest_matches_original": True,
            "scope": {key: value for key, value in scope.items()
                      if key.endswith("_count") or key == "unresolved_comment_parents"},
            "note": "Read-only single-run baseline; Python allocations exclude SQLite native memory."}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
