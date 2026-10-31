# Agent interface (MCP + CLI)

Lets an AI agent (Claude, Hermes, any MCP client) or a script work with the running
stack. Two front doors, one tool list (`backend/src/toast/agent_tools.py`):

* **MCP server** — `python -m toast.mcp_server` (stdio). For agent clients.
* **`toast` CLI** — same tools as shell commands that print JSON. For scripts and
  agents that prefer a terminal.

Both talk to the operator API over HTTP, so they see exactly what the workstation
sees and inherit its validation.

## What an agent can and cannot do

| | |
|---|---|
| **Can read** | system status, incidents, an incident's fact ledger, the live intake stream, drafts (with the verifier's findings), flood alerts + river gauges, reservoirs, the watch board, close/recheck proposals, dead jobs, and **archive search** — full-text search over facts, drafts, radio transcripts and archived news pages (`search_archive`, then `read_archive` for a window) |
| **Can write — only if you turn it on** | leave a note; ask the local model to draft an update (it lands as a **draft** for you to review) |
| **Cannot, ever** | approve, release or publish anything; change status; delete; edit settings; read or set credentials; add sources. Those tools do not exist, and publishing is also blocked by the database unless a human approved. |

**Read-only is the default.** Enable the two write tools with `TOAST_AGENT_WRITE=1`
(MCP) or `--write` (CLI).

### The real risk: instructions hidden in data

The ledger is built from news, social posts, radio transcripts and community mesh
traffic. Any of it can contain text written to hijack an AI ("ignore your
instructions and ..."). Rules this design follows, and you should keep:

1. Every tool result carries a notice that text fields are untrusted data.
2. The server's instructions tell the agent the same.
3. **Results are capped**, so no single call can hand an agent a wall of scraped text:
   any text field ≤ 600 characters (a draft body ≤ 6,000 — it's your own report), any
   list ≤ 50 items, a whole result ≤ 16,000 characters, archive snippets ≤ 160
   characters and an archive read ≤ 2,000. The result says when something was cut.
4. An agent with write access can at worst create a note or a draft; a draft still
   needs your approval, and the draft verifier flags invented numbers and
   paraphrased evacuation text. Do not give an agent other tools on the same
   machine (shell, email, posting) at the same time as this one without thinking
   about what a poisoned news snippet could make it do.

## Claude Code

A project config is included: `.mcp.json` runs the server inside the stack's `api`
container (`docker compose --profile desktop exec -T api python -m toast.mcp_server`).
Start the stack, open Claude Code in this folder, approve the `toast` server when
asked, then try: *"Use the toast tools: what's the system status and which fires have
evacuations?"*

To run it from the host instead (needs `uv` and the repo's Python environment):

```json
{
  "mcpServers": {
    "toast": {
      "command": "uv",
      "args": ["run", "--project", "backend", "python", "-m", "toast.mcp_server"],
      "env": { "TOAST_API_URL": "http://127.0.0.1:8400" }
    }
  }
}
```

Add `"TOAST_AGENT_WRITE": "1"` to `env` (or `"--write"` to the Docker args) to allow
notes and drafts.

## Hermes and other MCP clients

Any client that can launch a stdio MCP server works: point it at the command above
(`python -m toast.mcp_server`, or the `docker compose ... exec -T api ...` form) with
`TOAST_API_URL` if the API is not on `http://127.0.0.1:8400`. I have not tested Hermes
specifically; check its documentation for where stdio servers are configured.

## CLI

```bash
toast status
toast list-incidents --status active
toast incident-facts --incident-id <id> --limit 20
toast reservoirs --filter attention
toast water-board
toast search-archive --query '"evacuation order" Paradise'
toast read-archive --doc-id fact:01J... --around Paradise
toast --pretty list-drafts --status needs_review
toast --write create-draft --incident-id <id>
toast tools                      # everything available
```

From inside the stack: `docker compose --profile desktop exec -T api toast status`.
Output is JSON on stdout; errors go to stderr with exit status 1. Set `--api` or
`TOAST_API_URL` for a non-default address.

## Archive search

The index covers ledger facts, drafts and published reports, scanner transcripts once
transcribed, and archived HTML/text/RSS pages. Structured JSON feeds are skipped,
since their content already became facts. It is SQLite's built-in FTS5 (no extra
software), kept current every two minutes by the `archive_index` stage, and is a
rebuildable cache:

```bash
docker compose --profile desktop exec -T api python -m toast.pipeline.archive_search status
docker compose --profile desktop exec -T api python -m toast.pipeline.archive_search rebuild
```

Typed queries can't act as search commands: every word is quoted, so FTS operators and
column filters are inert. `"quoted phrases"` and a trailing `*` for a prefix still work.
Not built: text inside images (OCR). That needs a separately installed OCR engine and
its language data, and would follow the same pinned, never-auto-downloaded rule as models.

## Notes

* The API also enforces a browser-origin guard (CSRF / DNS rebinding). The MCP server
  and CLI send no `Origin` header, which is allowed; a `Host` of `localhost`,
  `127.0.0.1` or `api` is required.
* Tested: protocol behaviour against the official MCP Python client SDK
  (handshake, tool listing, calls, error results) and the full tool/validation logic in
  `tests/unit/test_agent_interface.py`. **Not tested in this environment:** the
  Docker-exec launch path (no Docker here) and any specific third-party agent client.
