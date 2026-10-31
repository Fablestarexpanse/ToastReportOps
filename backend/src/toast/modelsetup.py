"""Download the AI models the tool needs, safely and with one command.

    python -m toast.modelsetup status            what's installed and verified
    python -m toast.modelsetup plan              what an install would download
    python -m toast.modelsetup install           download, verify, smoke-test
    python -m toast.modelsetup verify            re-check installed models against the pins
    python -m toast.modelsetup pin --write       (maintainers) refresh pins from the registry

"Safe" here means four concrete things:

1. Official sources only. Models come from the Ollama library under the names in
   model_manifest.json. Community uploads (hf.co/..., user/model) are refused —
   they carry executable chat templates and unreviewed weights.
2. Pinned. The manifest records the SHA-256 of each model's registry manifest (which
   in turn lists the SHA-256 of every weight file). Before downloading we fetch the
   manifest ourselves and refuse if the tag has been re-pointed since the pin was
   reviewed; after downloading we check what Ollama stored. Ollama itself verifies
   every weight file against the manifest while downloading.
3. Fits your GPU. Given the card's memory, models that won't fit are skipped.
4. Proven to work. Embedding models must return the declared vector size; chat
   models must answer a trivial prompt.

Downloads resume if interrupted — just run it again. A failed check never deletes
anything: you are told, and decide.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")
REGISTRY_URL = "https://registry.ollama.ai"
# official library names only: no slashes, no hostnames, no hf.co/...
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*(?::[A-Za-z0-9._-]+)?$")
GB = 1_000_000_000


class ModelSetupError(Exception):
    """A problem to report to the operator in plain words."""


# ---------------------------------------------------------------- manifest


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for name in data["models"]:
        check_name(name)
    return data


def check_name(name: str) -> str:
    if not _NAME.match(name):
        raise ModelSetupError(
            f"'{name}' is not an official Ollama library model name. Community uploads "
            "(hf.co/..., user/model) are refused because their weights and chat "
            "templates are unreviewed."
        )
    return name if ":" in name else f"{name}:latest"


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text.split("-")[0])[:3])


def required_vram_gb(entry: dict[str, Any]) -> float | None:
    """Rough memory a model needs while loaded: weights plus ~15% and 1.5 GB for the
    context cache and runtime. An estimate, not a measurement."""
    size = entry.get("size_bytes")
    return None if size is None else round(size / GB * 1.15 + 1.5, 1)


# ------------------------------------------------------------ remote services


class Registry:
    """Read-only client for the official Ollama registry (fixed host, fixed paths)."""

    def __init__(self, transport: Any = None) -> None:
        self.http = httpx.Client(base_url=REGISTRY_URL, timeout=30, transport=transport)

    def manifest(self, name: str) -> dict[str, Any]:
        model, _, tag = check_name(name).partition(":")
        try:
            r = self.http.get(
                f"/v2/library/{model}/manifests/{tag}",
                headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
            )
        except httpx.HTTPError as exc:
            raise ModelSetupError(
                f"cannot reach the Ollama registry to verify {name} ({type(exc).__name__}). "
                "Check your internet connection."
            ) from exc
        if r.status_code == 404:
            raise ModelSetupError(f"{name} does not exist in the Ollama library")
        if r.status_code != 200:
            raise ModelSetupError(f"registry returned {r.status_code} for {name}")
        digest = hashlib.sha256(r.content).hexdigest()
        header = r.headers.get("ollama-content-digest", "").removeprefix("sha256:")
        if header and header != digest:
            raise ModelSetupError(f"registry response for {name} is corrupt (digest mismatch)")
        layers = r.json().get("layers", [])
        return {"sha256": digest, "size_bytes": sum(layer.get("size", 0) for layer in layers)}


class Ollama:
    """The parts of Ollama's native API the installer needs."""

    def __init__(self, base_url: str, transport: Any = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=600, transport=transport)

    def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            r = self.http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ModelSetupError(
                f"cannot reach Ollama at {self.base_url} ({type(exc).__name__}). "
                "Is the stack running? (docker compose --profile desktop up -d llm)"
            ) from exc
        return r

    def version(self) -> str:
        return str(self._call("GET", "/api/version").json().get("version", "0"))

    def installed(self) -> dict[str, str]:
        r = self._call("GET", "/api/tags")
        return {m["name"]: m.get("digest", "") for m in r.json().get("models", [])}

    def pull(self, name: str) -> Iterator[dict[str, Any]]:
        try:
            with self.http.stream("POST", "/api/pull", json={"model": name, "stream": True}) as r:
                if r.status_code != 200:
                    r.read()
                    raise ModelSetupError(f"Ollama refused to pull {name}: {r.text[:200]}")
                for line in r.iter_lines():
                    if line.strip():
                        yield json.loads(line)
        except httpx.HTTPError as exc:
            raise ModelSetupError(
                f"the download of {name} was interrupted ({type(exc).__name__}). "
                "Run the same command again - it resumes where it stopped."
            ) from exc

    def embed(self, name: str, text: str) -> list[float]:
        r = self._call("POST", "/api/embed", json={"model": name, "input": text})
        if r.status_code != 200:
            raise ModelSetupError(f"embedding test failed for {name}: {r.text[:200]}")
        return list(r.json()["embeddings"][0])

    def chat(self, name: str, prompt: str) -> str:
        body: dict[str, Any] = {
            "model": name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_predict": 32, "temperature": 0},
        }
        r = self._call("POST", "/api/chat", json={**body, "think": False})
        if r.status_code == 400:  # a model without a thinking mode may reject the flag
            r = self._call("POST", "/api/chat", json=body)
        if r.status_code != 200:
            raise ModelSetupError(f"chat test failed for {name}: {r.text[:200]}")
        return str(r.json().get("message", {}).get("content", ""))


