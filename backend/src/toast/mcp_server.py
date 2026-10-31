"""Model Context Protocol server for Toast Report OPs (stdio transport).

Lets an agent — Claude, Hermes, anything that speaks MCP — read the ledger, the
WATER board and the drafts, and (only if the operator enables it) leave notes
and request DRAFTS. It cannot approve, release or publish: those tools do not
exist (see toast.agent_tools). It talks to the running operator API over HTTP,
so it sees exactly what the workstation sees and inherits the API's validation.

Run:   python -m toast.mcp_server            (reads TOAST_API_URL, TOAST_AGENT_WRITE)
Docker: docker compose exec -T api python -m toast.mcp_server

Protocol: newline-delimited JSON-RPC 2.0 on stdin/stdout. stdout carries ONLY
protocol messages; diagnostics go to stderr. Implemented by hand (initialize,
ping, tools/list, tools/call) so the server needs no extra dependency.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, TextIO

from toast import __version__
from toast.agent_tools import (
    DEFAULT_API_URL,
    UNTRUSTED_NOTICE,
    ApiClient,
    ToolError,
    run_tool,
    visible_tools,
)

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "Toast Report OPs is a California wildfire and flood intelligence ledger run by one "
    "human operator. Call `status` first. Use the read tools to answer questions about "
    "incidents, evacuations, drafts, flood alerts, river gauges and reservoirs. "
    + UNTRUSTED_NOTICE
    + " You cannot publish or approve anything; drafts are reviewed by the operator. "
    "When relaying evacuation information, quote the official text exactly and say when "
    "it was observed."
)

# JSON-RPC error codes
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


def _error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


class McpServer:
    def __init__(self, api: ApiClient, *, allow_write: bool = False) -> None:
        self.api = api
        self.allow_write = allow_write

    # ---- method handlers -------------------------------------------------
    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        wanted = params.get("protocolVersion")
        return {
            "protocolVersion": wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "toast-report-ops", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def _tools_list(self) -> dict[str, Any]:
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                    "annotations": {
                        "readOnlyHint": not t.write,
                        "destructiveHint": False,
                        "openWorldHint": False,
                    },
                }
                for t in visible_tools(self.allow_write)
            ]
        }

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            raise ValueError("tools/call requires a tool name")
        try:
            out = run_tool(self.api, name, params.get("arguments"), allow_write=self.allow_write)
            text, is_error = json.dumps(out, ensure_ascii=False, default=str), False
        except ToolError as exc:
            # tool failures are results the model can read and react to, not protocol errors
            text, is_error = str(exc), True
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    # ---- dispatch ----------------------------------------------------------
    def handle(self, message: Any) -> dict[str, Any] | None:
        """One JSON-RPC message in, at most one response out (None for notifications)."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        method, id_ = message.get("method"), message.get("id")
        is_notification = "id" not in message
        if not isinstance(method, str):
            return None if is_notification else _error(id_, INVALID_REQUEST, "missing method")
        params = message.get("params") or {}
        if is_notification:
            return None  # notifications/initialized, notifications/cancelled, ...
        try:
            if method == "initialize":
                return _result(id_, self._initialize(params))
            if method == "ping":
                return _result(id_, {})
            if method == "tools/list":
                return _result(id_, self._tools_list())
            if method == "tools/call":
                return _result(id_, self._tools_call(params))
        except ValueError as exc:
            return _error(id_, INVALID_PARAMS, str(exc))
        return _error(id_, METHOD_NOT_FOUND, f"method not found: {method}")

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            response: Any
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response = _error(None, PARSE_ERROR, "invalid JSON")
            else:
                if isinstance(message, list):  # JSON-RPC batch (older protocol revisions)
                    response = [r for m in message if (r := self.handle(m)) is not None] or None
                else:
                    response = self.handle(message)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


def main() -> None:
    allow_write = os.environ.get("TOAST_AGENT_WRITE", "").lower() in ("1", "true", "yes")
    if "--write" in sys.argv[1:]:
        allow_write = True
    api = ApiClient(os.environ.get("TOAST_API_URL", DEFAULT_API_URL))
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    print(
        f"toast-mcp: serving {api.base_url} ({'read+draft' if allow_write else 'read-only'})",
        file=sys.stderr,
    )
    McpServer(api, allow_write=allow_write).serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
