"""The agent interface: MCP protocol behaviour, argument validation and — above all —
the guarantee that an agent has no way to approve or publish anything."""

from __future__ import annotations

import contextlib
import io
import json

import httpx
import pytest

from toast import cli
from toast.agent_tools import (
    MAX_ITEMS,
    MAX_RESULT_CHARS,
    TOOLS,
    UNTRUSTED_NOTICE,
    ApiClient,
    ToolError,
    run_tool,
    validate_args,
    visible_tools,
)
from toast.mcp_server import SUPPORTED_VERSIONS, McpServer

CALLS: list[tuple[str, str, dict]] = []


def fake_api(extra=None) -> ApiClient:
    routes = {
        ("GET", "/system/status"): {"ok": True, "problems": {}},
        ("GET", "/incidents"): {"incidents": [{"id": "I1", "canonical_name": "FLUME FIRE"}]},
        ("GET", "/reservoirs"): {
            "reservoirs": [
                {"station": "ORO", "pinned": True, "near_full": False, "below_average": False},
                {"station": "SHA", "pinned": False, "near_full": True, "below_average": False},
                {"station": "TRN", "pinned": False, "near_full": False, "below_average": False},
            ]
        },
        ("POST", "/notes"): {"ok": True, "id": "N1"},
        **(extra or {}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        CALLS.append((request.method, request.url.path, body))
        hit = routes.get((request.method, request.url.path))
        if hit is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=hit)

    return ApiClient("http://api.test", transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _reset():
    CALLS.clear()


# ---------------------------------------------------------------- safety


def test_no_tool_can_publish_approve_or_reconfigure():
    forbidden = ("publish", "approve", "release", "delete", "setting", "credential", "status_set")
    for t in TOOLS:
        assert not any(word in t.name for word in forbidden), t.name


def test_tools_only_reach_allowlisted_api_routes(monkeypatch):
    """Run every tool and assert that nothing outside the read set (and the two
    write routes) is ever requested."""
    api = fake_api()
    for t in TOOLS:
        args = {n: "X1" for n in t.required}
        with contextlib.suppress(ToolError):
            run_tool(api, t.name, args, allow_write=True)
    allowed_prefixes = (
        "/system/status", "/incidents", "/intake", "/drafts", "/water", "/reservoirs",
        "/watch", "/activity/proposals", "/jobs/dead", "/notes", "/archive/",
    )  # fmt: skip
    for method, path, _ in CALLS:
        assert path.startswith(allowed_prefixes), path
        if method != "GET":
            assert path == "/notes" or path.endswith("/draft"), path


def test_write_tools_are_refused_unless_enabled():
    with pytest.raises(ToolError, match="write access is off"):
        run_tool(fake_api(), "add_note", {"body": "hi"}, allow_write=False)
    assert not CALLS


def test_write_tools_hidden_from_listing_unless_enabled():
    assert {t.name for t in visible_tools(False)}.isdisjoint({"add_note", "create_draft"})
    assert {"add_note", "create_draft"} <= {t.name for t in visible_tools(True)}


def test_results_carry_the_untrusted_text_notice():
    out = run_tool(fake_api(), "list_incidents", {}, allow_write=False)
    assert out["notice"] == UNTRUSTED_NOTICE and out["data"]["incidents"][0]["id"] == "I1"


# ------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "tool,args,msg",
    [
        ("get_incident", {}, "missing required"),
        ("get_incident", {"incident_id": "../../settings"}, "unexpected characters"),
        ("get_incident", {"incident_id": "I1", "evil": 1}, "unknown argument"),
        ("incident_facts", {"incident_id": "I1", "limit": 5000}, "between"),
        ("incident_facts", {"incident_id": "I1", "limit": "5"}, "integer"),
        ("incident_facts", {"incident_id": "I1", "limit": True}, "integer"),
        ("list_drafts", {"status": "published; DROP"}, "one of"),
        ("list_incidents", {"include_rx": "yes"}, "true or false"),
    ],
)
def test_bad_arguments_are_rejected_before_any_request(tool, args, msg):
    t = next(t for t in TOOLS if t.name == tool)
    with pytest.raises(ToolError, match=msg):
        validate_args(t, args)


def test_reservoir_filters():
    pinned = run_tool(fake_api(), "reservoirs", {"filter": "pinned"}, allow_write=False)
    attention = run_tool(fake_api(), "reservoirs", {"filter": "attention"}, allow_write=False)
    assert [r["station"] for r in pinned["data"]["reservoirs"]] == ["ORO"]
    assert [r["station"] for r in attention["data"]["reservoirs"]] == ["SHA"]


def test_a_long_text_field_is_clipped_and_flagged():
    big = {("GET", "/drafts"): {"x": "a" * 50_000}}
    out = run_tool(fake_api(big), "list_drafts", {}, allow_write=False)
    assert len(out["data"]["x"]) < 700 and out["data"]["x"].endswith("[clipped]")
    assert "clipped" in out


def test_long_lists_are_cut_to_the_item_cap():
    many = {("GET", "/drafts"): {"drafts": [{"id": f"D{i}"} for i in range(500)]}}
    out = run_tool(fake_api(many), "list_drafts", {}, allow_write=False)
    assert len(out["data"]["drafts"]) == MAX_ITEMS and "clipped" in out


def test_a_result_still_too_big_after_clipping_is_truncated_with_a_note():
    wide = {("GET", "/drafts"): {"drafts": [{"body": "b" * 590} for _ in range(50)]}}
    out = run_tool(fake_api(wide), "list_drafts", {}, allow_write=False)
    assert out["data"]["truncated"] is True and len(out["data"]["preview"]) == MAX_RESULT_CHARS


def test_get_draft_may_return_a_longer_body_than_scraped_fields():
    body = {("GET", "/drafts/D1"): {"draft": {"body_md": "w" * 5_000}}}
    out = run_tool(fake_api(body), "get_draft", {"draft_id": "D1"}, allow_write=False)
    assert len(out["data"]["draft"]["body_md"]) == 5_000 and "clipped" not in out


def test_read_archive_rejects_malformed_doc_ids():
    for bad in ("../etc", "fact:../../x", "secret:ABC", "fact:"):
        with pytest.raises(ToolError, match="expected form"):
            run_tool(fake_api(), "read_archive", {"doc_id": bad}, allow_write=False)
    assert not CALLS


def test_api_errors_and_unreachable_api_become_tool_errors():
    with pytest.raises(ToolError, match="404"):
        run_tool(fake_api(), "get_draft", {"draft_id": "D1"}, allow_write=False)
    dead = ApiClient("http://127.0.0.1:9")  # nothing listens here
    with pytest.raises(ToolError, match="cannot reach"):
        run_tool(dead, "status", {}, allow_write=False)


def test_create_draft_posts_only_the_window():
    api = fake_api({("POST", "/incidents/I1/draft"): {"created": True}})
    run_tool(
        api, "create_draft", {"incident_id": "I1", "window_start": "2026-10-01"}, allow_write=True
    )
    assert CALLS[-1] == ("POST", "/incidents/I1/draft", {"window_start": "2026-10-01"})


# ------------------------------------------------------------------- MCP


def rpc(server, method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        msg["id"] = id_
    if params is not None:
        msg["params"] = params
    return server.handle(msg)


def test_initialize_negotiates_version_and_declares_tools():
    s = McpServer(fake_api())
    r = rpc(s, "initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert r["protocolVersion"] == "2025-03-26" and "tools" in r["capabilities"]
    assert "cannot publish or approve" in r["instructions"]
    fallback = rpc(s, "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert fallback["protocolVersion"] == SUPPORTED_VERSIONS[0]


def test_notifications_get_no_response():
    s = McpServer(fake_api())
    assert rpc(s, "notifications/initialized", id_=None) is None


def test_ping_and_unknown_method():
    s = McpServer(fake_api())
    assert rpc(s, "ping")["result"] == {}
    assert rpc(s, "resources/list")["error"]["code"] == -32601


def test_tools_list_shape_and_write_gating():
    tools = rpc(McpServer(fake_api()), "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "status" in names and "add_note" not in names
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["annotations"]["readOnlyHint"] is True
    on = rpc(McpServer(fake_api(), allow_write=True), "tools/list")["result"]["tools"]
    assert {t["name"] for t in on if not t["annotations"]["readOnlyHint"]} == {
        "add_note",
        "create_draft",
    }


def test_tools_call_success_and_error_results():
    s = McpServer(fake_api())
    ok = rpc(s, "tools/call", {"name": "status", "arguments": {}})["result"]
    assert ok["isError"] is False and json.loads(ok["content"][0]["text"])["data"]["ok"] is True
    bad = rpc(s, "tools/call", {"name": "add_note", "arguments": {"body": "x"}})["result"]
    assert bad["isError"] is True and "write access is off" in bad["content"][0]["text"]
    unknown = rpc(s, "tools/call", {"name": "publish_draft", "arguments": {}})["result"]
    assert unknown["isError"] is True


def test_tools_call_without_a_name_is_invalid_params():
    assert rpc(McpServer(fake_api()), "tools/call", {})["error"]["code"] == -32602


def test_stdio_loop_emits_only_protocol_lines():
    s = McpServer(fake_api())
    lines = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    stdin = io.StringIO("\n".join(json.dumps(m) for m in lines) + "\nnot json\n\n")
    stdout = io.StringIO()
    s.serve(stdin, stdout)
    out = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [o.get("id") for o in out] == [1, 2, None]  # no reply to the notification
    assert out[2]["error"]["code"] == -32700


# ------------------------------------------------------------------- CLI


def test_cli_prints_json_for_a_read_command(capsys):
    assert cli.main(["list-incidents", "--status", "active"], api=fake_api()) == 0
    assert json.loads(capsys.readouterr().out)["data"]["incidents"][0]["id"] == "I1"


def test_cli_refuses_write_commands_without_the_flag(capsys):
    assert cli.main(["add-note", "--body", "hello"], api=fake_api()) == 1
    assert "write access is off" in capsys.readouterr().err
    assert not CALLS
    assert cli.main(["--write", "add-note", "--body", "hello"], api=fake_api()) == 0


def test_cli_tools_listing(capsys):
    assert cli.main(["tools"]) == 0
    listed = {t["name"]: t["write"] for t in json.loads(capsys.readouterr().out)}
    assert listed["status"] is False and listed["create_draft"] is True


def test_cli_rejects_bad_input_at_parse_time():
    with pytest.raises(SystemExit):
        cli.main(["incident-facts"])  # missing --incident-id
    with pytest.raises(SystemExit):
        cli.main(["list-drafts", "--status", "bogus"])
