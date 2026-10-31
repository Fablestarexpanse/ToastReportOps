"""Full-text search over what Toast already holds: ledger facts, drafts, radio
transcripts and archived web pages.

A local full-text index with highlighted snippets, read through a few narrow, capped,
read-only calls. Built on SQLite FTS5, which ships inside SQLite, so there is no new
dependency and nothing to download.

What is indexed, and why that and no more:

* ``fact``  — every ledger fact (append-only, so indexed once, by ingest order);
* ``draft`` — drafts and published reports (re-indexed when edited);
* ``radio`` — scanner transcripts, once transcribed;
* ``page``  — archived HTML/text/RSS from the raw store (news articles, notices).
  Structured API feeds (JSON) are skipped: their content already became facts, and
  their ids and coordinates would bury real text in noise.

The index is a cache: it can be dropped and rebuilt from the ledger at any time
(``python -m toast.pipeline.archive_search rebuild``). It is created on first use,
not in a migration, so a SQLite build without FTS5 degrades to "search unavailable"
instead of stopping the API from starting.

Everything returned is capped (see the limits below), because what this searches is
largely text scraped from the open internet — exactly what an AI agent must not be
handed in bulk.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from toast.core.config import Settings
from toast.core.logging import get_logger

TABLE = "archive_fts"
KINDS = ("fact", "draft", "radio", "page")
PAGE_TYPES = ("text/html", "text/plain", "application/rss+xml", "application/atom+xml",
              "application/xml", "text/xml", "application/xhtml+xml")  # fmt: skip

MAX_HITS = 50
SNIPPET_CHARS = 160
MAX_READ_CHARS = 2_000
MAX_TERMS = 12
MAX_BODY_CHARS = 60_000  # per indexed document
MAX_PAGE_BYTES = 2 * 1024 * 1024
BATCH = {"fact": 2_000, "draft": 500, "radio": 500, "page": 200}

_CURSOR = "archive_fts:cursor:{}"


class SearchUnavailable(Exception):
    """This SQLite build has no FTS5."""


# ------------------------------------------------------------------ the index


def ensure_index(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            f"""CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE} USING fts5(
                   doc_id UNINDEXED, kind UNINDEXED, title, body,
                   ref UNINDEXED, at UNINDEXED, source UNINDEXED,
                   tokenize = 'porter unicode61 remove_diacritics 2')"""
        )
    except sqlite3.OperationalError as exc:
        raise SearchUnavailable(f"SQLite full-text search (FTS5) is unavailable: {exc}") from exc


def _cursor(conn: sqlite3.Connection, kind: str) -> tuple[str, str]:
    row = conn.execute(
        "SELECT payload FROM map_cache WHERE key = ?", (_CURSOR.format(kind),)
    ).fetchone()
    if row is None:
        return ("", "")
    at, key = json.loads(row["payload"])
    return (str(at), str(key))


def _save_cursor(conn: sqlite3.Connection, kind: str, at: str, key: str) -> None:
    from toast.core.db import utcnow

    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (_CURSOR.format(kind), json.dumps([at, key]), utcnow()),
    )


def _put(conn: sqlite3.Connection, doc: dict[str, Any]) -> None:
    conn.execute(f"DELETE FROM {TABLE} WHERE doc_id = ?", (doc["doc_id"],))
    conn.execute(
        f"""INSERT INTO {TABLE} (doc_id, kind, title, body, ref, at, source)
            VALUES (:doc_id, :kind, :title, :body, :ref, :at, :source)""",
        {**doc, "body": (doc["body"] or "")[:MAX_BODY_CHARS], "title": doc["title"] or ""},
    )


def _page_text(data_dir: Path, rel_path: str, media_type: str) -> str | None:
    path = (data_dir / rel_path).resolve()
    if not path.is_relative_to(data_dir.resolve()) or not path.is_file():
        return None  # never read outside the data directory
    if path.stat().st_size > MAX_PAGE_BYTES:
        return None
    raw = path.read_bytes()
    if media_type in ("text/plain",):
        return raw.decode("utf-8", errors="replace")
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "iframe"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def index_pending(conn: sqlite3.Connection, settings: Settings | None = None) -> dict[str, Any]:
    """Index whatever is new since the last run, a bounded batch per kind. `more` is
    true when a batch filled up, i.e. there may be more to do right away."""
    ensure_index(conn)
    s = settings or Settings()
    done: dict[str, Any] = {k: 0 for k in KINDS}
    more = False

    at, key = _cursor(conn, "fact")
    rows = conn.execute(
        """SELECT f.id, f.content, f.kind, f.observed_at, f.ingested_at, f.tier,
                  s.name AS source, i.canonical_name AS incident
           FROM facts f JOIN sources s ON s.id = f.source_id
           LEFT JOIN incidents i ON i.id = f.incident_id
           WHERE (f.ingested_at, f.id) > (?, ?)
           ORDER BY f.ingested_at, f.id LIMIT ?""",
        (at, key, BATCH["fact"]),
    ).fetchall()
    for r in rows:
        _put(conn, {"doc_id": f"fact:{r['id']}", "kind": "fact",
                    "title": r["incident"] or f"{r['kind']} report", "body": r["content"],
                    "ref": r["id"], "at": r["observed_at"],
                    "source": f"{r['source']} (T{r['tier']})"})  # fmt: skip
        done["fact"] += 1
    if rows:
        _save_cursor(conn, "fact", rows[-1]["ingested_at"], rows[-1]["id"])
    more |= len(rows) == BATCH["fact"]

    at, key = _cursor(conn, "draft")
    rows = conn.execute(
        """SELECT id, title, body_md, status, updated_at FROM drafts
           WHERE (updated_at, id) > (?, ?) ORDER BY updated_at, id LIMIT ?""",
        (at, key, BATCH["draft"]),
    ).fetchall()
    for r in rows:
        _put(conn, {"doc_id": f"draft:{r['id']}", "kind": "draft", "title": r["title"],
                    "body": r["body_md"], "ref": r["id"], "at": r["updated_at"],
                    "source": f"newsroom ({r['status']})"})  # fmt: skip
        done["draft"] += 1
    if rows:
        _save_cursor(conn, "draft", rows[-1]["updated_at"], rows[-1]["id"])
    more |= len(rows) == BATCH["draft"]

    # transcripts arrive after the call is captured, so "new" means "transcribed and
    # not yet in the index" rather than a time cursor
    indexed_radio = {
        r["doc_id"] for r in conn.execute(f"SELECT doc_id FROM {TABLE} WHERE kind = 'radio'")
    }
    for r in conn.execute(
        """SELECT id, system, talkgroup, transcript, started_at FROM radio_calls
           WHERE transcript IS NOT NULL AND transcript != '' ORDER BY started_at LIMIT ?""",
        (BATCH["radio"] + len(indexed_radio),),
    ):
        doc_id = f"radio:{r['id']}"
        if doc_id in indexed_radio or done["radio"] >= BATCH["radio"]:
            continue
        _put(conn, {"doc_id": doc_id, "kind": "radio",
                    "title": " ".join(x for x in (r["system"], r["talkgroup"]) if x) or "radio",
                    "body": r["transcript"], "ref": r["id"], "at": r["started_at"],
                    "source": "scanner radio"})  # fmt: skip
        done["radio"] += 1
    more |= done["radio"] == BATCH["radio"]

    at, key = _cursor(conn, "page")
    rows = conn.execute(
        """SELECT sha256, media_type, path, storage, fetched_at, url FROM raw_blobs
           WHERE (fetched_at, sha256) > (?, ?) ORDER BY fetched_at, sha256 LIMIT ?""",
        (at, key, BATCH["page"]),
    ).fetchall()
    for r in rows:
        media = (r["media_type"] or "").split(";")[0].strip().lower()
        if media not in PAGE_TYPES or r["storage"] != "local":
            continue
        try:
            text = _page_text(s.toast_data_dir, r["path"], media)
        except OSError:
            text = None
        if text:
            _put(conn, {"doc_id": f"page:{r['sha256']}", "kind": "page", "title": r["url"],
                        "body": text, "ref": r["sha256"], "at": r["fetched_at"],
                        "source": _host(r["url"])})  # fmt: skip
            done["page"] += 1
    if rows:
        _save_cursor(conn, "page", rows[-1]["fetched_at"], rows[-1]["sha256"])
    more |= len(rows) == BATCH["page"]

    conn.commit()
    if any(done.values()):
        get_logger("archive_search").info("archive_indexed", **done)
    done["more"] = more
    return done


def _host(url: str | None) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else "archive"


def rebuild(conn: sqlite3.Connection) -> None:
    """Drop the index and its cursors; the next index_pending runs start over."""
    conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    conn.execute("DELETE FROM map_cache WHERE key LIKE 'archive_fts:cursor:%'")
    conn.commit()


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        ensure_index(conn)
    except SearchUnavailable as exc:
        return {"available": False, "reason": str(exc)}
    counts = {
        r["kind"]: r["n"]
        for r in conn.execute(f"SELECT kind, COUNT(*) AS n FROM {TABLE} GROUP BY kind")
    }
    return {"available": True, "documents": {k: counts.get(k, 0) for k in KINDS}}


# -------------------------------------------------------------------- queries


def build_query(text: str) -> str:
    """Turn what a person (or an agent) typed into a safe FTS5 query.

    Every term is quoted, so FTS operators and column filters in the input are inert;
    "quoted phrases" stay phrases and a trailing * keeps prefix search. Terms are ANDed.
    """
    terms: list[str] = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', text):
        raw = phrase or word
        prefix = raw.endswith("*") and not phrase
        clean = re.sub(r"[^\w\s'-]", " ", raw, flags=re.UNICODE).strip()
        clean = " ".join(clean.split())[:60]
        if clean:
            terms.append(f'"{clean}"' + ("*" if prefix else ""))
        if len(terms) >= MAX_TERMS:
            break
    if not terms:
        raise ValueError("search needs at least one word")
    return " ".join(terms)


def search(
    conn: sqlite3.Connection, text: str, *, kind: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    ensure_index(conn)
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    query = build_query(text)
    limit = max(1, min(limit, MAX_HITS))
    params: list[Any] = [query]
    where = ""
    if kind:
        where, params = " AND kind = ?", [query, kind]
    rows = conn.execute(
        f"""SELECT doc_id, kind, title, ref, at, source,
                   snippet({TABLE}, 3, '[', ']', '…', 18) AS snippet
            FROM {TABLE} WHERE {TABLE} MATCH ?{where}
            ORDER BY bm25({TABLE}, 0, 0, 2.0, 1.0) LIMIT ?""",
        [*params, limit],
    ).fetchall()
    return [
        {
            "doc_id": r["doc_id"],
            "kind": r["kind"],
            "title": (r["title"] or "")[:120],
            "snippet": (r["snippet"] or "")[:SNIPPET_CHARS],
            "source": r["source"],
            "at": r["at"],
            "ref": r["ref"],
        }
        for r in rows
    ]


def read(
    conn: sqlite3.Connection,
    doc_id: str,
    *,
    around: str | None = None,
    max_chars: int = 1_500,
) -> dict[str, Any]:
    """A window of one indexed document — never the whole thing — centred on the
    first occurrence of `around` when given."""
    ensure_index(conn)
    row = conn.execute(
        f"SELECT doc_id, kind, title, body, ref, at, source FROM {TABLE} WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        raise KeyError(doc_id)
    body: str = row["body"] or ""
    size = max(200, min(max_chars, MAX_READ_CHARS))
    start = 0
    if around:
        for term in re.findall(r"\w[\w'-]*", around)[:MAX_TERMS]:
            hit = body.lower().find(term.lower())
            if hit != -1:
                start = max(0, hit - size // 3)
                break
    window = body[start : start + size]
    return {
        "doc_id": row["doc_id"],
        "kind": row["kind"],
        "title": (row["title"] or "")[:200],
        "source": row["source"],
        "at": row["at"],
        "ref": row["ref"],
        "offset": start,
        "total_chars": len(body),
        "truncated": start > 0 or start + size < len(body),
        "text": window,
    }


def main(argv: list[str] | None = None) -> int:
    """python -m toast.pipeline.archive_search status|index|rebuild|search <words>"""
    from toast.core.db import apply_migrations, connect

    args = argv if argv is not None else sys.argv[1:]
    s = Settings()
    conn = connect(s.toast_db_path)
    apply_migrations(conn)
    try:
        cmd = args[0] if args else "status"
        if cmd == "rebuild":
            rebuild(conn)
            print("index dropped; rebuilding in batches:")
            total = {k: 0 for k in KINDS}
            while True:
                got = index_pending(conn, s)
                total = {k: total[k] + got[k] for k in KINDS}
                if not got["more"]:
                    break
            print(json.dumps(total))
        elif cmd == "index":
            print(json.dumps(index_pending(conn, s)))
        elif cmd == "search":
            print(json.dumps(search(conn, " ".join(args[1:])), indent=1, ensure_ascii=False))
        else:
            print(json.dumps(status(conn)))
    except (SearchUnavailable, ValueError) as exc:
        print(f"archive search: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
