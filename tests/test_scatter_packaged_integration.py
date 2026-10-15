"""Opt-in loopback integration check using SQLite backup copies.

Set SCATTER_TEST_GRAPH_DATA, REDNOTE_MIRROR_DB, REDNOTE_STUDY_DB, and
REDNOTE_INDEX_DB. No provider worker is started; source databases are read only.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "engines" / "scatter_graph" / "api.py"
BASE = "http://127.0.0.1:5189/api/scatter/v1"
ORIGIN = "http://127.0.0.1:5186"
REQUIRED = ("SCATTER_TEST_GRAPH_DATA", "REDNOTE_MIRROR_DB",
            "REDNOTE_STUDY_DB", "REDNOTE_INDEX_DB")


def request(route: str, body: dict | None = None, origin: str | None = None):
    headers = {"Host": "127.0.0.1:5189"}
    if origin:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    with urlopen(Request(BASE + route, data=payload, headers=headers,
                         method="POST" if body is not None else "GET"), timeout=10) as response:
        return json.load(response)


class PackagedScatterIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [name for name in REQUIRED if not os.environ.get(name)]
        if missing:
            raise unittest.SkipTest("Set " + ", ".join(missing))
        with socket.socket() as listener:
            if listener.connect_ex(("127.0.0.1", 5189)) == 0:
                raise unittest.SkipTest("Port 5189 is occupied; do not stop another service")
        cls.scratch = tempfile.TemporaryDirectory(prefix="scatter-v3-api-")
        cls.data = Path(cls.scratch.name)
        source_dir = Path(os.environ["SCATTER_TEST_GRAPH_DATA"])
        for name in ("scatter.sqlite", "scatter-runs.sqlite", "scatter-ui.sqlite"):
            src = sqlite3.connect(f"file:{(source_dir / name).as_posix()}?mode=ro", uri=True)
            try:
                dst = sqlite3.connect(cls.data / name)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
        db = sqlite3.connect(cls.data / "scatter.sqlite")
        try:
            row = db.execute("SELECT id FROM projects WHERE title LIKE '%source-backed%' LIMIT 1").fetchone()
        finally:
            db.close()
        if not row:
            cls.scratch.cleanup()
            raise unittest.SkipTest("No source-backed project in copied graph store")
        cls.project_id = row[0]
        env = os.environ.copy()
        env["SCATTER_DATA_DIR"] = str(cls.data)
        env["SCATTER_BUILD_ID"] = "integration-test"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        cls.process = subprocess.Popen([sys.executable, str(API)], cwd=API.parent,
                                       env=env, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, creationflags=flags)
        try:
            for _ in range(80):
                if cls.process.poll() is not None:
                    raise RuntimeError("Packaged API exited before health check")
                try:
                    cls.health = request("/health")
                    break
                except (OSError, URLError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("Packaged API did not become ready")
        except Exception:
            cls.process.terminate()
            cls.process.wait(timeout=5)
            cls.scratch.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "process"):
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)
        if hasattr(cls, "scratch"):
            cls.scratch.cleanup()

    def test_health_graph_and_source_backed_output(self):
        self.assertEqual(self.health["schema"], "scatter.graph/1")
        self.assertEqual(self.health["build"], "integration-test")
        graph = request("/graph?project_id=" + self.project_id)
        self.assertEqual(graph["project_id"], self.project_id)
        self.assertTrue(graph["snapshot"]["nodes"])
        branches = request("/branches?project_id=" + self.project_id)["branches"]
        self.assertTrue(branches)
        output = next(item["output_id"] for items in branches.values() for item in items)
        queued = request("/queue-output", {"project_id": self.project_id,
                                            "output_id": output}, ORIGIN)
        summary = request("/input-summary?run_id=" + queued["run_id"])
        self.assertEqual(summary["graph_revision"], graph["revision"])
        self.assertGreater(summary["frozen_packet_bytes_utf8"], 0)
        cancelled = request("/cancel-queued", {"run_id": queued["run_id"]}, ORIGIN)
        self.assertEqual(cancelled["state"], "cancelled")
        events = [json.loads(line) for line in
                  (self.data / "telemetry" / "scatter-events.jsonl").read_text().splitlines()]
        self.assertTrue(any(event["event"] == "story.queued" for event in events))
        self.assertTrue(all("prompt" not in event and "source_text" not in event
                            for event in events))

    def test_unknown_origin_cannot_mutate(self):
        with self.assertRaises(HTTPError) as failure:
            request("/project", {"title": "should fail"}, "http://127.0.0.1:9999")
        self.assertEqual(failure.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
