"""Antigravity & Gemini App Bridge Client.
Automatically connects to the local Google Antigravity session/instance for seamless zero-key authentication,
with adaptive timeout handling, liveness tracking, and full transcript resolution.
"""
from __future__ import annotations
import atexit
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

def locate_antigravity() -> Optional[str]:
    """Finds the installed Antigravity CLI / agentapi binary automatically."""
    for cmd in ("agentapi.bat", "agentapi", "agy.exe", "agy"):
        found = shutil.which(cmd)
        if found:
            return str(Path(found).resolve())

    user_home = Path.home()

    bin_bat = user_home / ".gemini" / "antigravity" / "bin" / "agentapi.bat"
    if bin_bat.is_file():
        return str(bin_bat.resolve())

    local_app = Path(os.environ.get("LOCALAPPDATA", user_home / "AppData" / "Local"))
    cand_ls = local_app / "Programs" / "antigravity" / "resources" / "bin" / "language_server.exe"
    if cand_ls.is_file():
        return str(cand_ls.resolve())

    for pf_var in ("ProgramFiles", "ProgramFiles(x86)"):
        pf = Path(os.environ.get(pf_var, r"C:\Program Files"))
        cand = pf / "Antigravity" / "resources" / "bin" / "language_server.exe"
        if cand.is_file():
            return str(cand.resolve())

    return None

def get_brain_dir() -> Path:
    """Returns the Antigravity conversation brain directory."""
    return Path.home() / ".gemini" / "antigravity" / "brain"

def discover_antigravity_session() -> Tuple[Optional[str], Optional[str]]:
    """
    Discovers ANTIGRAVITY_LS_ADDRESS and ANTIGRAVITY_CSRF_TOKEN.
    First checks environment variables. If missing (e.g. running from external app / Codex),
    automatically inspects running language_server.exe processes via PowerShell,
    extracts the active port and CSRF token, and verifies with /healthz.
    """
    ls_addr = os.environ.get("ANTIGRAVITY_LS_ADDRESS")
    csrf_token = os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
    if ls_addr and csrf_token:
        return ls_addr, csrf_token

    ps_cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'language_server.exe'\" | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            if isinstance(data, dict):
                data = [data]

            for item in data:
                pid = item.get("ProcessId")
                cmdline = item.get("CommandLine", "")

                m_csrf = re.search(r"--csrf_token\s+([a-f0-9-]+)", cmdline)
                discovered_csrf = m_csrf.group(1) if m_csrf else csrf_token

                if not pid:
                    continue

                net_cmd = f"Get-NetTCPConnection -OwningProcess {pid} -State Listen | Select-Object -ExpandProperty LocalPort"
                net_proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", net_cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5,
                )
                if net_proc.returncode == 0:
                    ports = [p.strip() for p in net_proc.stdout.strip().splitlines() if p.strip().isdigit()]
                    for port in ports:
                        candidate = f"localhost:{port}"
                        try:
                            url = f"http://127.0.0.1:{port}/healthz"
                            res = urllib.request.urlopen(url, timeout=1)
                            if res.status == 200:
                                os.environ["ANTIGRAVITY_LS_ADDRESS"] = candidate
                                if discovered_csrf:
                                    os.environ["ANTIGRAVITY_CSRF_TOKEN"] = discovered_csrf
                                return candidate, discovered_csrf
                        except Exception:
                            continue
    except Exception:
        pass

    return ls_addr, csrf_token

