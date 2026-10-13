"""Isolated broker receipt, cancellation, and restart-recovery check."""

import json
import sqlite3
import tempfile
from pathlib import Path
from contextlib import closing

from research_broker import ResearchSessionStore


def run():
    with tempfile.TemporaryDirectory(prefix="red-broker-") as folder:
        store = ResearchSessionStore(Path(folder) / "sessions.sqlite")
        study = {"id": "study-probe", "manifest_hash": "probe"}
        cancelled = store.create(study, "codex", "probe", "probe question")
        store.finish(cancelled, "running")
        store.record(cancelled, "study_search", {"query": "probe"}, {"items": []})
        request = store.request_cancel(cancelled)
        if not request["requested"] or not store.cancellation_requested(cancelled):
            raise AssertionError("Cancellation request was not durable")
        store.finish(cancelled, "cancelled")
        crashed = store.create(study, "gemini", "flash", "probe question")
        store.finish(crashed, "running")
        with closing(sqlite3.connect(store.path)) as db:
            db.execute("UPDATE sessions SET owner_pid=999999999 WHERE id=?", (crashed,))
            db.commit()
        recovered = store.recover_interrupted()
        if crashed not in recovered["interrupted"] or store.read(crashed)["state"] != "interrupted":
            raise AssertionError("Restart recovery did not detect the lost worker")
        immutable = False
        try:
            with closing(sqlite3.connect(store.path)) as db:
                db.execute("UPDATE calls SET result_hash='bad' WHERE session_id=?", (cancelled,))
        except sqlite3.IntegrityError:
            immutable = True
        if not immutable:
            raise AssertionError("Research receipt was mutable")
        return {"cancelled": store.read(cancelled)["state"],
                "recovered": store.read(crashed)["state"],
                "receipt_immutable": immutable}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
