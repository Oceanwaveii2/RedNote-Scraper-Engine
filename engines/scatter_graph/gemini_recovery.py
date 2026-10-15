"""Inspect an uncertain Antigravity turn without creating another conversation."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from execution import ScatterExecution, _json, _utc
from story_pipeline import StoryPipeline
from paths import DATA, RESEARCH

if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))
import provider_gemini as bridge


class GeminiRecovery:
    def __init__(self, pipeline: StoryPipeline | None = None):
        self.pipeline = pipeline or StoryPipeline()

    def _run(self, run_id):
        run = self.pipeline.execution.read_run(run_id)
        if run["provider"] != "gemini" or run["state"] != "outcome_unknown":
            raise ValueError("Only an uncertain Gemini run can be inspected")
        return run

    def _metadata(self, conversation_id):
        if not isinstance(conversation_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", conversation_id):
            raise ValueError("Conversation ID must be a UUID")
        if not bridge._configure_session():
            raise RuntimeError("Antigravity session metadata is unavailable")
        native = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity" / "resources" / "bin" / "language_server.exe"
        executable = str(native) if native.is_file() else bridge._executable()
        if not executable:
            raise RuntimeError("Antigravity sidecar is unavailable")
        response = bridge._command(executable,
                                   ["get-conversation-metadata", conversation_id],
                                   DATA / "gemini-workspace")
        return response["response"]["conversationMetadata"]["metadata"]

    def attach(self, run_id: str, conversation_id: str) -> dict:
        run = self._run(run_id)
        metadata = self._metadata(conversation_id)
        created = datetime.fromisoformat(metadata["createdAt"].replace("Z", "+00:00"))
        started = datetime.fromisoformat(run["created_at"])
        if abs((created - started).total_seconds()) > 120:
            raise ValueError("Conversation creation time does not match this run")
        configured_project = os.environ.get("ANTIGRAVITY_PROJECT_ID")
        if configured_project and metadata.get("projectId") != configured_project:
            raise ValueError("Conversation belongs to another Antigravity project")
        receipt = {"provider": "gemini", "model": run["model"],
                   "conversation_id": conversation_id, "metadata_verified_at": _utc(),
                   "remote_cancel_supported": False}
        with self.pipeline.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT stage,receipt_json FROM story_attempts WHERE run_id=? "
                             "ORDER BY started_at DESC LIMIT 1", (run_id,)).fetchone()
            if row is None:
                raise LookupError("Provider attempt not found")
            if row["receipt_json"]:
                prior = json.loads(row["receipt_json"])
                if prior.get("conversation_id") != conversation_id:
                    raise ValueError("A different conversation is already attached")
            db.execute("UPDATE story_attempts SET receipt_json=? WHERE run_id=? AND stage=?",
                       (_json(receipt), run_id, row["stage"]))
            self.pipeline._event(db, run_id, "conversation_attached",
                                 {"conversation_id": conversation_id, "stage": row["stage"]})
        return {"run_id": run_id, "conversation_id": conversation_id,
                "created_at": metadata["createdAt"], "stage": row["stage"]}

    def inspect(self, run_id: str) -> dict:
        self._run(run_id)
        with self.pipeline.execution._db() as db:
            row = db.execute("SELECT stage,receipt_json FROM story_attempts WHERE run_id=? "
                             "ORDER BY started_at DESC LIMIT 1", (run_id,)).fetchone()
        if row is None or not row["receipt_json"]:
            return {"run_id": run_id, "state": "conversation_unattached"}
        receipt = json.loads(row["receipt_json"])
        conversation_id = receipt.get("conversation_id")
        if not conversation_id:
            return {"run_id": run_id, "state": "conversation_unattached"}
        log = (Path.home() / ".gemini" / "antigravity" / "brain" / conversation_id /
               ".system_generated" / "logs" / "transcript.jsonl")
        if not log.is_file():
            return {"run_id": run_id, "conversation_id": conversation_id,
                    "stage": row["stage"], "state": "no_transcript"}
        states = []
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            states.append(item)
        if any(item.get("tool_calls") for item in states):
            state = "tools_used_rejected"
        elif any(item.get("type") == "PLANNER_RESPONSE" and item.get("status") == "DONE"
                 for item in states):
            state = "response_ready"
        else:
            state = "pending"
        return {"run_id": run_id, "conversation_id": conversation_id,
                "stage": row["stage"], "state": state,
                "last_step": states[-1].get("step_index") if states else None}

    def recover_interpretations(self, run_id: str) -> dict:
        status = self.inspect(run_id)
        if status.get("stage") != "interpreting" or status["state"] != "response_ready":
            raise ValueError("A completed interpretation transcript is required")
        class NoCancellation:
            def cancelled(self):
                return False
        value, turn_id = bridge._result_text(status["conversation_id"], NoCancellation(), timeout=2)
        run = self.pipeline.execution.read_run(run_id, True)
        receipt = {"provider": "gemini", "model": run["model"],
                   "conversation_id": status["conversation_id"], "turn_id": turn_id,
                   "recovered_at": _utc()}
        self.pipeline._received_response(run_id, "interpreting", value, receipt)
        result = bridge._parse_json(value)
        self.pipeline._save_artifact(run_id, "interpretations_raw", result, receipt)
        self.pipeline._check_candidates(run["packet"], result)
        self.pipeline._save_artifact(run_id, "interpretations", result, receipt)
        with self.pipeline.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE runs SET state='waiting_for_selection',updated_at=?,error=NULL "
                                 "WHERE id=? AND state='outcome_unknown'", (_utc(), run_id)).rowcount
            if changed != 1:
                raise ValueError("Run changed while recovering")
            db.execute("UPDATE story_attempts SET state='received',finished_at=?,receipt_json=? "
                       "WHERE run_id=? AND stage='interpreting'",
                       (_utc(), _json(receipt), run_id))
            self.pipeline._event(db, run_id, "waiting_for_selection",
                                 {"candidate_ids": [c["id"] for c in result["candidates"]],
                                  "recovered": True})
        return {"run_id": run_id, "state": "waiting_for_selection",
                "candidate_ids": [c["id"] for c in result["candidates"]]}
