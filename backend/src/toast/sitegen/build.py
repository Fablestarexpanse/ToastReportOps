"""Static public-site generator.

Renders published reports + active incidents into plain static HTML — no server,
no JS required, zero attack surface (the mission's public-facing guarantee). The
output directory is a local preview the operator reads and copies from by hand;
nothing deploys it (deploy step removed
until a token is provided).

Every report carries a burned-in timestamp and a "verify with officials" notice;
report bodies (including verbatim evacuation text) are rendered as-published.
"""

from __future__ import annotations

import html
import sqlite3
from pathlib import Path

import markdown as md

from toast.core.db import utcnow
from toast.core.sanitize import clean_html

SITE_TITLE = "Toast Report OPs"
SITE_TAGLINE = "California wildfire updates — verified, sourced, human-reviewed"

_CSS = """
:root{--bg:#faf8f5;--ink:#1c2126;--muted:#6b7480;--line:#e3ddd3;--ember:#c2410c;--warn:#b45309}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
a{color:var(--ember);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:720px;margin:0 auto;padding:0 20px}
header{border-bottom:2px solid var(--ember);background:#fff}
header .wrap{padding:18px 20px}
.brand{font-weight:800;font-size:22px;letter-spacing:.5px;color:var(--ember)}
.tag{color:var(--muted);font-size:13px;margin-top:2px}
.notice{background:#fff7ed;border:1px solid #fed7aa;color:var(--warn);
  border-radius:8px;padding:10px 14px;font-size:13px;margin:16px 0}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;
  padding:16px 18px;margin:14px 0}
.card h2{margin:.1em 0 .2em;font-size:19px}
.meta{color:var(--muted);font-size:13px}
.report-body{margin-top:14px}
.report-body h1,.report-body h2{font-size:18px}
.sources{margin-top:18px;border-top:1px solid var(--line);padding-top:12px;
  font-size:13px;color:var(--muted)}
.sources ol{margin:.4em 0;padding-left:1.3em}
.badge{display:inline-block;font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.5px;padding:2px 8px;border-radius:99px;background:#fee2d5;color:var(--ember)}
.inc{display:flex;justify-content:space-between;gap:10px;padding:8px 0;
  border-bottom:1px solid var(--line);font-size:14px}
.inc:last-child{border-bottom:none}
footer{color:var(--muted);font-size:12px;text-align:center;padding:30px 0}
.stamp{font-variant-numeric:tabular-nums}
""".strip()


def _page(title: str, body: str, *, back: bool = False) -> str:
    nav = '<p><a href="../index.html">← all updates</a></p>' if back else ""
    logo_href = "../logo.png" if back else "./logo.png"
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="icon" type="image/png" href="{logo_href}">
<style>{_CSS}</style>
</head><body>
<header><div class="wrap">
  <div class="brand"><img src="{logo_href}" alt="" style="height:68px;width:68px;
    vertical-align:middle;margin-right:9px">{SITE_TITLE}</div>
  <div class="tag">{SITE_TAGLINE}</div>
</div></header>
<main class="wrap">
<div class="notice">⚠ Always verify evacuation and safety information with official
sources (CAL FIRE, your county, and local law enforcement). This site aggregates
and summarizes; officials are authoritative.</div>
{nav}
{body}
</main>
<footer>Toast Report OPs · generated {utcnow()[:16].replace("T", " ")} UTC ·
static site, no tracking</footer>
</body></html>"""


def _stamp(iso: str | None) -> str:
    return (iso or "")[:16].replace("T", " ") + " UTC" if iso else "—"


def _report_html(draft: sqlite3.Row, incident_name: str | None) -> str:
    body_html = clean_html(md.markdown(draft["body_md"] or "", extensions=["extra"]))
    inc = f" · {html.escape(incident_name)}" if incident_name else ""
    return _page(
        draft["title"] or "Update",
        f"""<article class="card">
  <span class="badge">Update</span>
  <h2>{html.escape(draft["title"] or "Update")}</h2>
  <div class="meta stamp">Published {_stamp(draft["approved_at"] or draft["updated_at"])}{inc}</div>
  <div class="report-body">{body_html}</div>
</article>""",
        back=True,
    )


def _index_html(reports: list[sqlite3.Row], incidents: list[sqlite3.Row]) -> str:
    items = []
    for r in reports:
        items.append(
            f"""<article class="card">
  <h2><a href="./report/{r["id"]}.html">{html.escape(r["title"] or "Update")}</a></h2>
  <div class="meta stamp">Published {_stamp(r["approved_at"] or r["updated_at"])}
    {(" · " + html.escape(r["incident_name"])) if r["incident_name"] else ""}</div>
</article>"""
        )
    reports_block = "\n".join(items) or '<p class="meta">No published updates yet.</p>'

    def _inc_meta(i: sqlite3.Row) -> str:
        parts = [html.escape(i["status"])]
        if i["acreage"]:
            parts.append(f"{i['acreage']:,.0f} ac")
        if i["containment_pct"] is not None:
            parts.append(f"{int(i['containment_pct'])}% contained")
        return " · ".join(parts)

    inc_rows = "\n".join(
        f"""<div class="inc"><span>{html.escape(i["canonical_name"])}
        {(" · " + html.escape(i["county"]) + " County") if i["county"] else ""}</span>
        <span class="meta">{_inc_meta(i)}</span></div>"""
        for i in incidents
    )
    incidents_block = (
        f'<div class="card"><h2>Active incidents</h2>{inc_rows}</div>' if incidents else ""
    )
    return _page(
        f"{SITE_TITLE} — California wildfire updates",
        f"<h1 style='font-size:20px'>Latest updates</h1>{reports_block}{incidents_block}",
    )


def build_site(conn: sqlite3.Connection, out_dir: Path) -> dict[str, int]:
    """Regenerate the whole static site. Returns counts."""
    out_dir = Path(out_dir)
    (out_dir / "report").mkdir(parents=True, exist_ok=True)
    logo = Path(__file__).resolve().parents[1] / "assets" / "ops-badge.png"
    if logo.exists():
        (out_dir / "logo.png").write_bytes(logo.read_bytes())

    reports = conn.execute(
        """SELECT d.*, i.canonical_name AS incident_name
           FROM drafts d LEFT JOIN incidents i ON i.id = d.incident_id
           WHERE d.status = 'published'
           ORDER BY COALESCE(d.approved_at, d.updated_at) DESC"""
    ).fetchall()
    incidents = conn.execute(
        """SELECT i.canonical_name, i.status, i.county,
                  (SELECT acreage FROM facts v WHERE v.incident_id = i.id AND v.kind='acreage'
                   AND v.id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
                   ORDER BY (v.observed_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) ASC,
                     (v.tier >= 3) ASC, v.observed_at DESC LIMIT 1) AS acreage,
                  (SELECT containment_pct FROM facts v WHERE v.incident_id = i.id
                   AND v.kind='containment'
                   AND v.id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
                   ORDER BY (v.observed_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) ASC,
                     (v.tier >= 3) ASC, v.observed_at DESC LIMIT 1) AS containment_pct
           FROM incidents i
           WHERE i.status IN ('active','contained')
             AND COALESCE(i.incident_type,'WF') != 'RX'
           ORDER BY i.updated_at DESC"""
    ).fetchall()

    (out_dir / "index.html").write_text(_index_html(reports, incidents), encoding="utf-8")
    for r in reports:
        (out_dir / "report" / f"{r['id']}.html").write_text(
            _report_html(r, r["incident_name"]), encoding="utf-8"
        )
    return {"reports": len(reports), "incidents": len(incidents)}
