"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Draft,
  DraftDetail,
  Incident,
  ResearchJob,
  cancelResearch,
  createSocialDraft,
  fetchDraft,
  fetchDrafts,
  fetchIncidents,
  generateDraft,
  saveDraft,
  transitionDraft,
} from "@/lib/api";
import { C, TIER, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

const statusColor = (s: string) =>
  s.startsWith("published") ? C.ok : s === "needs_review" ? C.warn : s === "approved" ? C.blue : s === "rejected" ? C.evac : C.dim;

const KIND_BADGE: Record<string, [string, string]> = {
  update: ["UPDATE", C.ember],
  analysis: ["RESEARCH", C.violet],
  social: ["SOCIAL", C.blue],
  summary: ["SUMMARY", C.dim],
  correction: ["CORRECTION", C.evac],
};

const WINDOWS: [string, number | null][] = [
  ["Full incident", null],
  ["Last 24h", 24],
  ["Last 3h", 3],
  ["Last hour", 1],
];

export default function NewsroomMode({ toast }: { toast: (m: string) => void }) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [research, setResearch] = useState<ResearchJob[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DraftDetail | null>(null);
  const [genIncident, setGenIncident] = useState<string>("");
  const [genWindow, setGenWindow] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [socialBusy, setSocialBusy] = useState(false);
  const [bodyEdit, setBodyEdit] = useState("");
  const [titleEdit, setTitleEdit] = useState("");

  const refresh = useCallback(async () => {
    // A failed poll must never take the screen away. Before this, one network
    // blip threw out of the effect and Next replaced the whole workstation with
    // an error page — on a tool whose operating condition is PSPS outages and
    // flaky links. Keep whatever we last had; the top bar says it is stale.
    let d, inc;
    try {
      [d, inc] = await Promise.all([fetchDrafts(), fetchIncidents()]);
    } catch {
      return;
    }
    // defensively dedupe by id — a transient duplicate from overlapping
    // fetches must never produce duplicate React keys
    const seen = new Set<string>();
    const unique = inc.filter((i) => !seen.has(i.id) && seen.add(i.id) !== undefined);
    setDrafts(d.drafts);
    setResearch(d.research_jobs ?? []);
    setIncidents(unique);
    // functional update: no dependency on genIncident, so refresh keeps a
    // stable identity and the mount effect runs exactly once (the previous
    // genIncident dep re-created refresh mid-flight → overlapping fetches)
    setGenIncident((prev) => prev || (unique.length ? unique[0].id : prev));
  }, []);

  useEffect(() => {
    refresh();
    // keep the queue live: in-flight research entries update, and finished
    // research pops in as a draft without a manual reload
    const id = setInterval(refresh, 8000);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (!selId) {
      setDetail(null);
      return;
    }
    fetchDraft(selId).then((d) => {
      setDetail(d);
      setBodyEdit(d.draft.body_md);
      setTitleEdit(d.draft.title ?? "");
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
    await saveDraft(detail.draft.id, { body_md: bodyEdit, title: titleEdit || undefined });
    toast("saved");
    refresh();
  };

  const copyBody = async () => {
    await navigator.clipboard.writeText(bodyEdit);
    toast("copied to clipboard");
  };

  const makeSocial = async () => {
    if (!detail || socialBusy) return;
    setSocialBusy(true);
    try {
      const r = await createSocialDraft(detail.draft.id);
      toast(`social version drafted (${r.post_chars} chars + ${r.short_chars}-char short)`);
      await refresh();
      setSelId(r.draft_id);
    } catch (e) {
      toast(`social rewrite failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setSocialBusy(false);
    }
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
          ? [["published", "MARK RELEASED ✓", C.ok]]
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
          {research.map((r) => {
            const cfg =
              r.state === "failed"
                ? { color: C.evac, icon: "✕", label: "RESEARCH FAILED" }
                : r.state === "parked"
                  ? { color: C.warn, icon: "⏸", label: "RESEARCH PARKED — rate limited, retrying" }
                  : { color: C.violet, icon: "⌕", label: r.state === "starting" ? "RESEARCH STARTING" : "RESEARCHING…" };
            return (
              <div
                key={r.key}
                style={{
                  padding: "9px 12px",
                  borderLeft: `3px solid ${cfg.color}`,
                  background: cfg.color + "0d",
                  borderBottom: `1px solid ${C.line}55`,
                }}
                title={r.error ?? r.query}
              >
                <div style={{ display: "flex", gap: 7, alignItems: "center" }}>
                  <span
                    className={r.state === "failed" ? undefined : "tr-blink"}
                    style={{ color: cfg.color, fontSize: 13 }}
                  >
                    {cfg.icon}
                  </span>
                  <span style={{ fontSize: 11.5, fontWeight: 600, lineHeight: 1.3, minWidth: 0, flex: 1 }}>
                    {r.incident_name ? `${r.incident_name} — deep research` : r.query.slice(0, 60)}
                  </span>
                  <span
                    onClick={async (e) => {
                      e.stopPropagation();
                      try {
                        await cancelResearch(r.key);
                        toast(r.state === "failed" ? "dismissed" : "research cancelled");
                        refresh();
                      } catch (err) {
                        toast(`cancel failed: ${err instanceof Error ? err.message : err}`);
                      }
                    }}
                    title={r.state === "failed" ? "dismiss" : "cancel this research run"}
                    style={{ color: C.faint, cursor: "pointer", fontSize: 12, padding: "0 3px" }}
                  >
                    ✕
                  </span>
                </div>
                <div style={{ fontFamily: font.mono, fontSize: 8.5, color: cfg.color, marginTop: 3 }}>
                  {cfg.label}
                  <span style={{ color: C.faint }}>
                    {" "}
                    · running {age(r.since)} · since {fmtStamp(r.since)} · lands here when done
                  </span>
                </div>
                {r.error && (
                  <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 2 }}>
                    {r.error}
                  </div>
                )}
              </div>
            );
          })}
          {drafts.map((dr) => (
            <div
              key={dr.id}
              onClick={() => setSelId(dr.id)}
              className="tr-row"
              style={{ padding: "9px 12px", borderLeft: `3px solid ${dr.id === selId ? C.ember : "transparent"}`, background: dr.id === selId ? C.panelUp : undefined, cursor: "pointer", borderBottom: `1px solid ${C.line}55` }}
            >
              <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                <span
                  style={{
                    fontFamily: font.mono,
                    fontSize: 7.5,
                    fontWeight: 700,
                    letterSpacing: 0.5,
                    color: (KIND_BADGE[dr.kind] ?? KIND_BADGE.update)[1],
                    border: `1px solid ${(KIND_BADGE[dr.kind] ?? KIND_BADGE.update)[1]}55`,
                    borderRadius: 3,
                    padding: "1px 4px",
                    flexShrink: 0,
                  }}
                >
                  {(KIND_BADGE[dr.kind] ?? KIND_BADGE.update)[0]}
                </span>
                <span style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.3, minWidth: 0 }}>
                  {dr.title || "untitled"}
                </span>
              </div>
              <div style={{ fontFamily: font.mono, fontSize: 9, marginTop: 3, display: "flex", gap: 8 }}>
                <span style={{ color: statusColor(dr.status) }}>{dr.status.toUpperCase().replace("_", " ")}</span>
                <span style={{ color: C.faint }}>{dr.incident_name}</span>
                <span style={{ color: C.faint, marginLeft: "auto" }}>{dr.cited_count} cited</span>
              </div>
            </div>
          ))}
          {!drafts.length && !research.length && (
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
              <input
                value={titleEdit}
                onChange={(e) => setTitleEdit(e.target.value)}
                readOnly={d.status === "published"}
                style={{ fontFamily: font.disp, fontSize: 16, fontWeight: 700, flex: 1, minWidth: 0, background: "transparent", border: "none", outline: "none", color: C.txt, borderBottom: `1px dashed transparent` }}
                onFocus={(e) => (e.target.style.borderBottom = `1px dashed ${C.line}`)}
                onBlur={(e) => (e.target.style.borderBottom = "1px dashed transparent")}
              />
              <span style={{ fontFamily: font.mono, fontSize: 9, color: statusColor(d.status), border: `1px solid ${statusColor(d.status)}`, borderRadius: 3, padding: "2px 7px", flexShrink: 0 }}>
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
            <div style={{ padding: "10px 16px", borderTop: `1px solid ${C.line}`, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button onClick={save} disabled={d.status === "published"} style={{ background: C.panelUp, color: C.txt, border: `1px solid ${C.line}`, borderRadius: 5, fontFamily: font.disp, fontWeight: 600, fontSize: 12, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}>
                SAVE
              </button>
              <button onClick={copyBody} title="copy the full text to clipboard" style={{ background: C.panelUp, color: C.txt, border: `1px solid ${C.line}`, borderRadius: 5, fontFamily: font.disp, fontWeight: 600, fontSize: 12, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}>
                ⧉ COPY
              </button>
              {d.kind !== "social" && (
                <button onClick={makeSocial} title="rewrite in a human voice for posting (full + X-length)" style={{ background: C.panelUp, color: C.blue, border: `1px solid ${C.blue}55`, borderRadius: 5, fontFamily: font.disp, fontWeight: 600, fontSize: 12, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}>
                  {socialBusy ? "…" : "✍ SOCIAL VERSION"}
                </button>
              )}
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

        {/* Delivery outcomes. 'unknown' is the one that matters: the post may
            have landed and we never heard back, so re-sending risks a duplicate
            on a public account. Only a human who has looked can resolve it. */}
        {!!detail?.publications?.length && (
          <div style={{ padding: "8px 13px", borderBottom: `1px solid ${C.line}` }}>
            {detail.publications.map((p) => {
              const color =
                p.status === "sent" ? C.ok
                : p.status === "unknown" ? C.warn
                : p.status === "failed" ? C.evac
                : C.dim;
              return (
                <div
                  key={p.id}
                  title={p.status === "unknown"
                    ? "delivery indeterminate — check the account before re-sending"
                    : p.receipt ?? undefined}
                  style={{ display: "flex", gap: 6, alignItems: "center", fontFamily: font.mono, fontSize: 9, marginTop: 3 }}
                >
                  <span style={{ width: 6, height: 6, borderRadius: 3, background: color }} />
                  <span style={{ color: C.dim, minWidth: 58 }}>{p.channel.toUpperCase()}</span>
                  <span style={{ color }}>
                    {p.status === "unknown" ? "UNKNOWN — VERIFY MANUALLY" : p.status.toUpperCase()}
                  </span>
                  {p.external_url && (
                    <a href={p.external_url} target="_blank" rel="noopener noreferrer" style={{ color: C.blue }}>⧉</a>
                  )}
                </div>
              );
            })}
          </div>
        )}
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
          <span style={{ color: C.txt }}>Website — manual.</span> Nothing is pushed to any site.
          Use <span style={{ color: C.blue }}>⧉ COPY</span> and paste into your own site;
          “MARK RELEASED” only records that you cleared it.
          <div style={{ marginTop: 6 }}>
            <span style={{ color: C.violet }}>Social</span> — a SOCIAL draft posts automatically on
            release, but only to channels whose keys are set in OPTIONS → API KEYS. With none set,
            nothing leaves this machine.
          </div>
        </div>
      </div>
    </div>
  );
}