# --------------------------------------------------------------------- plan


@dataclass
class PlanItem:
    name: str
    action: str  # present | pull | too_big | unpinned
    size_bytes: int | None
    reason: str = ""
    role: str = "chat"


def plan(
    manifest: dict[str, Any],
    names: list[str],
    installed: dict[str, str],
    *,
    vram_gb: float | None = None,
) -> list[PlanItem]:
    items: list[PlanItem] = []
    for name in names:
        entry = manifest["models"].get(name)
        if entry is None:
            raise ModelSetupError(f"'{name}' is not in model_manifest.json")
        size, role = entry.get("size_bytes"), entry["role"]
        need = required_vram_gb(entry)
        if "manifest_sha256" not in entry:
            action, reason = "unpinned", "no pinned digest; run 'pin --write'"
        elif vram_gb is not None and need is not None and role == "chat" and need > vram_gb:
            action, reason = "too_big", f"needs about {need} GB; you have {vram_gb}"
        elif installed.get(name, "").removeprefix("sha256:") == entry["manifest_sha256"]:
            action, reason = "present", ""
        else:
            action = "pull"
            reason = "installed copy differs from the pin" if name in installed else ""
        items.append(PlanItem(name, action, size, reason, role))
    return items


def _fmt_gb(n: int | None) -> str:
    return "?" if n is None else f"{n / GB:.1f} GB"


def format_plan(items: list[PlanItem]) -> str:
    marks = {"present": "ok  ", "pull": "get ", "too_big": "skip", "unpinned": "skip"}
    lines = [
        f"  [{marks[i.action]}] {i.name:<28} {_fmt_gb(i.size_bytes):>8}  {i.reason}".rstrip()
        for i in items
    ]
    total = sum(i.size_bytes or 0 for i in items if i.action == "pull")
    lines.append(f"  download: {_fmt_gb(total)}")
    chats = [i for i in items if i.role == "chat"]
    if chats and not any(i.action in ("present", "pull") for i in chats):
        lines += [
            "",
            "  ! No chat model fits in the graphics memory available, so the AI features",
            "    (extraction, drafting, triage) won't work until one is installed. Close other",
            "    programs using the GPU and run this again, or choose a smaller profile",
            "    (Install Models.cmd -Profile small-8gb).",
        ]
    return "\n".join(lines)


