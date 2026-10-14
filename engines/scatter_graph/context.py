"""Deterministic preflight for authored Scatter instructions and context size."""

from __future__ import annotations

from execution import _json

OPERATIONS = {"replace_actor", "add_actor", "set_context", "require_event",
              "forbid_event", "set_form"}
SCOPES = {"card", "branch", "story"}


def compile_context(packet: dict, byte_budget: int = 96_000) -> dict:
    """Keep mandatory authored inputs intact; fail instead of silent eviction."""
    if type(byte_budget) is not int or not 8_000 <= byte_budget <= 2_000_000:
        raise ValueError("Context byte budget must be 8 KB–2 MB")
    operations = []
    effective = {}
    for node_id in packet["ordered_node_ids"]:
        node = packet["nodes"][node_id]
        authored = (node.get("data") or {}).get("operations", [])
        if not isinstance(authored, list):
            raise ValueError(f"Node {node_id} operations must be a list")
        for item in authored:
            if not isinstance(item, dict) or item.get("operation") not in OPERATIONS:
                raise ValueError(f"Node {node_id} has an unsupported authored operation")
            if item.get("scope") not in SCOPES or not isinstance(item.get("target"), str) or not item["target"]:
                raise ValueError(f"Node {node_id} operation needs scope and target")
            if not isinstance(item.get("payload"), dict):
                raise ValueError(f"Node {node_id} operation payload must be an object")
            key = (item["scope"], item["target"], item["operation"])
            prior = effective.get(key)
            if prior and prior["payload"] != item["payload"]:
                raise ValueError(f"CONFLICTING_OVERRIDES: {prior['node_id']} and {node_id}")
            entry = {"node_id": node_id, "operation": item["operation"],
                     "scope": item["scope"], "target": item["target"],
                     "payload": item["payload"], "locked": item.get("locked") is True,
                     "author_revision": item.get("author_revision")}
            effective[key] = entry
            operations.append(entry)
    output = packet["nodes"][packet["output_id"]]
    context = {"schema": "scatter.context/1", "packet": packet,
               "authored_operations": operations,
               "music_controls": packet.get("music_controls", []),
               "cultural_regime": (output.get("data") or {}).get("cultural_regime", "unspecified"),
               "required_final_form": (output.get("data") or {}).get("required_final_form")}
    size = len(_json(context).encode("utf-8"))
    if size > byte_budget:
        raise ValueError(f"CONTEXT_BUDGET_EXCEEDED: mandatory context is {size} bytes, budget is {byte_budget}")
    context["context_bytes"] = size
    return context


def check_symbolic_plan(context: dict, plan: dict) -> dict:
    """Check declared state transitions without imposing a cultural realism rule."""
    packet = context["packet"]
    actor = packet["nodes"][packet["main_actor_id"]]
    state = dict((actor.get("data") or {}).get("initial_story_state") or {})
    issues = []
    for event in plan["events"]:
        for key, required in event["preconditions"].items():
            if key not in state or state[key] != required:
                issues.append({"code": "PRECONDITION_MISMATCH", "event_id": event["id"],
                               "state_key": key, "required": required, "actual": state.get(key)})
        state.update(event["effects"])
    endpoint = context.get("required_final_form")
    if endpoint is not None and state.get("form") != endpoint:
        issues.append({"code": "ENDPOINT_FORM_MISMATCH", "required": endpoint,
                       "actual": state.get("form")})
    required_events = [operation for operation in context["authored_operations"]
                       if operation["operation"] == "require_event"]
    forbidden_events = [operation for operation in context["authored_operations"]
                        if operation["operation"] == "forbid_event"]
    event_ids = {event["id"] for event in plan["events"]}
    for operation in required_events:
        if operation["target"] not in event_ids:
            issues.append({"code": "REQUIRED_EVENT_MISSING", "target": operation["target"]})
    for operation in forbidden_events:
        if operation["target"] in event_ids:
            issues.append({"code": "FORBIDDEN_EVENT_PRESENT", "target": operation["target"]})
    return {"status": "valid" if not issues else "needs_resolution",
            "issues": issues, "final_state": state,
            "cultural_regime": context["cultural_regime"]}
