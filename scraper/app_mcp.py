"""Authenticated, stateless JSON Streamable HTTP MCP inside Red Note's Flask app.

The stdio wrapper and this endpoint share exactly one tool registry/dispatcher.
Internal bridge dispatch does not use the network or activate desktop windows.
Supports negotiated MCP revisions 2025-03-26 and 2025-06-18. It has no SSE stream,
server initiated requests, sessions, subscriptions, or JSON-RPC batches.
"""
from __future__ import annotations

import json
import secrets

from flask import Blueprint, current_app, jsonify, request

from harvest_mcp import HarvestError, dispatch, rpc_error

HTTP_PROTOCOLS = {"2025-03-26", "2025-06-18"}
MAX_BODY_BYTES = 256_000


class EmbeddedHarvestClient:
    """Call the authenticated app bridge in-process, with no HTTP socket."""
    def __init__(self, app):
        self.app = app

    def call(self, route: str, payload: dict | None = None, timeout: int = 60) -> dict:
        if not route.startswith("/api/bridge/") or route.startswith("//"):
            raise HarvestError("Only local app bridge routes are supported.")
        token = getattr(self.app, "bridge_token", None)
        if not isinstance(token, str) or not token:
            raise HarvestError("The app's local control capability is unavailable.")
        with self.app.test_client() as client:
            response = client.open(
                route, method="POST" if payload is not None else "GET",
                json=payload, headers={"Authorization": "Bearer " + token},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
            value = response.get_json(silent=True)
        if not isinstance(value, dict):
            raise HarvestError("The app returned an unexpected result.")
        if response.status_code >= 400 or value.get("error"):
            raise HarvestError(str(value.get("error", "The app rejected the operation.")))
        return value


def create_mcp_blueprint():
    blueprint = Blueprint("rednote_mcp", __name__)

    @blueprint.route("/mcp", methods=["POST", "GET", "DELETE", "OPTIONS"])
    def mcp_endpoint():
        token = getattr(current_app, "bridge_token", None)
        supplied = request.headers.get("Authorization", "")
        if (request.remote_addr not in {"127.0.0.1", "::1"}
                or "Origin" in request.headers
                or not isinstance(token, str) or not token
                or not secrets.compare_digest(supplied.encode("utf-8"), ("Bearer " + token).encode("utf-8"))):
            return jsonify(error="Local Red Note MCP authorization required"), 403
        if request.method != "POST":
            response = jsonify(error="Use POST for JSON MCP messages; this endpoint has no event stream or sessions.")
            response.status_code = 405
            response.headers["Allow"] = "POST"
            return response
        protocol = request.headers.get("MCP-Protocol-Version", "2025-03-26")
        if protocol not in HTTP_PROTOCOLS:
            return jsonify(rpc_error(None, -32600, "Unsupported MCP protocol version")), 400
        accept = request.accept_mimetypes
        if accept["application/json"] <= 0 or accept["text/event-stream"] <= 0:
            return jsonify(error="Accept must allow application/json and text/event-stream"), 406
        if request.mimetype != "application/json":
            return jsonify(error="Content-Type must be application/json"), 415
        if request.content_length is None or request.content_length > MAX_BODY_BYTES:
            return jsonify(error="MCP message exceeds the local request size limit"), 413
        try:
            message = json.loads(request.get_data(cache=False).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return jsonify(rpc_error(None, -32700, "Invalid JSON-RPC message")), 400
        if not isinstance(message, dict):
            return jsonify(rpc_error(None, -32600, "MCP requires one JSON-RPC message per request")), 400
        if message.get("method") == "initialize":
            params = message.get("params", {})
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            if requested == "2024-11-05":
                # The legacy revision's HTTP+SSE transport is not implemented here.
                message = dict(message, params=dict(params, protocolVersion="2025-06-18"))
        result = dispatch(EmbeddedHarvestClient(current_app._get_current_object()), message)
        if result is None:
            return "", 202
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    return blueprint
