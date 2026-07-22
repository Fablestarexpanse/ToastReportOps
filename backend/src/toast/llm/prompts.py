"""Versioned prompt loader.

Prompts are markdown files in prompts/ with YAML-ish front-matter (id, version).
Placeholders like {content} are filled via safe substitution — a prompt file is
trusted repo content, but the VALUES substituted in are not; they only ever
land inside the delimited untrusted block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROMPTS_DIR_CANDIDATES = [
    Path(__file__).resolve().parents[4] / "prompts",  # repo checkout
    Path("/app/prompts"),  # container
    Path("prompts"),  # cwd fallback
]

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    template: str

    @property
    def prompt_version(self) -> str:
        return f"{self.id}.{self.version}"

    def render(self, **values: str) -> str:
        out = self.template
        for key, value in values.items():
            out = out.replace("{" + key + "}", value)
        return out


def _prompts_dir() -> Path:
    for candidate in PROMPTS_DIR_CANDIDATES:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("prompts/ directory not found")


PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


# Prompts the operator may edit from OPTIONS, with what each one controls.
# extract_claims is deliberately absent: it is the ledger's intake contract
# (and its injection posture), not a matter of writing style.
EDITABLE_PROMPTS: dict[str, dict[str, str]] = {
    "draft_update": {
        "label": "Incident report",
        "purpose": "How the newsroom writes a factual report from the ledger.",
        "version": "v1",
    },
    "rewrite_social": {
        "label": "Social rewrite",
        "purpose": "How a report is rewritten in a human voice for posting.",
        "version": "v1",
    },
    "classify_watch": {
        "label": "Watch age check",
        "purpose": "How a watch candidate is judged new vs. old.",
        "version": "v1",
    },
    "activity_check": {
        "label": "Activity check",
        "purpose": "How an active fire is judged still-burning vs done.",
        "version": "v1",
    },
    "autotriage": {
        "label": "Adjudication triage",
        "purpose": "How the AI proposes dispositions for unattributed claims.",
        "version": "v1",
    },
}


def placeholders(template: str) -> set[str]:
    """The {tokens} a template expects to be filled."""
    return set(PLACEHOLDER_RE.findall(template))


def _read_file(prompt_id: str, version: str) -> tuple[str, str, str]:
    path = _prompts_dir() / f"{prompt_id}.{version}.md"
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(text)
    if not match:
        raise ValueError(f"prompt {path.name} missing front-matter")
    meta = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    return (
        meta.get("id", prompt_id).strip(),
        meta.get("version", version).strip(),
        text[match.end() :],
    )


def load_prompt(prompt_id: str, version: str = "v1", conn: Any = None) -> Prompt:
    """The operator's edited prompt when one exists, else the shipped file."""
    file_id, file_version, body = _read_file(prompt_id, version)
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT template FROM prompt_overrides WHERE prompt_id = ? AND version = ?",
                (prompt_id, version),
            ).fetchone()
        except Exception:  # table missing on an old DB — fall back to the file
            row = None
        if row and row["template"].strip():
            # 'edited' in the recorded prompt_version so a fact's provenance
            # shows it was not produced by the shipped prompt
            return Prompt(id=file_id, version=f"{file_version}-edited", template=row["template"])
    return Prompt(id=file_id, version=file_version, template=body)


def default_template(prompt_id: str, version: str = "v1") -> str:
    return _read_file(prompt_id, version)[2]


def save_override(conn: Any, prompt_id: str, template: str) -> dict[str, Any]:
    """Store an edited prompt after checking it can still be rendered.

    A prompt that has lost a placeholder still 'works' — it just quietly stops
    receiving the facts, and the model invents the rest. That failure is
    invisible in the output, so it is refused here."""
    from toast.core.db import utcnow

    if prompt_id not in EDITABLE_PROMPTS:
        raise ValueError(f"{prompt_id!r} is not operator-editable")
    version = EDITABLE_PROMPTS[prompt_id]["version"]
    template = template.strip()
    if len(template) < 40:
        raise ValueError("template is too short to be a working prompt")

    required = placeholders(default_template(prompt_id, version))
    missing = sorted(required - placeholders(template))
    if missing:
        raise ValueError(
            "missing placeholder(s) " + ", ".join("{" + m + "}" for m in missing) + " — "
            "without them the prompt runs but never receives that data"
        )
    conn.execute(
        """INSERT INTO prompt_overrides (prompt_id, version, template, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(prompt_id, version) DO UPDATE SET
             template = excluded.template, updated_at = excluded.updated_at""",
        (prompt_id, version, template, utcnow()),
    )
    return {"prompt_id": prompt_id, "version": version, "edited": True}


def clear_override(conn: Any, prompt_id: str) -> None:
    """Revert to the shipped prompt. The file default is never destroyed."""
    conn.execute("DELETE FROM prompt_overrides WHERE prompt_id = ?", (prompt_id,))


def list_editable(conn: Any) -> list[dict[str, Any]]:
    overrides = {
        r["prompt_id"]: r
        for r in conn.execute("SELECT prompt_id, template, updated_at FROM prompt_overrides")
    }
    out = []
    for prompt_id, meta in EDITABLE_PROMPTS.items():
        version = meta["version"]
        default = default_template(prompt_id, version)
        override = overrides.get(prompt_id)
        out.append(
            {
                "id": prompt_id,
                "label": meta["label"],
                "purpose": meta["purpose"],
                "version": version,
                "edited": bool(override),
                "updated_at": override["updated_at"] if override else None,
                "template": override["template"] if override else default,
                "default_template": default,
                "placeholders": sorted(placeholders(default)),
            }
        )
    return out
