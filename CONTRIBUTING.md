# Contributing

Thanks for helping. This project relays public-safety information, so the bar is
"correct and safe" before "more features". Please read this page first.

## Licensing and sign-off (DCO)

The project is licensed under [Apache-2.0](LICENSE). By contributing, you agree your
contribution is licensed under the same terms (Apache-2.0, section 5) — no separate
contributor agreement.

Every commit must be **signed off** to certify the
[Developer Certificate of Origin](https://developercertificate.org/): that you wrote it,
or otherwise have the right to submit it under this license. Use `git commit -s`, which
adds a line like:

```
Signed-off-by: Your Name <you@example.com>
```

The sign-off is a statement *you* make; nobody else (including an AI assistant) can make
it for you. Pull requests with unsigned commits will be asked to amend
(`git commit --amend -s`, or `git rebase --signoff` for several).

Don't contribute code you copied from somewhere whose license doesn't allow it, and say
where anything you adapted came from.

## Before you open a pull request

Run the same checks CI runs:

```bash
cd backend
uv sync --frozen --all-extras          # includes the optional mesh extra, which the tests cover
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest

cd ../operator-ui
npm ci && npm run lint && npx tsc --noEmit && npm run build
```

Always pass `--frozen` to uv: without it uv may rewrite `uv.lock`.

## The rules that keep this safe

These are enforced in code and tests; a change that weakens one needs a strong reason
and a discussion first ([DEVELOPER_HANDOFF.md §11](DEVELOPER_HANDOFF.md)):

- **The ledger is append-only.** Facts are superseded, never edited (database triggers).
- **A human publishes.** Nothing reaches the public without operator approval.
- **Official first, verbatim evacuations.** Unofficial sources never write tier-1 facts;
  evacuation text is relayed exactly as officials issued it.
- **The model proposes, code verifies.** Extraction and draft verifiers stay on.
- **All outbound fetches go through `core/fetch.py`** (SSRF protection); no new HTTP
  clients for untrusted URLs.
- **Agents read in small pieces.** New agent tools are read-only unless there's a strong
  case, and respect the result caps in `agent_tools.py`.
- **Nothing downloads code or models automatically.** Models are pinned
  (`model_manifest.json`); container images are pinned by digest.
- **Respect data providers.** Note a source's terms of use in the PR; some feeds can be
  shown but not stored or republished.

## What to work on

Issues labelled `good first issue` are a good start. Planned extension points (themes,
map-layer packs, hazard packs such as tornadoes, region packs for other states) are
described in the README; open an issue to discuss before starting one, so the design
fits the pack system.

## Conduct

Be kind, assume good faith, and keep discussion about the work. Reports of
unacceptable behaviour can be made by opening an issue marked private, or to the
maintainers directly.
