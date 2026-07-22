"""The last gate before rendered markdown reaches a browser.

python-markdown passes RAW HTML in its input straight through to its output —
so a hostile article whose text survives into a draft body (via the LLM
echoing a quote or a fire name) would become live markup in the newsroom
preview and on the published site. Everything we render is built FROM
ingested content; sanitize at the boundary, every time.
"""

from __future__ import annotations

import nh3


def clean_html(html: str) -> str:
    """Strip scripts, event handlers and dangerous tags; keep honest markup.
    nh3 (ammonia) defaults allow the usual article set: p, headings, lists,
    tables, links (href sanitized), em/strong, blockquote, code."""
    return nh3.clean(html)
