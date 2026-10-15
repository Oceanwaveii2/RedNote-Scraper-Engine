"""Loopback API for a future UI adapter; provider work stays in the worker process."""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from documents import StoryDocuments
from execution import ScatterExecution
from graph import ScatterGraphStore
from story_pipeline import StoryPipeline
from ui_bindings import BoardBindings
from gemini_recovery import GeminiRecovery
from authored_sources import AuthoredSources
from telemetry import emit
from paths import DATA

PORT = 5189
ALLOWED_ORIGINS = {"http://127.0.0.1:5186", "http://127.0.0.1:5185"}


class ScatterAPI(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # The default HTTP log includes query strings and source identifiers.
        pass

    def _services(self):
        graph = ScatterGraphStore()
        execution = ScatterExecution(graph)
        return graph, execution, StoryPipeline(execution), StoryDocuments(execution)

    def _headers(self, status=200, kind="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()

    def _result(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(status)
        self.wfile.write(body)
        emit("api.request", method=self.command, route=urlsplit(self.path).path,
             status=status, response_bytes=len(body),
             elapsed_ms=round((time.monotonic() - getattr(self, "_started", time.monotonic())) * 1000, 2))

    def _guard(self, mutation=False):
        if self.headers.get("Host") != f"127.0.0.1:{PORT}":
            raise PermissionError("Loopback host required")
        if mutation and self.headers.get("Origin") not in ALLOWED_ORIGINS:
            raise PermissionError("Known UI origin required")

    def do_OPTIONS(self):
        try:
            self._guard(True)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
        except PermissionError as exc:
            self._result({"error": str(exc)}, 403)

    def do_GET(self):
        self._started = time.monotonic()
        try:
            self._guard()
            route = urlsplit(self.path)
            if route.path == "/api/scatter/v1/health":
                return self._result({"service": "scatter-graph", "schema": "scatter.graph/1",
                                     "build": os.environ.get("SCATTER_BUILD_ID", "dev"),
                                     "graph_db_present": (DATA / "scatter.sqlite").is_file(),
                                     "runs_db_present": (DATA / "scatter-runs.sqlite").is_file()})
            graph, execution, pipeline, documents = self._services()
            query = parse_qs(route.query)
            def arg(name):
                return query.get(name, [None])[0]
            if route.path == "/api/scatter/v1/graph":
                value = graph.read(arg("project_id"), int(arg("revision")) if arg("revision") else None)
            elif route.path == "/api/scatter/v1/branches":
                value = execution.branches(arg("project_id"))
            elif route.path == "/api/scatter/v1/source-catalog":
                value = graph._adapter().catalog(arg("study_id"), arg("kind"),
                                                 int(arg("cursor") or 0),
                                                 int(arg("limit") or 50))
            elif route.path == "/api/scatter/v1/custom-sources":
                value = AuthoredSources().list(arg("kind"), int(arg("cursor") or 0),
                                               int(arg("limit") or 50))
            elif route.path == "/api/scatter/v1/custom-source":
                value = AuthoredSources().read(arg("record_id"),
                                               int(arg("revision")) if arg("revision") else None)
            elif route.path == "/api/scatter/v1/custom-pin":
                value = AuthoredSources().pin(arg("record_id"),
                                              int(arg("revision")) if arg("revision") else None)
            elif route.path == "/api/scatter/v1/run":
                value = execution.read_run(arg("run_id"))
            elif route.path == "/api/scatter/v1/runs":
                value = execution.list_runs(arg("project_id"))
            elif route.path == "/api/scatter/v1/events":
                value = pipeline.events(arg("run_id"), int(arg("after") or 0))
            elif route.path == "/api/scatter/v1/artifact":
                value = pipeline.artifact(arg("run_id"), arg("stage"))
            elif route.path == "/api/scatter/v1/input-summary":
                value = pipeline.input_summary(arg("run_id"))
            elif route.path == "/api/scatter/v1/document":
                value = documents.read(arg("document_id"))
            elif route.path == "/api/scatter/v1/bindings":
                value = BoardBindings(graph).list(arg("project_id"))
            elif route.path == "/api/scatter/v1/interrupted":
                value = pipeline.interrupted()
            elif route.path == "/api/scatter/v1/gemini-status":
                value = GeminiRecovery(pipeline).inspect(arg("run_id"))
            elif route.path == "/api/scatter/v1/stream":
                run_id = arg("run_id")
                after = int(arg("after") or 0)
                pipeline.execution.read_run(run_id)
                self._headers(200, "text/event-stream; charset=utf-8")
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    events = pipeline.events(run_id, after)
                    for event in events:
                        after = event["sequence"]
                        self.wfile.write(("id: " + str(after) + "\n" +
                                          "event: progress\n" +
                                          "data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(1)
                emit("api.stream", route=route.path, status=200,
                     elapsed_ms=round((time.monotonic() - self._started) * 1000, 2))
                return
            else:
                return self._result({"error": "route not found"}, 404)
            self._result(value)
        except (ValueError, LookupError, PermissionError) as exc:
            self._result({"error": str(exc)}, 403 if isinstance(exc, PermissionError) else 400)

    def do_POST(self):
        self._started = time.monotonic()
        try:
            self._guard(True)
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise ValueError("JSON content type required")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_000_000:
                raise ValueError("Request must be 1 byte to 2 MB")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            graph, execution, pipeline, documents = self._services()
            route = urlsplit(self.path).path
            if route == "/api/scatter/v1/project":
                value = graph.create(body["title"], body.get("project_id"))
            elif route == "/api/scatter/v1/custom-source":
                value = AuthoredSources().create(body["kind"], body["title"],
                                                 body["content"], body.get("record_id"))
            elif route == "/api/scatter/v1/custom-revision":
                value = AuthoredSources().revise(body["record_id"],
                                                 body["expected_revision"],
                                                 body["command_id"], body["content"])
            elif route == "/api/scatter/v1/graph":
                value = graph.apply(body["project_id"], body["expected_revision"],
                                    body["command_id"], body["command"])
            elif route == "/api/scatter/v1/queue-output":
                value = pipeline.queue_output(body["project_id"], body["output_id"])
            elif route == "/api/scatter/v1/queue-branch":
                value = pipeline.queue_branch(body["project_id"], body["branch_id"])
            elif route == "/api/scatter/v1/select":
                value = pipeline.select(body["run_id"], body["candidate_id"],
                                        body["expected_revision"])
            elif route == "/api/scatter/v1/resolve-plan":
                value = pipeline.resolve_plan(body["run_id"], body["expected_revision"],
                                              body["command_id"], body["plan"])
            elif route == "/api/scatter/v1/cancel-queued":
                value = pipeline.cancel_queued(body["run_id"])
            elif route == "/api/scatter/v1/accept-draft":
                value = documents.create_from_run(body["run_id"], body["expected_graph_revision"])
            elif route == "/api/scatter/v1/document":
                value = documents.apply(body["document_id"], body["expected_revision"],
                                        body["command_id"], body["command"])
            elif route == "/api/scatter/v1/binding":
                value = BoardBindings(graph).set(body["project_id"], body["ui_node_id"],
                                                 body.get("semantic_node_id"), body["expected_graph_revision"])
            elif route == "/api/scatter/v1/edge-plan":
                value = BoardBindings(graph).plan_edges(
                    body["project_id"], body["expected_graph_revision"],
                    body["visual_edges"], body.get("board_id", "prototype-5186"))
            elif route == "/api/scatter/v1/edge-apply":
                value = BoardBindings(graph).apply_edges(
                    body["project_id"], body["expected_graph_revision"],
                    body["expected_bindings_hash"], body["command_id"],
                    body["visual_edges"], body.get("board_id", "prototype-5186"))
            else:
                return self._result({"error": "route not found"}, 404)
            self._result(value)
        except (KeyError, ValueError, LookupError, PermissionError) as exc:
            self._result({"error": str(exc)}, 403 if isinstance(exc, PermissionError) else 400)


def main():
    emit("api.start", bind="127.0.0.1", port=PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), ScatterAPI).serve_forever()


if __name__ == "__main__":
    main()
