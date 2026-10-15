"""Durable, headless Scatter interpretation -> plan -> draft worker.

Each story output is independent. Three short interpretations are proposed for
that output; a human selects one before its causal plan and draft are rendered.
Provider work is never retried automatically after an ambiguous interruption.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid

from execution import ScatterExecution, _hash, _json, _utc
from providers import RULES, STAGE_RULES, _prompt, generate_stage
from context import compile_context, check_symbolic_plan
from telemetry import emit


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class StoryPipeline:
    def __init__(self, execution: ScatterExecution | None = None):
        self.execution = execution or ScatterExecution()
        with self.execution._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS story_jobs (
                    run_id TEXT PRIMARY KEY, mode TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS story_artifacts (
                    run_id TEXT NOT NULL, stage TEXT NOT NULL, content_hash TEXT NOT NULL,
                    content_json TEXT NOT NULL, receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(run_id,stage));
                CREATE TABLE IF NOT EXISTS story_lineage (
                    child_run_id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS story_lineage_one_selection
                    ON story_lineage(parent_run_id);
                CREATE TABLE IF NOT EXISTS story_events (
                    run_id TEXT NOT NULL, sequence INTEGER NOT NULL, stage TEXT NOT NULL,
                    detail_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id,sequence));
                CREATE INDEX IF NOT EXISTS story_events_recent ON story_events(run_id,sequence);
                CREATE TABLE IF NOT EXISTS story_attempts (
                    run_id TEXT NOT NULL, stage TEXT NOT NULL, attempt_id TEXT NOT NULL,
                    worker_pid INTEGER NOT NULL, state TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT, receipt_json TEXT,
                    PRIMARY KEY(run_id,stage));
                CREATE TABLE IF NOT EXISTS story_plan_resolutions (
                    run_id TEXT PRIMARY KEY, command_id TEXT NOT NULL,
                    plan_hash TEXT NOT NULL, created_at TEXT NOT NULL);
            """)

    def queue_output(self, project_id: str, output_id: str) -> dict:
        packet = self.execution.compile_output(project_id, output_id)
        return self._queue_packets([packet])[0]

    def queue_branch(self, project_id: str, branch_id: str) -> list[dict]:
        packets = self.execution.compile_branch(project_id, branch_id)
        return self._queue_packets(packets)

    def _queue_packets(self, packets: list[dict]) -> list[dict]:
        """Commit frozen runs, worker membership, and queue events together."""
        prepared = []
        for packet in packets:
            # Fail at queue time if the mandatory context cannot fit a provider request.
            compile_context(packet)
            encoded = _json(packet)
            if len(encoded.encode("utf-8")) > 2_000_000:
                raise ValueError(f"Output {packet['output_id']} exceeds 2 MB; split source selections")
            prepared.append(("scatter-run-" + uuid.uuid4().hex, packet, encoded, _hash(packet)))
        now = _utc()
        results = []
        with self.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for run_id, packet, encoded, digest in prepared:
                db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (run_id, packet["project_id"], packet["revision"], packet["snapshot_hash"],
                            packet["output_id"], packet["provider"], packet["model"], "queued",
                            digest, encoded, now, now, None, None, None))
                db.execute("INSERT INTO story_jobs VALUES (?,?)", (run_id, "staged"))
                self._event(db, run_id, "queued",
                            {"branch_id": packet["branch_id"], "output_id": packet["output_id"]})
                results.append({"run_id": run_id, "state": "queued", "packet_hash": digest,
                                "project_id": packet["project_id"], "revision": packet["revision"],
                                "output_id": packet["output_id"]})
        emit("story.queued", count=len(results), packet_bytes=sum(len(item[2].encode("utf-8")) for item in prepared))
        return results

    def input_summary(self, run_id: str) -> dict:
        """Count the frozen evidence and exact interpretation input, not archive coverage."""
        run = self.execution.read_run(run_id, True)
        packet = run["packet"]
        context = compile_context(packet)
        allowed_cards = sorted(node_id for node_id, node in packet["nodes"].items()
                               if node["kind"] in {"source", "group"})
        request = {"provider": run["provider"], "model": run["model"],
                   "stage": "interpretations",
                   "input": {"context": context, "allowed_card_node_ids": allowed_cards}}
        prompt = _prompt(request)
        source_counts = {}
        media_counts = {}
        aid_counts = {}
        selected_posts = [post for posts in packet["selected_posts"].values() for post in posts]
        selected_comments = [comment for comments in packet.get("selected_comments", {}).values()
                             for comment in comments]
        for source in packet["source_records"].values():
            kind = source["kind"]
            source_counts[kind] = source_counts.get(kind, 0) + 1
        for post in selected_posts:
            media = post["content"].get("media") or {}
            status = media.get("status", "unknown")
            media_counts[status] = media_counts.get(status, 0) + 1
            for aid in media.get("reading_aids", []):
                kind = aid.get("kind", "unknown")
                aid_counts[kind] = aid_counts.get(kind, 0) + 1
        for comment in selected_comments:
            for aid in comment["content"].get("reading_aids", []):
                kind = aid.get("kind", "unknown")
                aid_counts[kind] = aid_counts.get(kind, 0) + 1
        return {"run_id": run_id, "project_id": run["project_id"],
                "graph_revision": run["revision"], "packet_hash": run["packet_hash"],
                "graph_nodes": len(packet["nodes"]), "graph_edges": len(packet["edges"]),
                "source_record_counts": source_counts,
                "selected_post_count": len(selected_posts),
                "selected_comment_count": len(selected_comments),
                "selected_post_media_states": media_counts,
                "machine_reading_aid_counts": aid_counts,
                "music_control_count": len(packet.get("music_controls", [])),
                "frozen_packet_bytes_utf8": len(_json(packet).encode("utf-8")),
                "context_bytes_utf8": context["context_bytes"],
                "interpretation_prompt_bytes_utf8": len(prompt.encode("utf-8")),
                "interpretation_rules_characters": len(RULES + " " + STAGE_RULES["interpretations"]),
                "direct_comment_source_records": source_counts.get("comment", 0),
                "selected_post_comment_bodies": len(selected_comments),
                "note": "Coverage counters and media hashes are metadata; they are not comment bodies or pixels."}

    @staticmethod
    def _event(db, run_id: str, stage: str, detail: dict):
        sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM story_events WHERE run_id=?",
                              (run_id,)).fetchone()[0]
        db.execute("INSERT INTO story_events VALUES (?,?,?,?,?)",
                   (run_id, sequence, stage, _json(detail), _utc()))

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        self.execution.read_run(run_id)
        with self.execution._db() as db:
            rows = db.execute("SELECT * FROM story_events WHERE run_id=? AND sequence>? "
                              "ORDER BY sequence", (run_id, after)).fetchall()
        return [{"run_id": row["run_id"], "sequence": row["sequence"],
                 "stage": row["stage"], "detail": json.loads(row["detail_json"]),
                 "created_at": row["created_at"]} for row in rows]

    def artifact(self, run_id: str, stage: str) -> dict:
        with self.execution._db() as db:
            row = db.execute("SELECT * FROM story_artifacts WHERE run_id=? AND stage=?",
                             (run_id, stage)).fetchone()
        if row is None:
            raise LookupError("Story stage artifact not found")
        content = json.loads(row["content_json"])
        if _hash(content) != row["content_hash"]:
            raise RuntimeError("Story artifact checksum mismatch")
        return {"run_id": run_id, "stage": stage, "content": content,
                "receipt": json.loads(row["receipt_json"]), "content_hash": row["content_hash"]}

    def _save_artifact(self, run_id: str, stage: str, content: dict, receipt: dict):
        with self.execution._db() as db:
            db.execute("INSERT INTO story_artifacts VALUES (?,?,?,?,?,?)",
                       (run_id, stage, _hash(content), _json(content), _json(receipt), _utc()))
            self._event(db, run_id, stage + "_saved", {"content_hash": _hash(content)})

    def _claim(self):
        with self.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT r.id,r.state FROM runs r JOIN story_jobs job ON job.run_id=r.id "
                             "WHERE r.state IN ('queued','queued_plan','queued_draft') AND NOT EXISTS ("
                             "SELECT 1 FROM runs active WHERE active.provider=r.provider "
                             "AND active.state IN ('interpreting','planning','writing')) "
                             "ORDER BY r.created_at,r.id LIMIT 1").fetchone()
            if row is None:
                return None
            stage = {"queued": "interpreting", "queued_plan": "planning",
                     "queued_draft": "writing"}[row["state"]]
            db.execute("UPDATE runs SET state=?,updated_at=? WHERE id=? AND state=?",
                       (stage, _utc(), row["id"], row["state"]))
            db.execute("INSERT INTO story_attempts VALUES (?,?,?,?,?,?,?,?)",
                       (row["id"], stage, uuid.uuid4().hex, os.getpid(),
                        "outcome_unknown", _utc(), None, None))
            self._event(db, row["id"], stage, {"provider_attempt": "prepared"})
            return row["id"], stage

    def interrupted(self) -> list[dict]:
        """List potentially dispatched stages; never infer that they are safe to retry."""
        with self.execution._db() as db:
            rows = db.execute("SELECT r.id,r.state,a.worker_pid,a.started_at FROM runs r "
                              "JOIN story_attempts a ON a.run_id=r.id AND a.stage=r.state "
                              "WHERE r.state IN ('interpreting','planning','writing') "
                              "ORDER BY a.started_at").fetchall()
        return [dict(row) for row in rows]

    def cancel_queued(self, run_id: str) -> dict:
        with self.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE runs SET state='cancelled',updated_at=? WHERE id=? "
                                 "AND state IN ('queued','queued_plan','queued_draft')", (_utc(), run_id)).rowcount
            if changed != 1:
                raise ValueError("Only a queued stage can be cancelled")
            self._event(db, run_id, "cancelled", {})
        return self.execution.read_run(run_id)

    def mark_interrupted(self, run_id: str, expected_stage: str) -> dict:
        """Operator recovery after confirming the prior worker is gone."""
        if expected_stage not in {"interpreting", "planning", "writing"}:
            raise ValueError("Expected stage is invalid")
        with self.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT worker_pid FROM story_attempts WHERE run_id=? AND stage=?",
                             (run_id, expected_stage)).fetchone()
            if row is None:
                raise LookupError("Attempt not found")
            if _pid_alive(row["worker_pid"]):
                raise ValueError("Worker process is still alive; stop it before recovery")
            changed = db.execute("UPDATE runs SET state='outcome_unknown',updated_at=? "
                                 "WHERE id=? AND state=?", (_utc(), run_id, expected_stage)).rowcount
            if changed != 1:
                raise ValueError("Run is no longer in the expected stage")
            db.execute("UPDATE story_attempts SET state='outcome_unknown',finished_at=? "
                       "WHERE run_id=? AND stage=?", (_utc(), run_id, expected_stage))
            self._event(db, run_id, "outcome_unknown",
                        {"stage": expected_stage, "worker_pid": row["worker_pid"],
                         "action": "inspect provider receipt before any new dispatch"})
        return self.execution.read_run(run_id)

    def reclassify_timeout(self, run_id: str) -> dict:
        """Correct legacy timeout records without retrying or erasing the prior event."""
        with self.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,error FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["state"] != "failed" or row["error"] != "TimeoutError":
                raise ValueError("Only a failed timeout can be reclassified")
            db.execute("UPDATE runs SET state='outcome_unknown',updated_at=? WHERE id=?",
                       (_utc(), run_id))
            db.execute("UPDATE story_attempts SET state='outcome_unknown' WHERE run_id=? AND state='failed'",
                       (run_id,))
            self._event(db, run_id, "outcome_unknown", {"reason": "provider_timeout_reclassified"})
        return self.execution.read_run(run_id)

    def _dispatch_receipt(self, run_id: str, stage: str, receipt: dict):
        with self.execution._db() as db:
            db.execute("UPDATE story_attempts SET receipt_json=? WHERE run_id=? AND stage=?",
                       (_json(receipt), run_id, stage))
            self._event(db, run_id, "provider_dispatched", receipt)

    def _received_response(self, run_id: str, stage: str, text: str, receipt: dict):
        artifact_stage = {"interpreting": "interpretations_response",
                          "planning": "plan_response", "writing": "draft_response"}[stage]
        content = {"text": text}
        digest, now = _hash(content), _utc()
        with self.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE story_attempts SET state='received',finished_at=?,receipt_json=? "
                                 "WHERE run_id=? AND stage=?",
                                 (now, _json(receipt), run_id, stage)).rowcount
            if changed != 1:
                raise RuntimeError("Provider attempt was not recorded")
            db.execute("INSERT INTO story_artifacts VALUES (?,?,?,?,?,?)",
                       (run_id, artifact_stage, digest, _json(content), _json(receipt), now))
            self._event(db, run_id, artifact_stage + "_saved", {"content_hash": digest})

    @staticmethod
    def _require_text(value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be nonempty text")

    def _check_candidates(self, packet, result):
        values = result.get("candidates")
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError("Interpretation stage requires exactly three candidates")
        required = {node_id for node_id, node in packet["nodes"].items()
                    if node["kind"] in {"source", "group"}}
        seen = set()
        for candidate in values:
            if not isinstance(candidate, dict):
                raise ValueError("Candidate must be an object")
            ident = candidate.get("id")
            self._require_text(ident, "Candidate ID")
            self._require_text(candidate.get("logline"), "Candidate logline")
            self._require_text(candidate.get("causal_bridge"), "Candidate causal bridge")
            if ident in seen:
                raise ValueError("Candidate IDs must be unique")
            seen.add(ident)
            roles = candidate.get("card_roles")
            if not isinstance(roles, list):
                raise ValueError("Candidate card roles are required")
            mapped = set()
            for role in roles:
                if not isinstance(role, dict) or role.get("node_id") not in required:
                    raise ValueError("Candidate cites a node outside its selected sources")
                self._require_text(role.get("role"), "Card role")
                mapped.add(role["node_id"])
            if not required <= mapped:
                raise ValueError("Candidate omitted a selected source or group")

    def _check_plan(self, packet, result):
        events = result.get("events")
        if not isinstance(events, list) or not events:
            raise ValueError("Plan requires an ordered event list")
        allowed = set(packet["ordered_node_ids"])
        seen = set()
        covered = set()
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("Plan event must be an object")
            ident = event.get("id")
            self._require_text(ident, "Event ID")
            if ident in seen:
                raise ValueError("Duplicate event ID")
            if event.get("actor_id") not in allowed or packet["nodes"][event["actor_id"]]["kind"] != "actor":
                raise ValueError("Event actor is outside the selected output")
            for field in ("goal", "action", "predecessor_reason"):
                self._require_text(event.get(field), field)
            if not isinstance(event.get("preconditions"), dict) or not isinstance(event.get("effects"), dict):
                raise ValueError("Event preconditions and effects must be objects")
            if event.get("predecessor_id") is not None and event["predecessor_id"] not in seen:
                raise ValueError("Predecessor must be an earlier event")
            if event.get("bridge_kind") not in {"observed", "authored", "invented"}:
                raise ValueError("Event bridge classification is required")
            sources = event.get("source_node_ids")
            if not isinstance(sources, list) or any(s not in allowed for s in sources):
                raise ValueError("Event references an unselected node")
            covered.update(sources)
            seen.add(ident)
        required = {node_id for node_id, node in packet["nodes"].items()
                    if node["kind"] in {"source", "group"}}
        if not required <= covered:
            raise ValueError("Plan does not account for every selected source and group")

    def _check_draft(self, packet, plan, result):
        blocks = result.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            raise ValueError("Draft requires document blocks")
        allowed_events = {event["id"] for event in plan["events"]}
        allowed_nodes = set(packet["ordered_node_ids"])
        seen, covered = set(), set()
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError("Draft block must be an object")
            ident = block.get("id")
            self._require_text(ident, "Block ID")
            self._require_text(block.get("text"), "Block text")
            if ident in seen:
                raise ValueError("Duplicate block ID")
            events = block.get("event_ids")
            sources = block.get("source_node_ids")
            if not isinstance(events, list) or not events or any(e not in allowed_events for e in events):
                raise ValueError("Block event mapping is invalid")
            if not isinstance(sources, list) or any(s not in allowed_nodes for s in sources):
                raise ValueError("Block source mapping is invalid")
            covered.update(events)
            seen.add(ident)
        if covered != allowed_events:
            raise ValueError("Draft omitted planned events")

    def process_next(self) -> dict:
        claimed = self._claim()
        if claimed is None:
            return {"state": "idle"}
        run_id, stage = claimed
        started = time.monotonic()
        emit("story.stage_started", stage=stage)
        run = self.execution.read_run(run_id, True)
        packet = run["packet"]
        try:
            context = compile_context(packet)
            if stage == "interpreting":
                allowed_cards = sorted(node_id for node_id, node in packet["nodes"].items()
                                       if node["kind"] in {"source", "group"})
                result, receipt = generate_stage(run["provider"], run["model"],
                                                 "interpretations",
                                                 {"context": context, "allowed_card_node_ids": allowed_cards}, run_id,
                                                 on_dispatch=lambda receipt: self._dispatch_receipt(run_id, stage, receipt),
                                                 on_result=lambda text, receipt: self._received_response(run_id, stage, text, receipt))
                self._save_artifact(run_id, "interpretations_raw", result, receipt)
                self._check_candidates(packet, result)
                self._save_artifact(run_id, "interpretations", result, receipt)
                with self.execution._db() as db:
                    db.execute("UPDATE runs SET state='waiting_for_selection',updated_at=? WHERE id=?",
                               (_utc(), run_id))
                    self._event(db, run_id, "waiting_for_selection",
                                {"candidate_ids": [c["id"] for c in result["candidates"]]})
                emit("story.stage_finished", stage=stage, state="waiting_for_selection",
                     elapsed_ms=round((time.monotonic() - started) * 1000, 2))
                return {"run_id": run_id, "state": "waiting_for_selection",
                        "candidate_ids": [c["id"] for c in result["candidates"]]}
            with self.execution._db() as db:
                lineage = db.execute("SELECT * FROM story_lineage WHERE child_run_id=?",
                                     (run_id,)).fetchone()
            if lineage is None:
                raise RuntimeError("Plan run has no selected interpretation")
            parent = self.artifact(lineage["parent_run_id"], "interpretations")
            candidate = next(c for c in parent["content"]["candidates"]
                             if c["id"] == lineage["candidate_id"])
            if stage == "planning":
                selected = {"context": context, "candidate": candidate,
                            "candidate_hash": _hash(candidate)}
                plan, receipt = generate_stage(run["provider"], run["model"], "plan", selected, run_id,
                                               on_dispatch=lambda receipt: self._dispatch_receipt(run_id, stage, receipt),
                                               on_result=lambda text, receipt: self._received_response(run_id, stage, text, receipt))
                self._save_artifact(run_id, "plan_raw", plan, receipt)
                self._check_plan(packet, plan)
                self._save_artifact(run_id, "plan", plan, receipt)
                review = check_symbolic_plan(context, plan)
                if review["issues"]:
                    self._save_artifact(run_id, "plan_review", review, {})
                    with self.execution._db() as db:
                        db.execute("UPDATE runs SET state='waiting_for_resolution',updated_at=? WHERE id=?",
                                   (_utc(), run_id))
                        self._event(db, run_id, "waiting_for_resolution",
                                    {"issues": review["issues"]})
                    emit("story.stage_finished", stage=stage, state="waiting_for_resolution",
                         issue_count=len(review["issues"]),
                         elapsed_ms=round((time.monotonic() - started) * 1000, 2))
                    return {"run_id": run_id, "state": "waiting_for_resolution",
                            "issues": review["issues"]}
                with self.execution._db() as db:
                    db.execute("UPDATE runs SET state='writing',updated_at=? WHERE id=?",
                               (_utc(), run_id))
                    db.execute("INSERT INTO story_attempts VALUES (?,?,?,?,?,?,?,?)",
                               (run_id, "writing", uuid.uuid4().hex, os.getpid(),
                                "outcome_unknown", _utc(), None, None))
                    self._event(db, run_id, "writing", {"plan_hash": _hash(plan)})
            else:
                plan = self.artifact(run_id, "plan_resolved")["content"]
            draft, receipt = generate_stage(run["provider"], run["model"], "draft",
                                            {"context": context, "candidate": candidate, "plan": plan}, run_id,
                                            on_dispatch=lambda receipt: self._dispatch_receipt(run_id, "writing", receipt),
                                            on_result=lambda text, receipt: self._received_response(run_id, "writing", text, receipt))
            self._save_artifact(run_id, "draft_raw", draft, receipt)
            self._check_draft(packet, plan, draft)
            self._save_artifact(run_id, "draft", draft, receipt)
            current_head = self.execution.graph.read(run["project_id"])["revision"]
            freshness = "current" if current_head == run["revision"] else "stale"
            with self.execution._db() as db:
                db.execute("UPDATE runs SET state='completed',updated_at=?,response_text=?,"
                           "provider_receipt_json=? WHERE id=?",
                           (_utc(), _json(draft), _json(receipt), run_id))
                self._event(db, run_id, "completed", {"freshness": freshness,
                                                       "draft_hash": _hash(draft)})
            emit("story.stage_finished", stage=stage, state="completed", freshness=freshness,
                 elapsed_ms=round((time.monotonic() - started) * 1000, 2))
            return {"run_id": run_id, "state": "completed", "freshness": freshness}
        except Exception as exc:
            uncertain = isinstance(exc, (TimeoutError, InterruptedError))
            with self.execution._db() as db:
                db.execute("UPDATE runs SET state=?,updated_at=?,error=? WHERE id=?",
                           ("outcome_unknown" if uncertain else "failed", _utc(), type(exc).__name__, run_id))
                if not uncertain:
                    db.execute("UPDATE story_attempts SET state='failed',finished_at=? "
                               "WHERE run_id=? AND state='outcome_unknown'", (_utc(), run_id))
                self._event(db, run_id, "outcome_unknown" if uncertain else "failed",
                            {"error_class": type(exc).__name__})
            emit("story.stage_finished", stage=stage,
                 state="outcome_unknown" if uncertain else "failed",
                 error_class=type(exc).__name__,
                 elapsed_ms=round((time.monotonic() - started) * 1000, 2))
            raise

    def resolve_plan(self, run_id: str, expected_revision: int,
                     command_id: str, revised_plan: dict) -> dict:
        """Accept a corrected plan after review; preserve the provider plan unchanged."""
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValueError("Resolution command ID is required")
        if type(expected_revision) is not int:
            raise ValueError("Expected graph revision is required")
        if not isinstance(revised_plan, dict):
            raise ValueError("Revised plan must be an object")
        run = self.execution.read_run(run_id, True)
        if run["state"] != "waiting_for_resolution":
            raise ValueError("Run is not waiting for plan resolution")
        current = self.execution.graph.read(run["project_id"])["revision"]
        if current != expected_revision or current != run["revision"]:
            raise ValueError(f"Graph changed; current revision is {current}")
        self.artifact(run_id, "plan_review")
        self._check_plan(run["packet"], revised_plan)
        review = check_symbolic_plan(compile_context(run["packet"]), revised_plan)
        if review["issues"]:
            raise ValueError("Revised plan still has unresolved symbolic issues: " + _json(review["issues"]))
        digest = _hash(revised_plan)
        now = _utc()
        with self.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE runs SET state='queued_draft',updated_at=? WHERE id=? "
                                 "AND state='waiting_for_resolution'", (now, run_id)).rowcount
            if changed != 1:
                raise ValueError("Plan resolution state changed")
            db.execute("INSERT INTO story_plan_resolutions VALUES (?,?,?,?)",
                       (run_id, command_id, digest, now))
            db.execute("INSERT INTO story_artifacts VALUES (?,?,?,?,?,?)",
                       (run_id, "plan_resolved", digest, _json(revised_plan),
                        _json({"kind": "authored_resolution", "command_id": command_id}), now))
            self._event(db, run_id, "plan_resolved", {"plan_hash": digest,
                                                     "command_id": command_id})
        return {"run_id": run_id, "state": "queued_draft", "plan_hash": digest}

    def select(self, parent_run_id: str, candidate_id: str, expected_revision: int) -> dict:
        parent = self.execution.read_run(parent_run_id, True)
        if parent["state"] != "waiting_for_selection":
            raise ValueError("Run is not waiting for interpretation selection")
        current = self.execution.graph.read(parent["project_id"])["revision"]
        if current != expected_revision or current != parent["revision"]:
            raise ValueError(f"Graph changed; current revision is {current}")
        proposals = self.artifact(parent_run_id, "interpretations")["content"]["candidates"]
        if candidate_id not in {candidate["id"] for candidate in proposals}:
            raise ValueError("Unknown interpretation candidate")
        child_id, now = "scatter-run-" + uuid.uuid4().hex, _utc()
        with self.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            claimed = db.execute("UPDATE runs SET state='selected',updated_at=? WHERE id=? "
                                 "AND state='waiting_for_selection'",
                                 (now, parent_run_id)).rowcount
            if claimed != 1:
                raise ValueError("Interpretation has already been selected")
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (child_id, parent["project_id"], parent["revision"], parent["snapshot_hash"],
                        parent["output_id"], parent["provider"], parent["model"], "queued_plan",
                        parent["packet_hash"], _json(parent["packet"]), now, now, None, None, None))
            db.execute("INSERT INTO story_lineage VALUES (?,?,?)",
                       (child_id, parent_run_id, candidate_id))
            db.execute("INSERT INTO story_jobs VALUES (?,?)", (child_id, "staged"))
            self._event(db, child_id, "queued_plan", {"parent_run_id": parent_run_id,
                                                      "candidate_id": candidate_id})
            self._event(db, parent_run_id, "selected", {"candidate_id": candidate_id,
                                                       "child_run_id": child_id})
        return {"run_id": child_id, "parent_run_id": parent_run_id,
                "candidate_id": candidate_id, "state": "queued_plan"}
