"""Headless authored graph. No board coordinates, UI widgets, or mirror writes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from paths import DATA


DB_PATH = DATA / "scatter.sqlite"
NODE_KINDS = {"actor", "transformation", "source", "music", "music_processor",
              "junction", "story", "group", "output", "prose_engine", "llm_renderer"}
PORT_TYPES = {"actor", "reference", "music", "story", "control"}
COMPATIBLE = {"actor": {"actor"}, "reference": {"reference"},
              "music": {"music"}, "story": {"story"}, "control": {"control"}}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8")).hexdigest()


def _id(value, label):
    if not isinstance(value, str) or not 1 <= len(value) <= 120 or any(c.isspace() for c in value):
        raise ValueError(f"Invalid {label}")
    return value


class ScatterGraphStore:
    @staticmethod
    def story_output_command(output_id: str, title: str = "Story output",
                             from_node: str | None = None,
                             from_port: str = "story_out") -> dict:
        """Build one revisioned Prose -> renderer -> Output chain.

        Provider switches are saved configuration, never permission to dispatch.
        Runtime credentials and session IDs are deliberately absent from graph data.
        """
        output_id = _id(output_id, "output ID")
        if not isinstance(title, str) or not 1 <= len(title) <= 160:
            raise ValueError("Story output title is required")
        prose_id, llm_id = output_id + ".prose", output_id + ".llm"
        commands = []
        for node_id, kind, label, ports, data in (
            (prose_id, "prose_engine", "Prose Engine",
             {"in": {"direction": "in", "type": "story"},
              "out": {"direction": "out", "type": "story"}},
             {"engine_status": "not_installed", "session_role": "story_conditioning"}),
            (llm_id, "llm_renderer", "Story LLM renderer",
             {"in": {"direction": "in", "type": "story"},
              "out": {"direction": "out", "type": "story"}},
             {"session_role": "story_render", "separate_from": "data_analysis",
              "providers": {"gpt": True, "gemini": False, "grok": False},
              "active_provider": "gpt", "dispatch_status": "not_connected"}),
            (output_id, "output", title,
             {"in": {"direction": "in", "type": "story"}},
             {"output_role": "story_document"}),
        ):
            commands.append({"action": "add_node", "node": {"id": node_id, "kind": kind,
                             "title": label, "ports": ports, "data": data}})
        def edge(source, source_port, target, edge_id):
            return {"action": "add_edge", "edge": {"id": edge_id,
                    "from_node": source, "from_port": source_port,
                    "to_node": target, "to_port": "in"}}
        if from_node is not None:
            commands.append(edge(_id(from_node, "source node"), _id(from_port, "source port"),
                                 prose_id, output_id + ".source"))
        commands.extend((edge(prose_id, "out", llm_id, output_id + ".conditioned"),
                         edge(llm_id, "out", output_id, output_id + ".rendered")))
        return {"action": "batch", "commands": commands}

    def __init__(self, path: Path = DB_PATH, source_adapter=None):
        self.path = Path(path)
        self.source_adapter = source_adapter
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, head INTEGER NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    project_id TEXT NOT NULL REFERENCES projects(id), number INTEGER NOT NULL,
                    parent INTEGER, created_at TEXT NOT NULL, command_id TEXT NOT NULL,
                    command_hash TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, PRIMARY KEY(project_id,number),
                    UNIQUE(project_id,command_id));
                CREATE INDEX IF NOT EXISTS revisions_recent ON revisions(project_id,number DESC);
                CREATE TRIGGER IF NOT EXISTS revisions_no_update BEFORE UPDATE ON revisions
                    BEGIN SELECT RAISE(ABORT,'Graph revisions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS revisions_no_delete BEFORE DELETE ON revisions
                    BEGIN SELECT RAISE(ABORT,'Graph revisions are immutable'); END;
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=15000")
            yield db
            db.commit()
        finally:
            db.close()

    def create(self, title: str, project_id: str | None = None):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
            raise ValueError("Project title must contain 1–160 characters")
        project_id = _id(project_id or "scatter-" + uuid.uuid4().hex, "project ID")
        snapshot = {"schema": "scatter.graph/1", "nodes": {}, "edges": {}}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO projects VALUES (?,?,0,?)", (project_id, title.strip(), _now()))
            db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?)",
                       (project_id, 0, None, _now(), "initial", _digest({}),
                        _digest(snapshot), json.dumps(snapshot, ensure_ascii=False, sort_keys=True)))
            db.commit()
        return {"project_id": project_id, "revision": 0, "snapshot": snapshot}

    def read(self, project_id: str, revision: int | None = None):
        _id(project_id, "project ID")
        with self._connect() as db:
            project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise LookupError("Graph project not found")
            number = project["head"] if revision is None else revision
            row = db.execute("SELECT * FROM revisions WHERE project_id=? AND number=?",
                             (project_id, number)).fetchone()
            if not row:
                raise LookupError("Graph revision not found")
            snapshot = json.loads(row["snapshot_json"])
            if _digest(snapshot) != row["snapshot_hash"]:
                raise RuntimeError("Graph revision checksum mismatch")
            return {"project_id": project_id, "title": project["title"], "revision": number,
                    "head": project["head"], "snapshot_hash": row["snapshot_hash"],
                    "snapshot": snapshot}

    def apply(self, project_id: str, expected_revision: int, command_id: str, command: dict):
        _id(project_id, "project ID")
        _id(command_id, "command ID")
        if type(expected_revision) is not int or expected_revision < 0 or not isinstance(command, dict):
            raise ValueError("Expected revision and command are required")
        command_hash = _digest(command)
        operations = command.get("commands") if command.get("action") == "batch" else [command]
        if not isinstance(operations, list) or not operations or len(operations) > 200:
            raise ValueError("Batch requires 1–200 graph commands")
        if any(not isinstance(item, dict) or (command.get("action") == "batch"
               and item.get("action") in {"batch", "restore_revision"})
               for item in operations):
            raise ValueError("Invalid nested graph command")
        sources = []
        for item in operations:
            source = (item.get("node", {}).get("source") if item.get("action") == "add_node"
                      and isinstance(item.get("node"), dict) else
                      item.get("source") if item.get("action") == "set_node_source" else None)
            if source is not None:
                sources.append(source)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT number,command_hash,snapshot_hash FROM revisions "
                               "WHERE project_id=? AND command_id=?", (project_id, command_id)).fetchone()
            if prior:
                if prior["command_hash"] != command_hash:
                    raise ValueError("Command ID was reused with different content")
                return {"project_id": project_id, "revision": prior["number"],
                        "snapshot_hash": prior["snapshot_hash"], "replayed": True}
            project = db.execute("SELECT head FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise LookupError("Graph project not found")
            if project["head"] != expected_revision:
                raise ValueError(f"Revision conflict: current head is {project['head']}")
            for source in sources:
                self._require_current_source(source)
            row = db.execute("SELECT snapshot_json FROM revisions WHERE project_id=? AND number=?",
                             (project_id, expected_revision)).fetchone()
            snapshot = json.loads(row[0])
            if command.get("action") == "restore_revision":
                target = command.get("target_revision")
                if type(target) is not int or not 0 <= target <= expected_revision:
                    raise ValueError("Restore target must be an existing revision")
                old = db.execute("SELECT snapshot_json FROM revisions WHERE project_id=? AND number=?",
                                 (project_id, target)).fetchone()
                if not old:
                    raise LookupError("Restore target revision not found")
                snapshot = json.loads(old[0])
            else:
                self._edit(snapshot, command)
            self._validate(snapshot)
            number = expected_revision + 1
            snapshot_hash = _digest(snapshot)
            db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?)",
                       (project_id, number, expected_revision, _now(), command_id,
                        command_hash, snapshot_hash, json.dumps(snapshot, ensure_ascii=False, sort_keys=True)))
            db.execute("UPDATE projects SET head=? WHERE id=? AND head=?",
                       (number, project_id, expected_revision))
            db.commit()
            return {"project_id": project_id, "revision": number,
                    "snapshot_hash": snapshot_hash, "replayed": False}

    def _adapter(self):
        if self.source_adapter is None:
            try:
                from .source_adapter import ScatterSourceAdapter
            except ImportError:
                from source_adapter import ScatterSourceAdapter
            self.source_adapter = ScatterSourceAdapter()
        return self.source_adapter

    def _require_current_source(self, source):
        result = self._adapter().resolve(source)
        if result["status"] != "pinned":
            raise ValueError(f"Source reference is {result['status']}; no graph change was saved")

    def source_status(self, project_id: str, revision: int | None = None) -> dict:
        graph = self.read(project_id, revision)
        refs = {node_id: node["source"] for node_id, node in graph["snapshot"]["nodes"].items()
                if node.get("source") is not None}
        return {"project_id": project_id, "revision": graph["revision"],
                "sources": {node_id: self._adapter().resolve(ref) for node_id, ref in refs.items()}}

    def inspect(self, project_id: str, revision: int | None = None) -> dict:
        """Report authoring completeness without rejecting intermediate edits."""
        graph = self.read(project_id, revision)
        nodes, edges = graph["snapshot"]["nodes"], graph["snapshot"]["edges"]
        issues = []
        actor_links = {}
        for node_id, node in nodes.items():
            if node["kind"] != "transformation":
                continue
            data = node.get("data") or {}
            start, end = data.get("from_actor_id"), data.get("to_actor_id")
            if not (start in nodes and nodes[start]["kind"] == "actor"
                    and end in nodes and nodes[end]["kind"] == "actor" and start != end):
                issues.append({"node_id": node_id, "code": "transformation_actors_missing"})
                continue
            incoming = any(edge["from_node"] == start and edge["to_node"] == node_id
                           and nodes[start]["ports"][edge["from_port"]]["type"] == "actor"
                           for edge in edges.values())
            outgoing = any(edge["from_node"] == node_id and edge["to_node"] == end
                           and nodes[end]["ports"][edge["to_port"]]["type"] == "actor"
                           for edge in edges.values())
            if not incoming or not outgoing:
                issues.append({"node_id": node_id, "code": "transformation_path_incomplete"})
            declared_parent = nodes[end].get("data", {}).get("derived_from")
            if declared_parent is not None and declared_parent != start:
                issues.append({"node_id": end, "code": "derived_actor_parent_mismatch"})
            actor_links.setdefault(start, set()).add(end)
        visited, active = set(), set()
        def visit(actor_id):
            if actor_id in active:
                return True
            if actor_id in visited:
                return False
            active.add(actor_id)
            loop = any(visit(child) for child in actor_links.get(actor_id, ()))
            active.remove(actor_id)
            visited.add(actor_id)
            return loop
        if any(visit(actor_id) for actor_id in actor_links):
            issues.append({"code": "actor_transformation_cycle"})
        sources = self.source_status(project_id, revision)["sources"]
        for node_id, status in sources.items():
            if status["status"] != "pinned":
                issues.append({"node_id": node_id, "code": "source_" + status["status"]})
        return {"project_id": project_id, "revision": graph["revision"],
                "node_count": len(nodes), "edge_count": len(edges),
                "status": "structurally_complete" if not issues else "needs_attention",
                "issues": issues, "sources": sources}

    @staticmethod
    def _edit(graph, command):
        action = command.get("action")
        nodes, edges = graph["nodes"], graph["edges"]
        if action == "batch":
            for item in command["commands"]:
                ScatterGraphStore._edit(graph, item)
        elif action == "add_node":
            node = command.get("node")
            if not isinstance(node, dict):
                raise ValueError("Node is required")
            node_id = _id(node.get("id"), "node ID")
            if node_id in nodes or node.get("kind") not in NODE_KINDS:
                raise ValueError("Duplicate node or unsupported node kind")
            title = node.get("title")
            if not isinstance(title, str) or not 1 <= len(title) <= 160:
                raise ValueError("Node title is required")
            ports = node.get("ports", {})
            if not isinstance(ports, dict) or len(ports) > 40:
                raise ValueError("Invalid node ports")
            for port_id, port in ports.items():
                _id(port_id, "port ID")
                if not isinstance(port, dict) or port.get("direction") not in {"in", "out"} or port.get("type") not in PORT_TYPES:
                    raise ValueError("Invalid typed port")
            source = node.get("source")
            if source is not None and (not isinstance(source, dict) or
                                       not all(isinstance(source.get(key), str) and source[key]
                                               for key in ("archive_id", "entity_id", "content_hash"))):
                raise ValueError("Source reference needs archive ID, entity ID, and content hash")
            nodes[node_id] = {"id": node_id, "kind": node["kind"], "title": title,
                              "ports": ports, "source": source,
                              "data": node.get("data", {})}
        elif action == "remove_node":
            node_id = _id(command.get("node_id"), "node ID")
            if node_id not in nodes:
                raise LookupError("Node not found")
            del nodes[node_id]
            for edge_id in list(edges):
                if node_id in {edges[edge_id]["from_node"], edges[edge_id]["to_node"]}:
                    del edges[edge_id]
        elif action == "update_node_data":
            node_id = _id(command.get("node_id"), "node ID")
            if node_id not in nodes or not isinstance(command.get("data"), dict):
                raise ValueError("Existing node and object data required")
            nodes[node_id]["data"] = command["data"]
        elif action == "set_node_source":
            node_id = _id(command.get("node_id"), "node ID")
            if node_id not in nodes:
                raise LookupError("Node not found")
            source = command.get("source")
            if source is not None and not isinstance(source, dict):
                raise ValueError("Source must be a pinned reference or null")
            nodes[node_id]["source"] = source
        elif action == "set_node_ports":
            node_id = _id(command.get("node_id"), "node ID")
            ports = command.get("ports")
            if node_id not in nodes or not isinstance(ports, dict) or len(ports) > 40:
                raise ValueError("Existing node and valid ports required")
            for port_id, port in ports.items():
                _id(port_id, "port ID")
                if not isinstance(port, dict) or port.get("direction") not in {"in", "out"} or port.get("type") not in PORT_TYPES:
                    raise ValueError("Invalid typed port")
            nodes[node_id]["ports"] = ports
        elif action == "add_edge":
            edge = command.get("edge")
            if not isinstance(edge, dict):
                raise ValueError("Edge is required")
            edge_id = _id(edge.get("id"), "edge ID")
            if edge_id in edges:
                raise ValueError("Duplicate edge")
            edges[edge_id] = {key: _id(edge.get(key), key) for key in
                              ("from_node", "from_port", "to_node", "to_port")}
            edges[edge_id]["id"] = edge_id
        elif action == "remove_edge":
            edge_id = _id(command.get("edge_id"), "edge ID")
            if edge_id not in edges:
                raise LookupError("Edge not found")
            del edges[edge_id]
        else:
            raise ValueError("Unsupported graph command")

    @staticmethod
    def _validate(graph):
        nodes = graph["nodes"]
        if len(nodes) > 10000 or len(graph["edges"]) > 30000:
            raise ValueError("Graph exceeds this store's current transaction size")
        for node in nodes.values():
            if node["kind"] != "llm_renderer":
                continue
            data = node.get("data") or {}
            providers = data.get("providers")
            if (not isinstance(providers, dict) or set(providers) != {"gpt", "gemini", "grok"}
                    or any(type(enabled) is not bool for enabled in providers.values())):
                raise ValueError("LLM renderer requires GPT, Gemini and Grok on/off switches")
            active = data.get("active_provider")
            if active is not None and (active not in providers or not providers[active]):
                raise ValueError("Active renderer provider must be enabled")
            if data.get("session_role") != "story_render" or data.get("separate_from") != "data_analysis":
                raise ValueError("Story rendering must use a separate session role")
        seen_inputs = set()
        seen_connections = set()
        for edge in graph["edges"].values():
            a, b = nodes.get(edge["from_node"]), nodes.get(edge["to_node"])
            if not a or not b:
                raise ValueError("Edge endpoint node is missing")
            output, target = a["ports"].get(edge["from_port"]), b["ports"].get(edge["to_port"])
            if not output or not target or output["direction"] != "out" or target["direction"] != "in":
                raise ValueError("Edge must connect an output port to an input port")
            if target["type"] not in COMPATIBLE[output["type"]]:
                raise ValueError("Incompatible typed ports")
            connection = (edge["from_node"], edge["from_port"],
                          edge["to_node"], edge["to_port"])
            if connection in seen_connections:
                raise ValueError("Duplicate typed connection")
            seen_connections.add(connection)
            socket = (edge["to_node"], edge["to_port"])
            if socket in seen_inputs and not target.get("multiple", False):
                raise ValueError("Input port accepts only one connection")
            seen_inputs.add(socket)
