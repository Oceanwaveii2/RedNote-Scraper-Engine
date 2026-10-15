"""Headless Scatter output selection and immutable provider input packets.

The graph chooses scope. A provider renders prose only after explicit dispatch.
No board coordinates, mirror writes, or implicit provider fallback live here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from paths import DATA

from graph import ScatterGraphStore


RUN_DB = DATA / "scatter-runs.sqlite"
PROVIDERS = {"codex", "gemini"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _utc():
    return datetime.now(timezone.utc).isoformat()


class ScatterExecution:
    def __init__(self, graph: ScatterGraphStore | None = None, path: Path = RUN_DB):
        self.graph = graph or ScatterGraphStore()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    snapshot_hash TEXT NOT NULL, output_id TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT, state TEXT NOT NULL,
                    packet_hash TEXT NOT NULL, packet_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    response_text TEXT, provider_receipt_json TEXT, error TEXT);
                CREATE INDEX IF NOT EXISTS runs_project ON runs(project_id,created_at DESC);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=15000")
            yield db
            db.commit()
        finally:
            db.close()

    def compile_output(self, project_id: str, output_id: str, revision: int | None = None,
                       include_sources: bool = True) -> dict:
        graph = self.graph.read(project_id, revision)
        nodes, edges = graph["snapshot"]["nodes"], graph["snapshot"]["edges"]
        output = nodes.get(output_id)
        if not output or output["kind"] != "output":
            raise ValueError("Select an existing output node")
        provider = output.get("data", {}).get("provider")
        if provider not in PROVIDERS:
            raise ValueError("Output must select codex or gemini")
        branch_id = output.get("data", {}).get("branch_id")
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise ValueError("Output must name its branch_id")
        incoming = {}
        for edge in edges.values():
            incoming.setdefault(edge["to_node"], []).append(edge)
        state, ordered, selected_edges = {}, [], []

        def visit(node_id):
            if state.get(node_id) == "visiting":
                raise ValueError("Output ancestry contains a cycle")
            if state.get(node_id) == "done":
                return
            state[node_id] = "visiting"
            for edge in sorted(incoming.get(node_id, []), key=lambda e: e["id"]):
                visit(edge["from_node"])
                selected_edges.append(edge)
            state[node_id] = "done"
            ordered.append(node_id)

        visit(output_id)
        scoped = set(ordered)
        roots = [node_id for node_id in ordered if nodes[node_id]["kind"] == "actor"
                 and nodes[node_id].get("data", {}).get("role") == "original_protagonist"]
        if len(roots) != 1:
            raise ValueError("Output path must contain one original protagonist")
        for node_id in ordered:
            member_branch = nodes[node_id].get("data", {}).get("branch_id")
            if member_branch and member_branch != branch_id:
                raise ValueError(f"Node {node_id} belongs to another branch")
        groups = [node_id for node_id in ordered if nodes[node_id]["kind"] == "group"]
        for group_id in groups:
            members = [edge["from_node"] for edge in incoming.get(group_id, [])
                       if edge["from_node"] in scoped and nodes[edge["from_node"]]["kind"] == "source"]
            if not members:
                raise ValueError(f"Group {group_id} has no connected source profiles")
            for member in members:
                ref = nodes[member].get("source")
                if not ref or ref.get("entity_kind") != "profile":
                    raise ValueError(f"Group member {member} must pin a captured profile")
        source_payloads = {}
        selected_posts = {}
        selected_comments = {}
        for node_id in ordered:
            node = nodes[node_id]
            source = node.get("source")
            if source is None:
                continue
            status = self.graph._adapter().resolve(source)
            if status["status"] != "pinned":
                raise ValueError(f"Source {node_id} is {status['status']}; refresh its pin before dispatch")
            if include_sources:
                source_payloads[node_id] = self.graph._adapter().read_pinned(source)
            if source.get("entity_kind") == "profile":
                post_refs = (node.get("data") or {}).get("selected_post_refs", [])
                if not isinstance(post_refs, list) or len(post_refs) > 100:
                    raise ValueError(f"Profile {node_id} needs at most 100 selected post references")
                selected_posts[node_id] = []
                selected_post_ids = set()
                for post_ref in post_refs:
                    if not isinstance(post_ref, dict) or post_ref.get("entity_kind") != "post":
                        raise ValueError(f"Profile {node_id} has an invalid selected post pin")
                    if post_ref.get("study_id") != source.get("study_id"):
                        raise ValueError("Selected post and profile must share a study")
                    post = self.graph._adapter().read_pinned(post_ref)
                    if post["content"].get("profile_id") != source["entity_id"]:
                        raise ValueError("Selected post does not belong to its profile")
                    if post["id"] in selected_post_ids:
                        raise ValueError("Selected post is duplicated")
                    selected_post_ids.add(post["id"])
                    if include_sources:
                        selected_posts[node_id].append(post)
                comment_refs = (node.get("data") or {}).get("selected_comment_refs", [])
                if not isinstance(comment_refs, list) or len(comment_refs) > 200:
                    raise ValueError(f"Profile {node_id} needs at most 200 selected comment references")
                selected_comments[node_id] = []
                selected_comment_ids = set()
                for comment_ref in comment_refs:
                    if not isinstance(comment_ref, dict) or comment_ref.get("entity_kind") != "comment":
                        raise ValueError(f"Profile {node_id} has an invalid selected comment pin")
                    if comment_ref.get("study_id") != source.get("study_id"):
                        raise ValueError("Selected comment and profile must share a study")
                    comment = self.graph._adapter().read_pinned(comment_ref)
                    if comment["content"].get("post_id") not in selected_post_ids:
                        raise ValueError("Selected comment must belong to a selected post")
                    if comment["id"] in selected_comment_ids:
                        raise ValueError("Selected comment is duplicated")
                    selected_comment_ids.add(comment["id"])
                    if include_sources:
                        selected_comments[node_id].append(comment)
        music_controls = []
        for node_id in ordered:
            node = nodes[node_id]
            if node["kind"] != "music_processor":
                continue
            data = node.get("data") or {}
            mode = data.get("mode")
            if mode not in {"expression", "interpretation", "in_story"}:
                raise ValueError(f"Music processor {node_id} needs an explicit mode")
            influence = data.get("influence")
            if isinstance(influence, bool) or not isinstance(influence, (int, float)) or not 0 <= influence <= 1:
                raise ValueError(f"Music processor {node_id} needs influence from 0 to 1")
            time_range = data.get("time_range_ms")
            if (not isinstance(time_range, list) or len(time_range) != 2 or
                    any(type(t) is not int or t < 0 for t in time_range) or time_range[1] <= time_range[0]):
                raise ValueError(f"Music processor {node_id} needs a valid millisecond time range")
            feeds = [edge["from_node"] for edge in incoming.get(node_id, [])
                     if nodes[edge["from_node"]]["kind"] == "music"]
            if len(feeds) != 1:
                raise ValueError(f"Music processor {node_id} needs exactly one connected music node")
            music_controls.append({"processor_node_id": node_id, "music_node_id": feeds[0],
                                   "mode": mode, "influence": influence,
                                   "time_range_ms": time_range,
                                   "notes": data.get("notes", "")})
        packet = {
            "schema": "scatter.output-packet/1", "project_id": project_id,
            "revision": graph["revision"], "snapshot_hash": graph["snapshot_hash"],
            "output_id": output_id, "provider": provider,
            "branch_id": branch_id, "main_actor_id": roots[0],
            "model": output.get("data", {}).get("model"),
            "ordered_node_ids": ordered,
            "nodes": {node_id: nodes[node_id] for node_id in ordered},
            "edges": sorted(selected_edges, key=lambda e: e["id"]),
            "groups": {group_id: [edge["from_node"] for edge in incoming.get(group_id, [])
                                  if edge["from_node"] in scoped and nodes[edge["from_node"]]["kind"] == "source"]
                       for group_id in groups},
            "source_records": source_payloads,
            "selected_posts": selected_posts,
            "selected_comments": selected_comments,
            "music_controls": music_controls,
        }
        return packet

    def branches(self, project_id: str, revision: int | None = None) -> dict:
        graph = self.graph.read(project_id, revision)
        grouped = {}
        for node_id, node in graph["snapshot"]["nodes"].items():
            if node["kind"] == "output":
                branch_id = node.get("data", {}).get("branch_id")
                if branch_id:
                    grouped.setdefault(branch_id, []).append({
                        "output_id": node_id, "title": node["title"],
                        "provider": node.get("data", {}).get("provider"),
                        "model": node.get("data", {}).get("model")})
        return {"project_id": project_id, "revision": graph["revision"],
                "branches": {key: sorted(value, key=lambda item: item["output_id"])
                             for key, value in sorted(grouped.items())}}

    def compile_branch(self, project_id: str, branch_id: str, revision: int | None = None,
                       include_sources: bool = True) -> list[dict]:
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise ValueError("Branch ID is required")
        graph = self.graph.read(project_id, revision)
        outputs = sorted(node_id for node_id, node in graph["snapshot"]["nodes"].items()
                         if node["kind"] == "output" and node.get("data", {}).get("branch_id") == branch_id)
        if not outputs:
            raise ValueError("Branch has no outputs")
        return [self.compile_output(project_id, node_id, graph["revision"], include_sources)
                for node_id in outputs]

    def queue_output(self, project_id: str, output_id: str, revision: int | None = None) -> dict:
        packet = self.compile_output(project_id, output_id, revision)
        encoded = _json(packet)
        if len(encoded.encode("utf-8")) > 2_000_000:
            raise ValueError("Output packet exceeds 2 MB; split source selections")
        run_id, now = "scatter-run-" + uuid.uuid4().hex, _utc()
        with self._db() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (run_id, project_id, packet["revision"], packet["snapshot_hash"],
                        output_id, packet["provider"], packet["model"], "queued",
                        _hash(packet), encoded, now, now, None, None, None))
            db.commit()
        return {"run_id": run_id, "state": "queued", "packet_hash": _hash(packet),
                "project_id": project_id, "revision": packet["revision"], "output_id": output_id}

    def queue_branch(self, project_id: str, branch_id: str, revision: int | None = None) -> list[dict]:
        packets = self.compile_branch(project_id, branch_id, revision)
        prepared = []
        for packet in packets:
            encoded = _json(packet)
            if len(encoded.encode("utf-8")) > 2_000_000:
                raise ValueError(f"Output {packet['output_id']} exceeds 2 MB; split source selections")
            prepared.append(("scatter-run-" + uuid.uuid4().hex, packet, encoded, _hash(packet)))
        now = _utc()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for run_id, packet, encoded, digest in prepared:
                db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (run_id, project_id, packet["revision"], packet["snapshot_hash"],
                            packet["output_id"], packet["provider"], packet["model"], "queued",
                            digest, encoded, now, now, None, None, None))
        return [{"run_id": run_id, "state": "queued", "packet_hash": digest,
                 "project_id": project_id, "revision": packet["revision"],
                 "output_id": packet["output_id"]} for run_id, packet, _, digest in prepared]

    def read_run(self, run_id: str, include_packet: bool = False) -> dict:
        with self._db() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise LookupError("Scatter run not found")
        result = {key: row[key] for key in row.keys() if key != "packet_json"}
        if include_packet:
            packet = json.loads(row["packet_json"])
            if _hash(packet) != row["packet_hash"]:
                raise RuntimeError("Scatter run packet checksum mismatch")
            result["packet"] = packet
        return result

    def list_runs(self, project_id: str, limit: int = 30) -> list[dict]:
        if not isinstance(project_id, str) or not project_id or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Project ID and bounded limit are required")
        with self._db() as db:
            rows = db.execute("SELECT id,project_id,revision,output_id,provider,model,state,"
                              "created_at,updated_at,error FROM runs WHERE project_id=? "
                              "ORDER BY created_at DESC,id DESC LIMIT ?", (project_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def claim(self, run_id: str) -> dict:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE runs SET state='running',updated_at=? WHERE id=? AND state='queued'",
                                 (_utc(), run_id)).rowcount
            db.commit()
        if not changed:
            raise ValueError("Only a queued run can be dispatched")
        return self.read_run(run_id, True)

    def finish(self, run_id: str, text: str, receipt: dict):
        if not isinstance(text, str) or not isinstance(receipt, dict):
            raise ValueError("Provider text and receipt are required")
        with self._db() as db:
            changed = db.execute("UPDATE runs SET state='proposed',updated_at=?,response_text=?,provider_receipt_json=? "
                                 "WHERE id=? AND state='running'",
                                 (_utc(), text, _json(receipt), run_id)).rowcount
            db.commit()
        if not changed:
            raise ValueError("Run is not running")
        return self.read_run(run_id)

    def fail(self, run_id: str, reason: str):
        with self._db() as db:
            db.execute("UPDATE runs SET state='failed',updated_at=?,error=? WHERE id=? AND state='running'",
                       (_utc(), reason[:240], run_id))
            db.commit()
        return self.read_run(run_id)

    def cancel_queued(self, run_id: str) -> dict:
        """Cancel before external dispatch; running provider calls need separate control."""
        with self._db() as db:
            changed = db.execute("UPDATE runs SET state='cancelled',updated_at=? "
                                 "WHERE id=? AND state='queued'", (_utc(), run_id)).rowcount
        if not changed:
            raise ValueError("Only a queued run can be cancelled here")
        return self.read_run(run_id)