# ----------------------------------------------------------------- actions


def install(
    manifest: dict[str, Any],
    names: list[str],
    ollama: Ollama,
    registry: Registry,
    *,
    vram_gb: float | None = None,
    smoke: bool = True,
    out: Any = print,
) -> int:
    """Download and verify. Returns a process exit code (0 ok, 3 verification warning)."""
    if parse_version(ollama.version()) < parse_version(manifest["min_ollama_version"]):
        raise ModelSetupError(
            f"Ollama {ollama.version()} is too old; this tool needs "
            f"{manifest['min_ollama_version']} or newer. Update it with: "
            "docker compose --profile desktop pull llm  (then restart the stack)"
        )
    items = plan(manifest, names, ollama.installed(), vram_gb=vram_gb)
    out(format_plan(items))
    warnings = 0
    for item in items:
        entry = manifest["models"][item.name]
        if item.action in ("too_big", "unpinned"):
            continue
        if item.action == "pull":
            live = registry.manifest(item.name)
            if live["sha256"] != entry["manifest_sha256"]:
                raise ModelSetupError(
                    f"{item.name} has changed upstream since it was pinned "
                    f"(pinned {entry['manifest_sha256'][:12]}, "
                    f"registry now {live['sha256'][:12]}). "
                    "Refusing to download an unreviewed version. If you trust the change, "
                    "a maintainer can re-pin it with: python -m toast.modelsetup pin --write"
                )
            _pull(ollama, item.name, out)
            stored = ollama.installed().get(item.name, "").removeprefix("sha256:")
            if stored != entry["manifest_sha256"]:
                out(
                    f"  WARNING: {item.name} stored as {stored[:12] or 'nothing'}, pinned "
                    f"{entry['manifest_sha256'][:12]}. Not deleted; investigate before using it."
                )
                warnings += 1
        if smoke and not _smoke(ollama, item.name, entry, out):
            warnings += 1
    out("done." if not warnings else f"finished with {warnings} warning(s) - read the lines above.")
    return 3 if warnings else 0


def _pull(ollama: Ollama, name: str, out: Any) -> None:
    out(f"  downloading {name} ...")
    last_pct = -10
    for event in ollama.pull(name):
        if "error" in event:
            msg = str(event["error"])
            if re.search(r"no space|disk", msg, re.IGNORECASE):
                raise ModelSetupError(
                    f"not enough disk space to download {name}. Free some space "
                    "(or move the model store: OLLAMA_MODELS_DIR in .env) and run again."
                )
            raise ModelSetupError(f"download of {name} failed: {msg}")
        total, done = event.get("total"), event.get("completed")
        if total and done is not None:
            pct = int(100 * done / total)
            if pct - last_pct >= 10:
                out(f"    {pct:3d}%  ({done / GB:.1f} of {total / GB:.1f} GB)")
                last_pct = pct
    out(f"  downloaded {name}")


def _smoke(ollama: Ollama, name: str, entry: dict[str, Any], out: Any) -> bool:
    try:
        if entry["role"] == "embed":
            vec = ollama.embed(name, "wildfire near Paradise, California")
            want = entry.get("dim")
            if want and len(vec) != want:
                out(f"  WARNING: {name} returned {len(vec)}-dim vectors, expected {want}")
                return False
            out(f"  tested {name}: {len(vec)}-dim embedding ok")
        else:
            reply = ollama.chat(name, "Reply with exactly one word: ready")
            if not reply.strip():
                out(f"  WARNING: {name} gave an empty reply")
                return False
            out(f"  tested {name}: answered ({reply.strip()[:30]!r})")
    except ModelSetupError as exc:
        out(f"  WARNING: {exc}")
        return False
    return True


