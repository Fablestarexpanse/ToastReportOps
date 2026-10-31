# Models: what the tool uses and how they're installed safely

## One click

Double-click **`Install Models.cmd`**. It starts the model server if needed, shows what
it would download and how big, asks, then downloads and verifies. Interrupted? Run it
again; downloads resume. It never deletes anything.

| Profile (`-Profile ...`) | What | Download |
|---|---|---|
| `starter` (default) | `qwen3.5:9b` + `nomic-embed-text` — everything the tool needs | ~7 GB |
| `bakeoff-12gb` | starter + `gemma4:12b-it-qat`, `granite4.2:8b`, `embeddinggemma-2:270m`, to compare on your own data | ~21 GB |
| `bakeoff-24gb` | the above + `gemma4:26b-a4b-it-qat`, `granite4.2:30b`, `qwen3.8:27b` | ~72 GB |

The installer detects your NVIDIA card's memory and skips chat models that won't fit
(an estimate: weights + 15% + 1.5 GB for context). Pass `-VramGb N` if it can't detect it.

## What "safe" means here

1. **Official sources only.** Names must be plain Ollama-library names. Community
   uploads (`hf.co/...`, `user/model`) are refused: they carry weights nobody reviewed
   and *executable* chat templates, a known place to hide backdoors.
2. **Pinned.** `backend/src/toast/model_manifest.json` records the SHA-256 of each
   model's registry manifest, which in turn lists the SHA-256 of every weight file.
   Before downloading, the installer fetches the live manifest and **refuses if the tag
   has been re-pointed** since the pin. After downloading it checks what Ollama stored.
   Ollama itself verifies each weight file against the manifest while downloading.
3. **Fits your card.** See above.
4. **Proven to work.** Embedding models must return the declared vector size; chat
   models must answer a trivial prompt.
5. **Never destructive.** A failed check warns and tells you; it does not delete.

What this does *not* do: it can't tell you a model is good at wildfire extraction (that's
the bake-off) and it trusts the Ollama registry and the pins reviewed in git.

## Command line (inside the stack)

```bash
docker compose --profile desktop exec -T api python -m toast.modelsetup status
docker compose --profile desktop exec -T api python -m toast.modelsetup plan --profile bakeoff-12gb
docker compose --profile desktop exec -T api python -m toast.modelsetup install --vram-gb 12
docker compose --profile desktop exec -T api python -m toast.modelsetup verify
```

`status`/`verify` also warn if your configured `LLM_MODEL` isn't one of the pinned
official models.

## Maintainers: updating a pin

When you deliberately want a newer version of a model, re-pin from the registry, review
the diff in git, and commit:

```bash
python -m toast.modelsetup pin --write     # run from backend/
```

## About the default

The default changed from a community fine-tune (`hf.co/DavidAU/...Heretic...`) to the
official `qwen3.5:9b`. Reasons and evidence are in
`reports/Lightweight local models for wildfire tool.md`: the fine-tune only removes
refusals, its published benchmarks are author-run, and nothing about it helps a
fact-extraction tool. Same model family, so prompts behave the same. Change it back in
`.env` and OPTIONS if you prefer; `status` will flag it as unverified.

`qwen3.5:9b` ignores the `/no_think` switch this repo sends (it needs the API's `think`
parameter), so expect extraction to run slower than necessary until that is fixed; the
bake-off harness covers it.
