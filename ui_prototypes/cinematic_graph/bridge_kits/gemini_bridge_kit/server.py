"""Stdio JSON-RPC Bridge Server for Antigravity & Gemini.
Provides a standard JSON-RPC interface over stdio, matching the Codex App Server wire protocol
so any frontend (Electron, Flutter, C#, Node) can connect out-of-the-box.
"""
from __future__ import annotations
import json
import os
import sys
import threading
from typing import Any, Dict, Optional
from .client import GeminiAntigravityClient

def run_server():
    """Runs the stdio JSON-RPC loop."""
    client = GeminiAntigravityClient()
    active_thread_id: Optional[str] = None
    lock = threading.Lock()

    def send_rpc(msg: Dict[str, Any]):
        with lock:
            line = json.dumps(msg, ensure_ascii=False)
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            send_rpc({
                "id": req_id,
                "result": {
                    "serverInfo": {"name": "antigravity_gemini_bridge", "version": "1.0.0"},
                    "capabilities": {"streaming": True, "thoughts": True},
                },
            })

        elif method == "account/read":
            status = client.account()
            send_rpc({
                "id": req_id,
                "result": {
                    "account": {
                        "type": "antigravity_session",
                        "connected": status.get("connected", False),
                        "signedIn": status.get("signedIn", False),
                    },
                    "requiresKey": False,
                },
            })

        elif method == "model/list":
            send_rpc({
                "id": req_id,
                "result": {
                    "data": client.models(),
                },
            })

        elif method == "thread/start":
            # Initializes a conversation thread
            active_thread_id = None
            send_rpc({
                "id": req_id,
                "result": {
                    "thread": {"id": "thread_ready"},
                },
            })

        elif method == "turn/start":
            thread_id = params.get("threadId")
            model = params.get("model", "flash")
            user_input = params.get("input", [])
            prompt = ""
            if isinstance(user_input, list):
                for item in user_input:
                    if isinstance(item, dict) and item.get("type") == "text":
                        prompt += item.get("text", "")
            elif isinstance(user_input, str):
                prompt = user_input

            # Send immediate turn acknowledgment
            turn_id = f"turn_{int(threading.get_native_id())}"
            send_rpc({
                "id": req_id,
                "result": {
                    "turn": {"id": turn_id, "status": "running"},
                },
            })

            def execute_turn(t_id=turn_id, p=prompt, m=model):
                def on_delta(delta: str):
                    send_rpc({
                        "method": "item/agentMessage/delta",
                        "params": {"turnId": t_id, "delta": delta},
                    })

                def on_thought(thought: str):
                    send_rpc({
                        "method": "item/agentThought/delta",
                        "params": {"turnId": t_id, "delta": thought},
                    })

                try:
                    result = client.run(
                        prompt=p,
                        model=m,
                        on_delta=on_delta,
                        on_thought=on_thought,
                    )
                    send_rpc({
                        "method": "turn/completed",
                        "params": {
                            "turn": {
                                "id": t_id,
                                "status": "completed" if not result.get("cancelled") else "cancelled",
                                "output": result.get("text", ""),
                                "inputTokens": result.get("inputTokens", 0),
                                "outputTokens": result.get("outputTokens", 0),
                            }
                        },
                    })
                except Exception as ex:
                    send_rpc({
                        "method": "turn/completed",
                        "params": {
                            "turn": {"id": t_id, "status": "failed", "error": str(ex)},
                        },
                    })

            threading.Thread(target=execute_turn, daemon=True).start()

        elif method == "turn/interrupt":
            client.cancel()
            send_rpc({"id": req_id, "result": {"cancelled": True}})

        elif method == "bridge/close":
            client.close()
            send_rpc({"id": req_id, "result": {"closed": True}})
            break

        else:
            if req_id is not None:
                send_rpc({
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method {method} not found"},
                })

if __name__ == "__main__":
    run_server()