def pin(manifest: dict[str, Any], registry: Registry, out: Any = print) -> dict[str, Any]:
    """Refresh pinned digests/sizes from the live registry. Returns the new manifest."""
    for name, entry in manifest["models"].items():
        live = registry.manifest(name)
        old = entry.get("manifest_sha256")
        entry.update(
            manifest_sha256=live["sha256"],
            size_bytes=live["size_bytes"],
            pinned_at=str(date.today()),
        )
        status = "new" if old is None else ("unchanged" if old == live["sha256"] else "CHANGED")
        out(f"  {name:<28} {live['sha256'][:12]}  {_fmt_gb(live['size_bytes']):>8}  {status}")
    return manifest


def status(manifest: dict[str, Any], ollama: Ollama, configured_chat: str, out: Any = print) -> int:
    """What's installed and whether it matches its pin. The models the tool needs are
    always listed; optional comparison models only when installed, plus a count."""
    installed = ollama.installed()
    needed = set(manifest["profiles"]["starter"]["models"]) | {configured_chat}
    bad = optional_missing = 0
    for name, entry in manifest["models"].items():
        have = installed.get(name)
        if have is None:
            if name not in needed:
                optional_missing += 1
                continue
            state = "not installed"
        elif have.removeprefix("sha256:") == entry.get("manifest_sha256"):
            state = "installed, matches pin"
        else:
            state, bad = "INSTALLED BUT DIFFERENT FROM PIN", bad + 1
        out(f"  {name:<28} {state}")
    if optional_missing:
        out(f"  ({optional_missing} optional comparison models not installed - see docs/MODELS.md)")
    if configured_chat not in manifest["models"]:
        out(
            f"  NOTE: LLM_MODEL is '{configured_chat}', which is not a pinned official model "
            "(unverified source)."
        )
    elif configured_chat not in installed:
        out(
            f"  NOTE: the configured model '{configured_chat}' is not installed yet - run "
            "Install Models.cmd (or: python -m toast.modelsetup install)"
        )
    return 3 if bad else 0


# --------------------------------------------------------------------- CLI


def _ollama_url() -> str:
    if os.environ.get("OLLAMA_URL"):
        return os.environ["OLLAMA_URL"]
    from toast.core.config import Settings

    return Settings().llm_base_url.removesuffix("/v1").removesuffix("/")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="toast.modelsetup", description=__doc__.split("\n\n")[0])
    p.add_argument("command", choices=["status", "plan", "install", "verify", "pin", "chat-model"])
    p.add_argument(
        "--profile", default="starter", help="starter | small-8gb | bakeoff-12gb | bakeoff-24gb"
    )
    p.add_argument("--model", action="append", help="install this manifest model (repeatable)")
    p.add_argument(
        "--vram-gb", type=float, help="your GPU's memory; models that won't fit are skipped"
    )
    p.add_argument("--no-smoke", action="store_true", help="skip the post-install tests")
    p.add_argument("--write", action="store_true", help="with 'pin': write the manifest file")
    args = p.parse_args(argv)
    try:
        manifest = load_manifest()
        if args.command == "pin":
            pin(manifest, Registry())
            if args.write:
                MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                print(f"wrote {MANIFEST_PATH}")
            else:
                print("(dry run - add --write to save)")
            return 0
        ollama = Ollama(_ollama_url())
        if args.command in ("status", "verify"):
            from toast.core.config import Settings

            return status(manifest, ollama, Settings().llm_model)
        profile = manifest["profiles"].get(args.profile)
        if profile is None:
            raise ModelSetupError(
                f"unknown profile '{args.profile}': {sorted(manifest['profiles'])}"
            )
        names = args.model or profile["models"]
        if args.command == "chat-model":  # the chat model a profile installs (for scripts)
            print(next(n for n in profile["models"] if manifest["models"][n]["role"] == "chat"))
            return 0
        if args.command == "plan":
            print(format_plan(plan(manifest, names, ollama.installed(), vram_gb=args.vram_gb)))
            return 0
        return install(
            manifest, names, ollama, Registry(), vram_gb=args.vram_gb, smoke=not args.no_smoke
        )
    except ModelSetupError as exc:
        print(f"models: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
