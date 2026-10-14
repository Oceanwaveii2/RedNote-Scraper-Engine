"""Headless authoring fixture for the Rainee transformation chain."""

from __future__ import annotations


def build_rainee_chain(store):
    created = store.create("Rainee alternative form chain")
    project_id = created["project_id"]
    revision = 0
    nodes = [
        {"id": "rainee-01", "kind": "actor", "title": "Rainee 01",
         "ports": {"actor_out": {"direction": "out", "type": "actor"}},
         "data": {"role": "original_protagonist", "identity": "rainee.base"}},
        {"id": "big-lip-transformation", "kind": "transformation", "title": "Big Lip transformation",
         "ports": {"actor_in": {"direction": "in", "type": "actor"},
                   "actor_out": {"direction": "out", "type": "actor"}},
         "data": {"from_actor_id": "rainee-01", "to_actor_id": "big-lip-variant",
                  "overrides": {"appearance": "pouty lips", "personality": "new personality"}}},
        {"id": "big-lip-variant", "kind": "actor", "title": "Big Lip Variant",
         "ports": {"actor_in": {"direction": "in", "type": "actor"},
                   "actor_out": {"direction": "out", "type": "actor"}},
         "data": {"role": "derived_protagonist", "identity": "rainee.big_lip",
                  "derived_from": "rainee-01"}},
        {"id": "horse-transformation", "kind": "transformation", "title": "Horse transformation",
         "ports": {"actor_in": {"direction": "in", "type": "actor"},
                   "actor_out": {"direction": "out", "type": "actor"}},
         "data": {"from_actor_id": "big-lip-variant", "to_actor_id": "horse-rainee",
                  "overrides": {"form": "horse variant"}}},
        {"id": "horse-rainee", "kind": "actor", "title": "Horse Rainee",
         "ports": {"actor_in": {"direction": "in", "type": "actor"}},
         "data": {"role": "derived_protagonist", "identity": "rainee.horse",
                  "derived_from": "big-lip-variant"}},
    ]
    for node in nodes:
        result = store.apply(project_id, revision, "seed-node-" + node["id"],
                             {"action": "add_node", "node": node})
        revision = result["revision"]
    for index, (start, end) in enumerate(zip(nodes, nodes[1:]), 1):
        result = store.apply(project_id, revision, f"seed-edge-{index}",
                             {"action": "add_edge", "edge": {"id": f"chain-{index}",
                              "from_node": start["id"], "from_port": "actor_out",
                              "to_node": end["id"], "to_port": "actor_in"}})
        revision = result["revision"]
    return store.inspect(project_id)
