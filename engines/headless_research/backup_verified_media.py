"""Copy verified mirror media and a consistent SQLite snapshot to another volume."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Red Note Gemini"
MIRROR = SOURCE_ROOT / "data_v2" / "rednote-v2.sqlite"


def digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def backup(target: Path) -> dict:
    target = target.resolve()
    if target == SOURCE_ROOT.resolve() or SOURCE_ROOT.resolve() in target.parents:
        raise ValueError("Backup must be outside the source directory")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError("Choose an empty backup directory")
    target.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(MIRROR.as_uri() + "?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        rows = [dict(row) for row in source.execute(
            "SELECT id,path,sha256,bytes,mime FROM files WHERE verification='verified' ORDER BY path")]
        database_copy = target / "rednote-v2.sqlite"
        with sqlite3.connect(database_copy) as destination:
            source.backup(destination)
    recorded = []
    for row in rows:
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe mirror media path")
        origin = (SOURCE_ROOT / relative).resolve()
        if SOURCE_ROOT.resolve() not in origin.parents:
            raise ValueError("Media path leaves source directory")
        expected = row["sha256"]
        actual, size = digest(origin)
        if actual != expected or size != row["bytes"]:
            raise RuntimeError(f"Source media changed: {relative}")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".partial")
        shutil.copyfile(origin, partial)
        copied_hash, copied_size = digest(partial)
        if copied_hash != expected or copied_size != size:
            raise RuntimeError(f"Backup media did not verify: {relative}")
        partial.replace(destination)
        recorded.append({"id": row["id"], "path": relative.as_posix(),
                         "sha256": expected, "bytes": size, "mime": row["mime"]})
    db_hash, db_bytes = digest(database_copy)
    manifest = {"schema": "rednote.verified-media-backup/1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_root": str(SOURCE_ROOT), "source_database": str(MIRROR),
                "database": {"path": database_copy.name, "sha256": db_hash, "bytes": db_bytes},
                "file_count": len(recorded), "media_bytes": sum(row["bytes"] for row in recorded),
                "files": recorded,
                "restore_note": "Verify every SHA-256 and byte count before restoring to the relative path under the source root. Stop the native archive owner before replacing its database."}
    manifest_path = target / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"target": str(target), "file_count": manifest["file_count"],
            "media_bytes": manifest["media_bytes"], "database_bytes": db_bytes,
            "manifest_sha256": digest(manifest_path)[0]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    print(json.dumps(backup(parser.parse_args().target), indent=2))
