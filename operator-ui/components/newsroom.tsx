"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Draft,
  DraftDetail,
  Incident,
  fetchDraft,
  fetchDrafts,
  fetchIncidents,
  generateDraft,
  saveDraft,
  transitionDraft,
} from "@/lib/api";
import { C, TIER, font } from "@/lib/theme";

const statusColor = (s: string) =>
  s.startsWith("published") ? C.ok : s === "needs_review" ? C.warn : s === "approved" ? C.blue : s === "rejected" ? C.evac : C.dim;

const WINDOWS: [string, number | null][] = [
  ["Full incident", null],
  ["Last 24h", 24],
  ["Last 3h", 3],
  ["Last hour", 1],
];

export default function NewsroomMode({ toast }: { toast: (m: string) => void }) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DraftDetail | null>(null);
  const [genIncident, setGenIncident] = useState<string>("");
  const [genWindow, setGenWindow] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [bodyEdit, setBodyEdit] = useState("");

  const refresh = useCallback(async () => {
    const [d, inc] = await Promise.all([fetchDrafts(), fetchIncidents()]);
    setDrafts(d);
    setIncidents(inc);
    if (!genIncident && inc.length) setGenIncident(inc[0].id);
  }, [genIncident]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!selId) {
      setDetail(null);
      return;
    }
    fetchDraft(selId).then((d) => {
      setDetail(d);
      setBodyEdit(d.draft.body_md);
    });
  }, [selId]);

  const generate = async () => {
    if (!genIncident || busy) return;
    setBusy(true);
    try {
      const w: { window_start?: string } = {};
      if (genWindow) w.window_start = new Date(Date.now() - genWindow * 3600_000).toISOString();
      const r = await generateDraft(genIncident, w);
      toast(`draft generated — ${r.facts_used} facts, ${r.cited} cited`);
      await refresh();
      setSelId(r.draft_id);
    } catch (e) {
      toast(`draft failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!detail) return;
    await saveDraft(detail.draft.id, { body_md: bodyEdit });
    toast("saved");
    refresh();
  };

  const advance = async (status: string) => {
    if (!detail) return;
    try {
      await transitionDraft(detail.draft.id, status);
      toast(`draft → ${status}`);
      const d = await fetchDraft(detail.draft.id);
      setDetail(d);
      refresh();
    } catch (e) {
      toast(`${e instanceof Error ? e.message : e}`);
    }
  };

  const d = detail?.draft;
  const nextActions: [string, string, string][] = d
    ? d.status === "draft"
      ? [["needs_review", "SUBMIT FOR REVIEW", C.warn]]
      : d.status === "needs_review"
        ? [["approved", "APPROVE ✓", C.ok], ["draft", "SEND BACK", C.dim]]
        : d.status === "approved"
          ? [["published", "PUBLISH →", C.ok]]
          : []
    : [];

  return (
    <div style={{ flex: 1, display: "grid", gridTemplateColumns: "300px 1fr 280px", minHeight: 0 }}>
      {/* draft queue + generate */}
      <div style={{ borderRight: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: 10, borderBottom: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, marginBottom: 6 }}>
            GENERATE UPDATE
          </div>
          <select
            value={genIncident}
            onChange={(e) => setGenIncident(e.target.value)}
            style={{ width: "100%", background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.txt, fontFamily: font.mono, fontSize: 10.5, padding: "6px 8px", marginBottom: 6 }}
          >
            {incidents.map((i) => (
              <option key={i.id} value={i.id}>
                {i.canonical_name} · {i.fact_count} facts
              </option>
            ))}
          </select>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
            {WINDOWS.map(([label, hrs]) => (
              <span
                key={label}
                onClick={() => setGenWindow(hrs)}
                style={{ fontFamily: font.mono, fontSize: 8.5, padding: "2px 7px", borderRadius: 3, cursor: "pointer", border: `1px solid ${genWindow === hrs ? C.ember : C.line}`, color: genWindow === hrs ? C.ember : C.dim }}
              >
                {label}
              </span>
            ))}
          </div>
          <button onClick={generate} style={{ width: "100%", background: busy ? C.faint : C.ember, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 12, letterSpacing: 1, padding: "7px 0", cursor: "pointer" }}>
            {busy ? "drafting…" : "✎ DRAFT FROM LEDGER"}
          </button>
        </div>
        <div style={{ padding: "8px 12px 4px", fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim }}>
          DRAFT QUEUE
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {drafts.map((dr) => (
            <div
              key={dr.id}
              onClick={() => setSelId(dr.id)}
              className="tr-row"
              style={{ padding: "9px 12px", borderLeft: `3px solid ${dr.id === selId ? C.ember : "transparent"}`, background: dr.id === selId ? C.panelUp : undefined, cursor: "pointer", borderBottom: `1px solid ${C.line}55` }}
            >
              <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.3 }}>{dr.title || "untitled"}</div>
              <div style={{ fontFamily: font.mono, fontSize: 9, marginTop: 3, display: "flex", gap: 8 }}>
                <span style={{ color: statusColor(dr.status) }}>{dr.status.toUpperCase().replace("_", " ")}</span>
                <span style={{ color: C.faint }}>{dr.incident_name}</span>
                <span style={{ color: C.faint, marginLeft: "auto" }}>{dr.cited_count} cited</span>
              </div>
            </div>
          ))}
          {!drafts.length && (
            <div style={{ padding: 14, fontFamily: font.mono, fontSize: 10, color: C.faint }}>
              no drafts yet — generate one above
            </div>
          )}
        </div>
      </div>

      {/* editor */}
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0, background: C.bg }}>
        {!d && (
          <div style={{ padding: 24, fontFamily: font.mono, fontSize: 11, color: C.faint }}>
            select a draft, or generate one from an incident&apos;s ledger.
          </div>
        )}
        {d && (
          <>
            <div style={{ padding: "10px 16px", borderBottom: `1px solid ${C.line}`, display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontFamily: font.disp, fontSize: 16, fontWeight: 700, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {d.title}
              </span>
              <span style={{ fontFamily: font.mono, fontSize: 9, color: statusColor(d.status), border: `1px solid ${statusColor(d.status)}`, borderRadius: 3, padding: "2px 7px" }}>
                {d.status.toUpperCase().replace("_", " ")}
              </span>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: "14px 18px" }}>
              <textarea
                value={bodyEdit}
                onChange={(e) => setBodyEdit(e.target.value)}
                readOnly={d.status === "published"}
                spellCheck={false}
                style={{ width: "100%", minHeight: 300, background: "transparent", border: "none", outline: "none", resize: "vertical", color: C.txt, fontFamily: font.body, fontSize: 13.5, lineHeight: 1.65 }}
              />
              <div style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, marginTop: 6 }}>
                [#id] citations reference the ledger facts below · evacuation text is verbatim from officials
              </div>
            </div>
            <div style={{ padding: "10px 16px", borderTop: `1px solid ${C.line}`, display: "flex", gap: 8, alignItems: "center" }}>
              <button onClick={save} disabled={d.status === "published"} style={{ background: C.panelUp, color: C.txt, border: `1px solid ${C.line}`, borderRadius: 5, fontFamily: font.disp, fontWeight: 600, fontSize: 12, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}>
                SAVE
              </button>
              <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint }}>AI drafts · you approve · nothing publishes itself</span>
              {nextActions.map(([status, label, color]) => (
                <button
                  key={status}
                  onClick={() => advance(status)}
                  style={{ marginLeft: status === nextActions[0][0] ? "auto" : 0, background: color, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 12, letterSpacing: 1, padding: "7px 16px", cursor: "pointer" }}
                >
                  {label}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* cited facts */}
      <div style={{ borderLeft: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: "10px 13px", fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, borderBottom: `1px solid ${C.line}` }}>
          CITED FACTS {detail ? `· ${detail.cited_facts.length}` : ""}
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {(detail?.cited_facts ?? []).map((f) => (
            <div key={f.id} style={{ padding: "7px 11px", borderBottom: `1px solid ${C.line}44`, display: "flex", gap: 7 }}>
              <div style={{ width: 3, borderRadius: 2, background: TIER[f.tier], flexShrink: 0 }} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 11 }}>{f.content}</div>
                <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 2 }}>
                  {f.kind} · T{f.tier} · {f.source_name}
                </div>
              </div>
            </div>
          ))}
          {detail && !detail.cited_facts.length && (
            <div style={{ padding: 14, fontFamily: font.mono, fontSize: 9.5, color: C.faint }}>
              no citations on this draft
            </div>
          )}
        </div>
        <div style={{ padding: "10px 13px", fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, borderTop: `1px solid ${C.line}` }}>
          DISTRIBUTION
        </div>
        <div style={{ padding: "8px 13px 14px", fontFamily: font.mono, fontSize: 9.5, color: C.faint, lineHeight: 1.6 }}>
          Channel publishing (site · X · Facebook · RSS · webhook) needs your credentials + the
          Cloudflare Pages / FB app setup — wired in once those are provided. Approve is the final
          step for now.
        </div>
      </div>
    </div>
  );
}
