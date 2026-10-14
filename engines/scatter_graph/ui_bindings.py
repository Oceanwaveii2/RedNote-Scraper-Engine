"""Explicit links between an unfinished board and a headless semantic graph.

UI IDs and layout are never imported as evidence or graph authority.
"""

from __future__ import annotations

import sqlite3
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

from paths import DATA

from graph import ScatterGraphStore, _id, _now


UI_DB = DATA / "scatter-ui.sqlite"


class BoardBindings:
    def __init__(self, graph: ScatterGraphStore | None = None, path: Path = UI_DB):
        self.graph = graph or ScatterGraphStore()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS board_bindings (
                project_id TEXT NOT NULL, board_id TEXT NOT NULL, ui_node_id TEXT NOT NULL,
                semantic_node_id TEXT NOT NULL, graph_revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(project_id,board_id,ui_node_id),
                UNIQUE(project_id,board_id,semantic_node_id))""")

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

    def list(self, project_id: str, board_id: str = "prototype-5186") -> dict:
        _id(project_id, "project ID")
        _id(board_id, "board ID")
        graph = self.graph.read(project_id)
        with self._db() as db:
            rows = db.execute("SELECT * FROM board_bindings WHERE project_id=? AND board_id=? "
                              "ORDER BY ui_node_id", (project_id, board_id)).fetchall()
        return {"project_id": project_id, "board_id": board_id,
                "graph_revision": graph["revision"],
                "bindings": [{**dict(row), "semantic_node_exists": row["semantic_node_id"] in graph["snapshot"]["nodes"]}
                             for row in rows]}

    def set(self, project_id: str, ui_node_id: str, semantic_node_id: str | None,
            expected_graph_revision: int, board_id: str = "prototype-5186") -> dict:
        _id(project_id, "project ID")
        _id(board_id, "board ID")
        _id(ui_node_id, "UI node ID")
        if type(expected_graph_revision) is not int:
            raise ValueError("Expected graph revision is required")
        graph = self.graph.read(project_id)
        if graph["revision"] != expected_graph_revision:
            raise ValueError(f"Graph revision changed to {graph['revision']}")
        if semantic_node_id is not None:
            _id(semantic_node_id, "semantic node ID")
            if semantic_node_id not in graph["snapshot"]["nodes"]:
                raise ValueError("Semantic node does not exist in the selected graph")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT semantic_node_id FROM board_bindings "
                               "WHERE project_id=? AND board_id=? AND ui_node_id=?",
                               (project_id, board_id, ui_node_id)).fetchone()
            if prior and prior["semantic_node_id"] != semantic_node_id:
                prefix = "board-" + hashlib.sha256(board_id.encode("utf-8")).hexdigest()[:12] + "-"
                if any(edge_id.startswith(prefix) and prior["semantic_node_id"] in
                       {edge["from_node"], edge["to_node"]}
                       for edge_id, edge in graph["snapshot"]["edges"].items()):
                    raise ValueError("Remove this card's committed board wires before changing its binding")
            if semantic_node_id is None:
                db.execute("DELETE FROM board_bindings WHERE project_id=? AND board_id=? AND ui_node_id=?",
                           (project_id, board_id, ui_node_id))
            else:
                existing = db.execute("SELECT ui_node_id FROM board_bindings WHERE project_id=? "
                                      "AND board_id=? AND semantic_node_id=?",
                                      (project_id, board_id, semantic_node_id)).fetchone()
                if existing and existing["ui_node_id"] != ui_node_id:
                    raise ValueError("Semantic node is already bound to another UI node")
                db.execute("INSERT INTO board_bindings VALUES (?,?,?,?,?,?) "
                           "ON CONFLICT(project_id,board_id,ui_node_id) DO UPDATE SET "
                           "semantic_node_id=excluded.semantic_node_id,"
                           "graph_revision=excluded.graph_revision,updated_at=excluded.updated_at",
                           (project_id, board_id, ui_node_id, semantic_node_id,
                            expected_graph_revision, _now()))
            db.commit()
        return self.list(project_id, board_id)

    @staticmethod
    def _edge_id(board_id: str, ui_edge_id: str) -> str:
        return ("board-" + hashlib.sha256(board_id.encode("utf-8")).hexdigest()[:12]
                + "-" + hashlib.sha256(ui_edge_id.encode("utf-8")).hexdigest()[:32])

    def plan_edges(self, project_id: str, expected_graph_revision: int,
                   visual_edges: list[dict], board_id: str = "prototype-5186") -> dict:
        """Preview a complete, explicit set of board-owned typed connections.

        Coordinates, labels, and unbound cards never become graph authority.
        The caller must supply stable visual edge IDs and choose both semantic ports.
        """
        _id(project_id, "project ID")
        _id(board_id, "board ID")
        if type(expected_graph_revision) is not int or expected_graph_revision < 0:
            raise ValueError("Expected graph revision is required")
        if not isinstance(visual_edges, list) or len(visual_edges) > 200:
            raise ValueError("Provide at most 200 visual edge mappings per revision")
        graph = self.graph.read(project_id)
        if graph["revision"] != expected_graph_revision:
            raise ValueError(f"Graph revision changed to {graph['revision']}")
        nodes = graph["snapshot"]["nodes"]
        current = graph["snapshot"]["edges"]
        with self._db() as db:
            rows = db.execute("SELECT ui_node_id,semantic_node_id FROM board_bindings "
                              "WHERE project_id=? AND board_id=? ORDER BY ui_node_id",
                              (project_id, board_id)).fetchall()
        bindings = {row["ui_node_id"]: row["semantic_node_id"] for row in rows}
        binding_hash = hashlib.sha256(json.dumps(bindings, sort_keys=True).encode("utf-8")).hexdigest()
        prefix = "board-" + hashlib.sha256(board_id.encode("utf-8")).hexdigest()[:12] + "-"
        owned = {edge_id: edge for edge_id, edge in current.items() if edge_id.startswith(prefix)}
        desired = {}
        seen = set()
        for item in visual_edges:
            if not isinstance(item, dict):
                raise ValueError("Each visual edge mapping must be an object")
            ui_edge_id = _id(item.get("ui_edge_id"), "UI edge ID")
            if ui_edge_id in seen:
                raise ValueError("Duplicate UI edge ID")
            seen.add(ui_edge_id)
            from_ui = _id(item.get("from_ui_node_id"), "source UI node ID")
            to_ui = _id(item.get("to_ui_node_id"), "target UI node ID")
            from_node, to_node = bindings.get(from_ui), bindings.get(to_ui)
            if not from_node or not to_node or from_node not in nodes or to_node not in nodes:
                raise ValueError(f"Both endpoints of {ui_edge_id} need current node bindings")
            edge_id = self._edge_id(board_id, ui_edge_id)
            desired[edge_id] = {"id": edge_id, "from_node": from_node,
                                "from_port": _id(item.get("from_port"), "output port ID"),
                                "to_node": to_node,
                                "to_port": _id(item.get("to_port"), "input port ID")}
            if edge_id in current and edge_id not in owned:
                raise ValueError("Generated edge ID conflicts with an existing edge")
        remove = sorted(edge_id for edge_id, edge in owned.items() if desired.get(edge_id) != edge)
        add = [desired[edge_id] for edge_id in sorted(desired)
               if edge_id not in owned or owned[edge_id] != desired[edge_id]]
        commands = ([{"action": "remove_edge", "edge_id": edge_id} for edge_id in remove]
                    + [{"action": "add_edge", "edge": edge} for edge in add])
        if len(commands) > 200:
            raise ValueError("Edge revision exceeds 200 changes; split the board into smaller revisions")
        # Validate the complete candidate against typed ports and input cardinality.
        candidate = json.loads(json.dumps(graph["snapshot"]))
        if commands:
            self.graph._edit(candidate, {"action": "batch", "commands": commands})
            self.graph._validate(candidate)
        return {"project_id": project_id, "board_id": board_id,
                "graph_revision": graph["revision"], "bindings_hash": binding_hash,
                "visual_edge_count": len(visual_edges), "add": add, "remove": remove,
                "commands": commands, "changed": bool(commands)}

    def apply_edges(self, project_id: str, expected_graph_revision: int,
                    expected_bindings_hash: str, command_id: str,
                    visual_edges: list[dict], board_id: str = "prototype-5186") -> dict:
        _id(command_id, "command ID")
        plan = self.plan_edges(project_id, expected_graph_revision, visual_edges, board_id)
        if plan["bindings_hash"] != expected_bindings_hash:
            raise ValueError("Board node bindings changed; review the edge plan again")
        if not plan["commands"]:
            return {"project_id": project_id, "revision": expected_graph_revision,
                    "changed": False, "bindings_hash": plan["bindings_hash"]}
        result = self.graph.apply(project_id, expected_graph_revision, command_id,
                                  {"action": "batch", "commands": plan["commands"]})
        return {**result, "changed": True, "added": len(plan["add"]),
                "removed": len(plan["remove"])}
