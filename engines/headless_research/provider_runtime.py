"""Lease heartbeat and cooperative cancellation for one provider attempt."""

from __future__ import annotations

import os
import threading

from study_core import utc_now
from study_storage import SqliteStudies


class JobMonitor:
    def __init__(self, studies: SqliteStudies, study_id: str, job_id: str, cancel_callback):
        self.studies = studies
        self.study_id = study_id
        self.job_id = job_id
        self.cancel_callback = cancel_callback
        self.owner_pid = os.getpid()
        self.stop_event = threading.Event()
        self.cancel_seen = threading.Event()
        self.thread = threading.Thread(target=self._watch, name="red-job-" + job_id[-8:], daemon=True)

    def start(self):
        self.thread.start()
        return self

    def _watch(self):
        while not self.stop_event.wait(1):
            try:
                if not self.studies.heartbeat_job(self.job_id, self.owner_pid, utc_now()):
                    return
                current = self.studies.job(self.study_id, self.job_id)
                if current and current["state"] == "cancel_requested":
                    self.cancel_seen.set()
                    try:
                        self.cancel_callback()
                    except Exception:
                        pass
                    return
            except Exception:
                # A transient SQLite lock should not claim the provider stopped.
                continue

    def cancelled(self) -> bool:
        if self.cancel_seen.is_set():
            return True
        current = self.studies.job(self.study_id, self.job_id)
        return bool(current and current["state"] == "cancel_requested")

    def close(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        self.studies.release_job(self.job_id, self.owner_pid)