class GeminiAntigravityClient:
    """Client for embedding Gemini into applications using the active Antigravity session."""

    def __init__(
        self,
        executable: Optional[str] = None,
        workspace: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        handler: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    ):
        self.executable = executable or locate_antigravity()
        self.workspace = Path(workspace).resolve() if workspace else Path.cwd()
        self.tools = list(tools or [])
        self.handler = handler
        self.active_conversation_id: Optional[str] = None
        self._turn_lock = threading.Lock()
        self._stop_requested = threading.Event()
        discover_antigravity_session()

    def _exec_agentapi(self, args: List[str], timeout: int = 60) -> Dict[str, Any]:
        """Execute an agentapi CLI command against the active Antigravity instance."""
        if not self.executable:
            raise RuntimeError(
                "Google Antigravity instance not detected on this system. "
                "Ensure Antigravity is installed and running."
            )

        cmd = [self.executable]
        if self.executable.lower().endswith("language_server.exe"):
            cmd.append("agentapi")
        cmd.extend(args)

        env = os.environ.copy()
        ls_addr, csrf_token = discover_antigravity_session()
        if ls_addr:
            env["ANTIGRAVITY_LS_ADDRESS"] = ls_addr
        if csrf_token:
            env["ANTIGRAVITY_CSRF_TOKEN"] = csrf_token

        proc = subprocess.run(
            cmd,
            cwd=str(self.workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip() or f"Process exited with code {proc.returncode}"
            raise RuntimeError(f"Antigravity agentapi failed: {err}")

        out = proc.stdout.strip()
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {"raw": out}

    def account(self) -> Dict[str, Any]:
        """Check active Antigravity session status."""
        exe = self.executable or locate_antigravity()
        ls_addr, csrf_token = discover_antigravity_session()
        if not exe or not ls_addr:
            return {
                "connected": False,
                "signedIn": False,
                "authType": "none",
                "error": "Antigravity running session not found",
            }

        brain_dir = get_brain_dir()

        return {
            "connected": True,
            "signedIn": True,
            "authType": "antigravity_session",
            "provider": "Google Antigravity / Gemini",
            "lsAddress": ls_addr,
            "csrfToken": csrf_token,
            "executable": exe,
            "brainDir": str(brain_dir),
            "requiresApiKey": False,
        }

    def models(self) -> List[Dict[str, Any]]:
        """List supported Gemini models via Antigravity session."""
        return [
            {
                "id": "flash",
                "model": "flash",
                "displayName": "Gemini Flash (Default - Fast & Multimodal)",
                "inputModalities": ["text", "image", "audio", "video"],
                "isDefault": True,
            },
            {
                "id": "flash_lite",
                "model": "flash_lite",
                "displayName": "Gemini Flash Lite (Ultra Fast)",
                "inputModalities": ["text", "image"],
                "isDefault": False,
            },
            {
                "id": "pro",
                "model": "pro",
                "displayName": "Gemini Pro (Advanced Reasoning)",
                "inputModalities": ["text", "image", "audio", "video"],
                "isDefault": False,
            },
        ]

    def run(
        self,
        prompt: str,
        model: str = "flash",
        rules: Optional[str] = None,
        title: Optional[str] = "App Bridge Turn",
        conversation_id: Optional[str] = None,
        on_delta: Optional[Callable[[str], None]] = None,
        on_thought: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """
        Executes a prompt using the active Antigravity session with adaptive liveness
        and real-time streaming, preventing premature timeouts on complex turns.
        """
        if not self._turn_lock.acquire(blocking=False):
            raise ValueError("A Gemini request is already running. Wait or cancel it.")

        self._stop_requested.clear()
        try:
            full_prompt = prompt
            if rules:
                full_prompt = f"System Rules:\n{rules}\n\nTask:\n{prompt}"

            # Start or continue conversation
            if not conversation_id:
                model_arg = f"--model={model}" if model in ("flash_lite", "flash", "pro") else "--model=flash"
                title_arg = f"--title={title}"
                res = self._exec_agentapi(["new-conversation", model_arg, title_arg, full_prompt], timeout=45)
                convo_data = res.get("response", {}).get("newConversation", {})
                cid = convo_data.get("conversationId")
                if not cid:
                    raise RuntimeError(f"Failed to create Antigravity conversation: {res}")
                conversation_id = cid
            else:
                title_arg = f"--title={title}"
                res = self._exec_agentapi(["send-message", title_arg, conversation_id, full_prompt], timeout=45)

            self.active_conversation_id = conversation_id

            brain_path = get_brain_dir() / conversation_id
            log_compact = brain_path / ".system_generated" / "logs" / "transcript.jsonl"
            log_full = brain_path / ".system_generated" / "logs" / "transcript_full.jsonl"

            start_time = time.monotonic()
            deadline = start_time + timeout
            last_activity = start_time
            last_size = 0

            # Wait for log initialization
            while not log_compact.is_file() and time.monotonic() < deadline:
                if self._stop_requested.is_set():
                    return {"text": "", "cancelled": True, "conversationId": conversation_id}
                time.sleep(0.1)

            if not log_compact.is_file():
                raise TimeoutError("Timed out waiting for Antigravity response transcript.")

            last_step_read = -1
            accumulated_text: List[str] = []
            accumulated_thoughts: List[str] = []
            tokens_info = {"input": 0, "output": 0, "cache_read": 0}

            # Tail loop with adaptive liveness extension
            while time.monotonic() < deadline:
                if self._stop_requested.is_set():
                    return {
                        "text": "".join(accumulated_text),
                        "cancelled": True,
                        "conversationId": conversation_id,
                    }

                # Check if file has updated
                try:
                    curr_size = log_compact.stat().st_size
                except OSError:
                    curr_size = 0

                if curr_size != last_size:
                    last_size = curr_size
                    last_activity = time.monotonic()
                    # Extend deadline if active writing is happening
                    deadline = max(deadline, last_activity + 60)

                # Read compact transcript lines
                try:
                    with open(log_compact, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                except OSError:
                    time.sleep(0.2)
                    continue

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        step = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    step_idx = step.get("step_index", -1)
                    if step_idx <= last_step_read:
                        continue

                    step_type = step.get("type")
                    step_status = step.get("status")

                    # Emit progress heartbeat
                    if on_progress:
                        on_progress({
                            "conversationId": conversation_id,
                            "stepIndex": step_idx,
                            "stepType": step_type,
                            "stepStatus": step_status,
                            "elapsed": round(time.monotonic() - start_time, 1),
                        })

                    if step_type == "PLANNER_RESPONSE":
                        last_step_read = step_idx
                        content = step.get("content", "")
                        thinking = step.get("thinking", "")
                        truncated = step.get("truncated_fields", [])

                        # If truncated, extract full payload from transcript_full or steps directory
                        if truncated and log_full.is_file():
                            try:
                                with open(log_full, "r", encoding="utf-8", errors="ignore") as f_full:
                                    for full_line in f_full:
                                        try:
                                            full_step = json.loads(full_line)
                                            if full_step.get("step_index") == step_idx:
                                                content = full_step.get("content", content)
                                                thinking = full_step.get("thinking", thinking)
                                                break
                                        except json.JSONDecodeError:
                                            continue
                            except OSError:
                                pass

                        if thinking and on_thought:
                            on_thought(thinking)
                        if thinking:
                            accumulated_thoughts.append(thinking)

                        if content:
                            accumulated_text.append(content)
                            if on_delta:
                                on_delta(content)

                        tokens_info["input"] = step.get("input_tokens", 0)
                        tokens_info["output"] = step.get("output_tokens", 0)
                        tokens_info["cache_read"] = step.get("cache_read_tokens", 0)

                        # A completed planner tool-call is not the final assistant answer.
                        if step_status == "DONE" and content and not step.get("tool_calls"):
                            return {
                                "text": "".join(accumulated_text),
                                "thinking": "\n".join(accumulated_thoughts),
                                "conversationId": conversation_id,
                                "inputTokens": tokens_info["input"],
                                "outputTokens": tokens_info["output"],
                                "cacheReadTokens": tokens_info["cache_read"],
                                "elapsedSeconds": round(time.monotonic() - start_time, 2),
                                "cancelled": False,
                            }

                time.sleep(0.25)

            raise TimeoutError(
                f"Antigravity generation timed out after {round(time.monotonic() - start_time, 1)}s. "
                "For heavy reasoning prompts, consider setting model='flash_lite' or increasing timeout."
            )
        finally:
            self._turn_lock.release()

    def cancel(self) -> Dict[str, bool]:
        """Cancel the active streaming generation."""
        self._stop_requested.set()
        return {"cancelled": True}

    def close(self):
        """Clean up active resources."""
        self.cancel()
