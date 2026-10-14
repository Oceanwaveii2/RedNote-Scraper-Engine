"""Explicit Gemini dispatch through a configured Antigravity agentapi sidecar.

This adapter never discovers credentials from another process. The sidecar
address and CSRF token must be exported through its supported environment.
Without them a job stays awaiting_session and no source content is sent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from provider_codex import RULES
from provider_runtime import JobMonitor
from study_core import StudyEngine, utc_now
from study_storage import SqliteArchive, SqliteStudies


HERE = Path(__file__).resolve().parent
BRIDGE_KIT = HERE.parent.parent / "ui_prototypes" / "cinematic_graph" / "bridge_kits"
PROJECT_CONFIG = HERE / "gemini-project.json"
MODELS = ("flash_lite", "flash", "pro")


def _configure_session() -> bool:
    """Use the user's installed bridge kit and this project's non-secret ID."""
    if not os.environ.get("ANTIGRAVITY_PROJECT_ID") and PROJECT_CONFIG.is_file():
        try:
            project_id = json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))["projectId"]
            if isinstance(project_id, str) and project_id:
                os.environ["ANTIGRAVITY_PROJECT_ID"] = project_id
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if not BRIDGE_KIT.is_dir():
        return False
    if str(BRIDGE_KIT) not in sys.path:
        sys.path.insert(0, str(BRIDGE_KIT))
    from gemini_bridge_kit.client import discover_antigravity_session
    address, token = discover_antigravity_session()
    return bool(address and token and os.environ.get("ANTIGRAVITY_PROJECT_ID"))


def _executable() -> str | None:
    found = shutil.which("agentapi.bat") or shutil.which("agentapi")
    if found:
        return found
    wrapper = Path.home() / ".gemini" / "antigravity" / "bin" / "agentapi.bat"
    if wrapper.is_file():
        return str(wrapper)
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity" / "resources" / "bin" / "language_server.exe"
    return str(local) if local.is_file() else None


def status() -> dict:
    exe = _executable()
    configured = _configure_session()
    validated_turns = SqliteStudies().validated_output_count("gemini")
    return {"provider": "gemini-antigravity", "executable_available": bool(exe),
            "sidecar_environment_configured": configured,
            "project_context_configured": bool(os.environ.get("ANTIGRAVITY_PROJECT_ID")),
            "ready_to_attempt": bool(exe and configured),
            "models_declared_by_adapter": list(MODELS),
            "account_verified": False, "inference_verified": validated_turns > 0,
            "historical_validated_turns": validated_turns,
            "remote_cancel_supported": False,
            "research_broker_ready": False,
            "research_broker_blocker": "Antigravity agentapi invoked native tools despite ask profile; exclusive Red Engine broker control is unverified.",
            "note": "Validated turns are historical; model labels and environment presence do not prove current entitlement."}


def _command(exe: str, args: list[str], workspace: Path) -> dict:
    command = [exe] + (["agentapi"] if exe.lower().endswith("language_server.exe") else []) + args
    result = subprocess.run(command, cwd=str(workspace), env=os.environ.copy(),
                            capture_output=True, text=True, encoding="utf-8", timeout=45,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        # Do not leak the prompt or local session token through provider stderr.
        raise RuntimeError(f"Antigravity agentapi exited with code {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Antigravity agentapi returned non-JSON metadata") from exc


def _result_text(conversation_id: str, monitor: JobMonitor, timeout: int = 240) -> tuple[str, str]:
    log = Path.home() / ".gemini" / "antigravity" / "brain" / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
    full_log = log.with_name("transcript_full.jsonl")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if monitor.cancelled():
            raise InterruptedError("Local Gemini wait cancelled; remote turn outcome is unknown")
        if log.is_file():
            try:
                lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            parsed = []
            for line in lines:
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            if any(step.get("tool_calls") for step in parsed):
                raise RuntimeError("Gemini used tools; source-only analysis output was rejected")
            for step in reversed(parsed):
                if step.get("type") != "PLANNER_RESPONSE" or step.get("status") != "DONE" or step.get("tool_calls"):
                    continue
                content = step.get("content") or ""
                index = step.get("step_index")
                if step.get("truncated_fields") and full_log.is_file():
                    for full_line in reversed(full_log.read_text(encoding="utf-8", errors="replace").splitlines()):
                        try:
                            full_step = json.loads(full_line)
                        except json.JSONDecodeError:
                            continue
                        if full_step.get("step_index") == index:
                            content = full_step.get("content") or content
                            break
                if content:
                    return content, str(index)
        time.sleep(0.25)
    raise TimeoutError("Gemini response transcript did not complete before the deadline")


def _parse_json(text: str) -> dict:
    value = text.strip()
    if value.startswith("```"):
        first_break = value.find("\n")
        if first_break < 0 or not value.endswith("```"):
            raise ValueError("Gemini returned an incomplete JSON fence")
        value = value[first_break + 1:-3].strip()
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("Gemini output must be a JSON object")
    return result


def dispatch(study_id: str, job_id: str) -> dict:
    engine = StudyEngine(SqliteArchive(), SqliteStudies())
    job = engine.job(study_id, job_id)
    if job["provider"] != "gemini" or job["state"] != "awaiting_session":
        raise ValueError("Choose an awaiting-session Gemini job")
    capability = status()
    if not capability["ready_to_attempt"]:
        raise ValueError("Antigravity sidecar environment is unavailable; job remains unsent")
    model = job["requested_model"] or "flash"
    if model not in MODELS:
        raise ValueError("Choose flash_lite, flash or pro for this sidecar")
    workspace = HERE / "data" / "gemini-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    context = engine.begin_provider_job(study_id, job_id, "gemini", model)
    monitor = JobMonitor(engine.studies, study_id, job_id, lambda: None).start()
    try:
        # The Windows agentapi parser truncates a positional prompt at its first
        # literal newline. Keep the complete request on one CLI argument line;
        # JSON escapes any line breaks inside source text.
        prompt = ("System Rules: " + RULES + " Return only a JSON object with keys "
                  "summary and observations. Each observation needs source_kind, "
                  "source_id, quote, note, uncertainty. Task: Analyze the selected "
                  "evidence for a cautious human-review proposal. Return an empty "
                  "observations list if unsupported. Evidence JSON: "
                  + json.dumps(context["payload"], ensure_ascii=True, sort_keys=True))
        engine.studies.advance_job(study_id, job_id, "running", "running", utc_now(),
                                   {"dispatch_attempted": True, "provider": "gemini", "model": model})
        result = _command(capability_exe := _executable(),
                          ["new-conversation", f"--model={model}",
                           "--title=Red Engine evidence review", prompt], workspace)
        conversation_id = result.get("response", {}).get("newConversation", {}).get("conversationId")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise RuntimeError("Antigravity did not return a conversation ID")
        response, turn_id = _result_text(conversation_id, monitor)
        if monitor.cancelled():
            engine.fail_running_job(study_id, job_id, "Cancelled")
            return {"job_id": job_id, "state": "cancelled", "output_saved": False,
                    "provider_outcome": "unknown"}
        return engine.save_job_proposal(study_id, job_id, model, conversation_id,
                                        turn_id, _parse_json(response))
    except Exception as exc:
        if engine.job(study_id, job_id)["state"] in {"running", "cancel_requested"}:
            engine.fail_running_job(study_id, job_id, type(exc).__name__)
        raise
    finally:
        monitor.close()


def dispatch_research(study_id: str, question: str, model: str = "flash") -> dict:
    """Use the explicit host-mediated Red Engine broker for live research."""
    from broker_dispatch import run_gemini
    return run_gemini(study_id, question, model)
