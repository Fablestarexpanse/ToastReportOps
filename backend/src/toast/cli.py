"""`toast` — command line for the running Toast Report OPs stack.

Meant for scripts and for agents that prefer a shell to MCP: every command prints
JSON on stdout (errors go to stderr, exit status 1). The commands are the same
tools the MCP server offers (toast.agent_tools), so the safety rules are the same:
no approve/publish, and the two write commands need --write.

    toast status
    toast list-incidents --status active
    toast incident-facts --incident-id 01J... --limit 20
    toast --write create-draft --incident-id 01J...
    toast tools                       # list what's available

Set TOAST_API_URL (or --api) if the API isn't on http://127.0.0.1:8400. From
inside the stack:  docker compose exec -T api toast status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from toast.agent_tools import (
    DEFAULT_API_URL,
    TOOLS,
    ApiClient,
    Tool,
    ToolError,
    run_tool,
)


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="toast", description=__doc__.split("\n\n")[0])
    p.add_argument("--api", default=os.environ.get("TOAST_API_URL", DEFAULT_API_URL))
    p.add_argument("--write", action="store_true", help="allow the commands that change data")
    p.add_argument("--pretty", action="store_true", help="indent the JSON output")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("tools", help="list the available commands")
    for tool in TOOLS:
        sp = sub.add_parser(tool.name.replace("_", "-"), help=tool.description.split(". ")[0])
        for name, spec in tool.properties.items():
            kw: dict[str, Any] = {"help": spec.get("description", ""), "dest": name}
            if spec["type"] == "boolean":
                sp.add_argument(_flag(name), action="store_true", default=None, **kw)
            else:
                kw["type"] = int if spec["type"] == "integer" else str
                if "enum" in spec:
                    kw["choices"] = spec["enum"]
                sp.add_argument(_flag(name), required=name in tool.required, **kw)
    return p


def _tool_for(command: str) -> Tool:
    return next(t for t in TOOLS if t.name.replace("_", "-") == command)


def main(argv: list[str] | None = None, api: ApiClient | None = None) -> int:
    args = build_parser().parse_args(argv)
    dump = (
        (lambda o: json.dumps(o, indent=2, ensure_ascii=False, default=str))
        if args.pretty
        else (lambda o: json.dumps(o, ensure_ascii=False, default=str))
    )
    if args.command == "tools":
        print(
            dump([{"name": t.name, "write": t.write, "description": t.description} for t in TOOLS])
        )
        return 0
    tool = _tool_for(args.command)
    values = {k: v for k, v in vars(args).items() if k in tool.properties and v is not None}
    try:
        out = run_tool(api or ApiClient(args.api), tool.name, values, allow_write=args.write)
    except ToolError as exc:
        print(f"toast: {exc}", file=sys.stderr)
        return 1
    print(dump(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
