"""Offline regression checks for the packaged Scatter graph service.

The suite uses fresh SQLite files and never starts a provider worker.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engines" / "scatter_graph"))

from execution import ScatterExecution  # noqa: E402
from fixtures import build_rainee_chain  # noqa: E402
from graph import ScatterGraphStore  # noqa: E402
from story_pipeline import StoryPipeline  # noqa: E402
from ui_bindings import BoardBindings  # noqa: E402


def node(node_id: str, kind: str, ports: dict, data: dict | None = None) -> dict:
    return {"id": node_id, "kind": kind, "title": node_id,
            "ports": ports, "data": data or {}}


class ScatterEngineV3Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="scatter-v3-unit-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.graph = ScatterGraphStore(root / "graph.sqlite")
        self.execution = ScatterExecution(self.graph, root / "runs.sqlite")
        self.bindings = BoardBindings(self.graph, root / "ui.sqlite")

    def test_revision_replay_conflict_and_restore(self):
        project_id = self.graph.create("Revision check")["project_id"]
        command = {"action": "add_node", "node": node(
            "original", "actor", {"out": {"direction": "out", "type": "actor"}},
            {"role": "original_protagonist"})}
        first = self.graph.apply(project_id, 0, "add-original", command)
        self.assertEqual(first["revision"], 1)
        replay = self.graph.apply(project_id, 0, "add-original", command)
        self.assertTrue(replay["replayed"])
        with self.assertRaisesRegex(ValueError, "Revision conflict"):
            self.graph.apply(project_id, 0, "stale", command)
        restored = self.graph.apply(project_id, 1, "restore-0",
                                    {"action": "restore_revision", "target_revision": 0})
        self.assertEqual(restored["revision"], 2)
        self.assertEqual(self.graph.read(project_id, 1)["snapshot"]["nodes"].keys(), {"original"})
        self.assertEqual(self.graph.read(project_id)["snapshot"]["nodes"], {})

    def test_batch_is_atomic_when_edge_types_conflict(self):
        project_id = self.graph.create("Typed edge check")["project_id"]
        batch = {"action": "batch", "commands": [
            {"action": "add_node", "node": node(
                "music", "music", {"out": {"direction": "out", "type": "music"}})},
            {"action": "add_node", "node": node(
                "actor", "actor", {"in": {"direction": "in", "type": "actor"}})},
            {"action": "add_edge", "edge": {"id": "wrong", "from_node": "music",
                                              "from_port": "out", "to_node": "actor", "to_port": "in"}},
        ]}
        with self.assertRaisesRegex(ValueError, "Incompatible typed ports"):
            self.graph.apply(project_id, 0, "bad-batch", batch)
        self.assertEqual(self.graph.read(project_id)["revision"], 0)
        self.assertEqual(self.graph.read(project_id)["snapshot"]["nodes"], {})

    def test_fixture_and_story_output_shape(self):
        fixture = build_rainee_chain(self.graph)
        self.assertEqual((fixture["node_count"], fixture["edge_count"]), (5, 4))
        self.assertEqual(fixture["status"], "structurally_complete")
        chain = ScatterGraphStore.story_output_command("story-a")
        project_id = self.graph.create("Story chain")["project_id"]
        self.graph.apply(project_id, 0, "add-story-chain", chain)
        snapshot = self.graph.read(project_id)["snapshot"]
        self.assertEqual(len(snapshot["nodes"]), 3)
        self.assertEqual(len(snapshot["edges"]), 2)
        renderer = snapshot["nodes"]["story-a.llm"]["data"]
        self.assertEqual(renderer["session_role"], "story_render")
        self.assertEqual(renderer["separate_from"], "data_analysis")
        self.assertEqual(set(renderer["providers"]), {"gpt", "gemini", "grok"})

    def test_bindings_plan_and_apply_reviewed_typed_edge(self):
        project_id = self.graph.create("Board bindings")["project_id"]
        batch = {"action": "batch", "commands": [
            {"action": "add_node", "node": node(
                "a", "actor", {"out": {"direction": "out", "type": "actor"}})},
            {"action": "add_node", "node": node(
                "b", "actor", {"in": {"direction": "in", "type": "actor"}})},
        ]}
        self.graph.apply(project_id, 0, "add-pair", batch)
        self.bindings.set(project_id, "card-a", "a", 1)
        self.bindings.set(project_id, "card-b", "b", 1)
        wires = [{"ui_edge_id": "wire-1", "from_ui_node_id": "card-a",
                  "to_ui_node_id": "card-b", "from_port": "out", "to_port": "in"}]
        plan = self.bindings.plan_edges(project_id, 1, wires)
        self.assertEqual(len(plan["add"]), 1)
        result = self.bindings.apply_edges(project_id, 1, plan["bindings_hash"],
                                           "apply-wire", wires)
        self.assertEqual(result["revision"], 2)
        self.assertEqual(len(self.graph.read(project_id)["snapshot"]["edges"]), 1)
        with self.assertRaisesRegex(ValueError, "Graph revision changed"):
            self.bindings.apply_edges(project_id, 1, plan["bindings_hash"], "stale-wire", wires)

    def test_story_queue_summary_and_cancel_without_provider(self):
        project_id = self.graph.create("Offline story")["project_id"]
        batch = {"action": "batch", "commands": [
            {"action": "add_node", "node": node(
                "original", "actor", {"out": {"direction": "out", "type": "actor"}},
                {"role": "original_protagonist"})},
            {"action": "add_node", "node": node(
                "output", "output", {"in": {"direction": "in", "type": "actor"}},
                {"branch_id": "core", "provider": "codex"})},
            {"action": "add_edge", "edge": {"id": "e", "from_node": "original",
                                              "from_port": "out", "to_node": "output", "to_port": "in"}},
        ]}
        self.graph.apply(project_id, 0, "add-story", batch)
        pipeline = StoryPipeline(self.execution)
        with patch("story_pipeline.emit"):
            run = pipeline.queue_output(project_id, "output")
        summary = pipeline.input_summary(run["run_id"])
        self.assertEqual(summary["graph_nodes"], 2)
        self.assertEqual(summary["graph_edges"], 1)
        self.assertEqual(summary["selected_post_count"], 0)
        cancelled = pipeline.cancel_queued(run["run_id"])
        self.assertEqual(cancelled["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
