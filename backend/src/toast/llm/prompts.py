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


def load_prompt(prompt_id: str, version: str = "v1") -> Prompt:
    path = _prompts_dir() / f"{prompt_id}.{version}.md"
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(text)
    if not match:
        raise ValueError(f"prompt {path.name} missing front-matter")
    body = text[match.end() :]
    meta = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    return Prompt(
        id=meta.get("id", prompt_id).strip(),
        version=meta.get("version", version).strip(),
        template=body,
    )
