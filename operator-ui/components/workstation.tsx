"use client";

import { ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import {
  AdjudicationItem,
  Fact,
  FireSearchResult,
  Incident,
  IncidentWeather,
  OriginCheck,
  Note,
  ProbeResult,
  Source,
  SourceSuggestion,
  TriageState,
  addNote,
  analyzeUrl,
  approveSuggestion,
  createSource,
  deleteNote,
  dismissSuggestion,
  fetchAdjudication,
  fetchHealth,
  fetchIncidentFacts,
  fetchIncidents,
  fetchIntake,
  fetchIntel,
  fetchOriginCheck,
  fetchWeather,
  fetchNotes,
  fetchSourceSuggestions,
  fetchSources,
  fetchTimeline,
  followIncident,
  hasActiveEvac,
  importFire,
  dismissTierSuggestion,
  probeSource,
  recomputeSourceStats,
  resolveAdjudication,
  searchFires,
  setSourceTier,
  starFact,
  startResearch,
  toggleNote,
  triageBulk,
  triageFact,
} from "@/lib/api";
import { C, TIER, TIER_TITLES, confColor, font } from "@/lib/theme";
import AlertBell from "@/components/alerts";
import AnalyticsMode from "@/components/analytics";
import SettingsPanel, { loadDisplayPrefs, syncDisplayPrefs } from "@/components/settings";
import { age, ageColor, fmtFull, fmtStamp, lag } from "@/lib/time";
import MapPanel from "@/components/map-panel";
import NewsroomMode from "@/components/newsroom";
import WatchMode from "@/components/watch";

const POLL_MS = 5000;

function usePolling<T>(fn: () => Promise<T>, deps: unknown[] = []): T | null {
  const [data, setData] = useState<T | null>(null);
  const cb = useCallback(fn, deps); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let live = true;
    const tick = () => cb().then((d) => live && setData(d)).catch(() => {});
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [cb]);
  return data;
}

const fmtTime = fmtStamp;
const fmtDate = fmtFull;

/* ---------------- shared bits ---------------- */

function Chip({
  children,
  color = C.dim,
  active = false,
  onClick,
  title,
}: {
  children: React.ReactNode;
  color?: string;
  active?: boolean;
  onClick?: () => void;
  title?: string;
}) {
  return (
    <span
      onClick={onClick}
      title={title}
      style={{
        fontFamily: font.mono,
        fontSize: 9,
        padding: "2px 7px",
        borderRadius: 3,
        border: `1px solid ${active ? color : C.line}`,
        color: active ? color : C.dim,
        cursor: onClick ? "pointer" : "default",
        display: "inline-block",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

/* Topic tags — derived from the fact's own kind and wording, never invented.
   They exist to make the stream scannable, so they only ever restate what the
   claim already says. */
const TAG_RULES: [RegExp, string][] = [
  [/\bevacuat/i, "EVACUATION"],
  [/\b(structure|home|building|residence)s?\b/i, "STRUCTURES"],
  [/\b(highway|hwy|road|route|interstate|closure)\b/i, "ROADS"],
  [/\b(air ?tanker|helicopter|aircraft|air attack|retardant)\b/i, "AIRCRAFT"],
  [/\b(engine|crew|dozer|personnel|firefighter)s?\b/i, "RESOURCES"],
  [/\b(spot|ember|spotting)\b/i, "SPOTTING"],
  [/\bred flag|wind|gust|humidity\b/i, "WEATHER"],
  [/\blightning\b/i, "LIGHTNING"],
  [/\b(injur|fatal|death|casualt)/i, "CASUALTIES"],
  [/\bpower|psps|utility\b/i, "POWER"],
];

function factTags(f: Fact): string[] {
  const tags = new Set<string>();
  const kindTag = { evac: "EVACUATION", road: "ROADS", resource: "RESOURCES", weather: "WEATHER" }[
    f.kind
  ];
  if (kindTag) tags.add(kindTag);
  for (const [re, tag] of TAG_RULES) if (re.test(f.content)) tags.add(tag);
  return [...tags].slice(0, 3);
}

function FactRow({
  f,
  showIncident,
  onStar,
}: {
  f: Fact;
  showIncident?: boolean;
  onStar?: (f: Fact) => void;
}) {
  const superseded = f.superseded_by != null;
  const tags = factTags(f);
  const openSource = () => {
    if (f.source_url) window.open(f.source_url, "_blank", "noopener,noreferrer");
  };
  const lagged = lag(f.observed_at, f.ingested_at);
  return (
    <div
      className="tr-row"
      onClick={f.source_url ? openSource : undefined}
      title={
        f.source_url
          ? `open source: ${f.source_url}\n\nPUBLISHED ${fmtFull(f.observed_at)}\nRECEIVED  ${fmtFull(f.ingested_at)}`
          : `PUBLISHED ${fmtFull(f.observed_at)}\nRECEIVED  ${fmtFull(f.ingested_at)}`
      }
      style={{
        padding: "7px 10px",
        borderBottom: `1px solid ${C.line}55`,
        display: "flex",
        gap: 8,
        opacity: superseded ? 0.55 : 1,
        cursor: f.source_url ? "pointer" : "default",
      }}
    >
      <div style={{ width: 3, borderRadius: 2, background: TIER[f.tier], flexShrink: 0 }} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            fontFamily: font.mono,
            fontSize: 9.5,
            color: C.dim,
            display: "flex",
            gap: 6,
          }}
        >
          <span style={{ color: TIER[f.tier] }} title={TIER_TITLES[f.tier]}>
            T{f.tier}
          </span>
          <span
            title={`published ${fmtFull(f.observed_at)} — age ${age(f.observed_at)}`}
            style={{ color: ageColor(f.observed_at, C) }}
          >
            {age(f.observed_at)} old
          </span>
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {f.source_name}
            {f.source_url && <span style={{ color: C.blue }}> ⧉</span>}
          </span>
          {showIncident && f.incident_name && (
            <span style={{ color: C.ember }}>{f.incident_name}</span>
          )}
          {showIncident && f.incident_county && (
            <span style={{ color: C.faint }}>{f.incident_county}</span>
          )}
          <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            {showIncident && (
              <span
                title={f.supersedes ? "updates a prior value via supersession" : "first report of this fact"}
                style={{
                  fontSize: 7.5,
                  letterSpacing: 1,
                  color: f.supersedes ? C.blue : C.ok,
                  border: `1px solid ${f.supersedes ? C.blue : C.ok}55`,
                  borderRadius: 3,
                  padding: "0px 4px",
                }}
              >
                {f.supersedes ? "UPDATE" : "NEW"}
              </span>
            )}
            <span style={{ color: confColor(f.confidence) }}>{Math.round(f.confidence * 100)}%</span>
          </span>
        </div>
        <div
          style={{
            fontSize: 11.5,
            marginTop: 2,
            textDecoration: superseded ? "line-through" : "none",
          }}
        >
          {f.content}
        </div>
        {/* bi-temporal: when it happened vs when we got it */}
        <div style={{ fontFamily: font.mono, fontSize: 8.5, marginTop: 3, display: "flex", gap: 10, color: C.faint }}>
          <span title={fmtFull(f.observed_at)}>
            PUB <span style={{ color: C.dim }}>{fmtStamp(f.observed_at)}</span>
          </span>
          <span title={fmtFull(f.ingested_at)}>
            RCV <span style={{ color: C.dim }}>{fmtStamp(f.ingested_at)}</span>
          </span>
          {lagged && (
            <span style={{ color: f.stale ? C.warn : C.faint }} title="delay between publication and our receipt">
              +{lagged} lag
            </span>
          )}
        </div>
        {tags.length > 0 && (
          <div style={{ display: "flex", gap: 4, marginTop: 3, flexWrap: "wrap" }}>
            {tags.map((tag) => (
              <span
                key={tag}
                style={{
                  fontFamily: font.mono,
                  fontSize: 7.5,
                  letterSpacing: 0.5,
                  color: tag === "EVACUATION" ? C.evac : C.dim,
                  border: `1px solid ${tag === "EVACUATION" ? C.evac + "66" : C.line}`,
                  borderRadius: 3,
                  padding: "0 4px",
                }}
              >
                #{tag}
              </span>
            ))}
          </div>
        )}
        <div style={{ fontFamily: font.mono, fontSize: 9, marginTop: 3, display: "flex", gap: 8 }}>
          <span style={{ color: C.faint }}>{f.kind}</span>
          {onStar && (
            <span
              onClick={(e) => {
                e.stopPropagation(); // the row itself opens the source
                onStar(f);
              }}
              title={f.starred ? "unpin" : "pin this fact"}
              style={{ color: f.starred ? C.warn : C.faint, cursor: "pointer", fontSize: 10 }}
            >
              {f.starred ? "★" : "☆"}
            </span>
          )}
          {f.supersedes && (
            <span style={{ color: C.blue }} title={`supersedes ${f.supersedes}`}>
              ⤷ supersedes #{f.supersedes.slice(-6)}
            </span>
          )}
          {superseded && <span style={{ color: C.faint }}>superseded</span>}
          {(f.corroborating_sources ?? 0) > 0 && (
            <span style={{ color: C.ok }} title="independent sources corroborating">
              ✓×{f.corroborating_sources}
            </span>
          )}
          {f.stale && (
            <span style={{ color: C.warn }} title="observed_at trails ingested_at by > 6h">
              ⏱ STALE
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------- adjudication + analyze ---------------- */

function AnalyzeBox({ toast }: { toast: (m: string) => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!url.trim() || busy) return;
    setBusy(true);
    try {
      const r = await analyzeUrl(url.trim());
      toast(
        `${r.claims} claims extracted (${r.facts.filter((f) => f.attribution === "attributed").length} auto-attributed, ${r.dropped} dropped)`,
      );
      setUrl("");
    } catch (e) {
      toast(`analyze failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ display: "flex", gap: 6, padding: "7px 9px", borderBottom: `1px solid ${C.line}` }}>
      <input
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && run()}
        placeholder="point AI at a URL — extract claims, check vs ledger…"
        style={{
          flex: 1,
          background: C.bg,
          border: `1px solid ${C.line}`,
          borderRadius: 5,
          color: C.txt,
          fontFamily: font.mono,
          fontSize: 10,
          padding: "6px 8px",
          outline: "none",
        }}
      />
      <button
        onClick={run}
        style={{
          background: busy ? C.faint : C.ember,
          color: C.bg,
          border: "none",
          borderRadius: 5,
          fontFamily: font.disp,
          fontWeight: 700,
          fontSize: 12,
          letterSpacing: 1,
          padding: "0 11px",
          cursor: "pointer",
        }}
      >
        {busy ? "…" : "ANALYZE"}
      </button>
    </div>
  );
}

function AdjudicationStrip({
  items,
  onResolve,
  onTriage,
  onTriageAllUnmatched,
}: {
  items: AdjudicationItem[];
  onResolve: (factId: string, incidentId: string, name: string) => void;
  onTriage: (factId: string, state: TriageState) => void;
  onTriageAllUnmatched: (factIds: string[]) => void;
}) {
  if (!items.length) return null;
  // nothing named, nothing scored — these can never be attributed by hand
  const unmatched = items.filter((i) => !i.candidates.length && !i.claimed_name);
  return (
    <div style={{ margin: "0 10px 10px", border: `1px solid ${C.violet}44`, borderRadius: 7, background: C.panel }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 11px 3px" }}>
        <span style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.violet }}>
          ADJUDICATION QUEUE · {items.length} awaiting attribution
        </span>
        {unmatched.length > 1 && (
          <span
            onClick={() => onTriageAllUnmatched(unmatched.map((i) => i.fact.id))}
            title="clear every item that names no fire and matches none — they aren't about a tracked incident"
            style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 8.5, cursor: "pointer", color: C.dim, border: `1px solid ${C.line}`, borderRadius: 3, padding: "2px 7px" }}
          >
            CLEAR {unmatched.length} UNMATCHED
          </span>
        )}
      </div>
      <div style={{ maxHeight: 180, overflowY: "auto" }}>
        {items.map(({ fact, candidates, claimed_name, unknown_fire }) => (
          <div key={fact.id} style={{ padding: "6px 11px", borderTop: `1px solid ${C.line}44` }}>
            <div style={{ fontSize: 11.5 }}>{fact.content}</div>
            <div style={{ display: "flex", gap: 6, marginTop: 4, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>
                T{fact.tier} · {fact.source_name} ·{" "}
                {candidates.length ? "attach to:" : unknown_fire ? "" : "no match:"}
              </span>
              {unknown_fire && (
                <span
                  title="this names a fire we don't track — likely a new start; the WATCH board clusters these"
                  style={{ fontFamily: font.mono, fontSize: 9, padding: "2px 8px", borderRadius: 3, border: `1px solid ${C.warn}`, color: C.warn }}
                >
                  ⚑ {claimed_name} — not a tracked fire
                </span>
              )}
              {candidates.slice(0, 3).map((c) => (
                <span
                  key={c.incident_id}
                  onClick={() => onResolve(fact.id, c.incident_id, c.incident_name)}
                  title={`name ${c.name_score} · geo ${c.geo_score} · time ${c.time_score}`}
                  style={{
                    fontFamily: font.mono,
                    fontSize: 9,
                    padding: "2px 8px",
                    borderRadius: 3,
                    cursor: "pointer",
                    border: `1px solid ${C.violet}`,
                    color: C.violet,
                  }}
                >
                  {c.incident_name} · {Math.round(c.score * 100)}%
                </span>
              ))}
              <span style={{ marginLeft: "auto", display: "flex", gap: 5 }}>
                {unknown_fire && (
                  <span
                    onClick={() => onTriage(fact.id, "watch")}
                    title="track as a possible new start on the WATCH board"
                    style={{ fontFamily: font.mono, fontSize: 8.5, cursor: "pointer", color: C.warn, border: `1px solid ${C.warn}66`, borderRadius: 3, padding: "2px 7px" }}
                  >
                    → WATCH
                  </span>
                )}
                <span
                  onClick={() => onTriage(fact.id, "not_incident")}
                  title="not about a tracked wildfire — clears the queue; the fact itself is never altered"
                  style={{ fontFamily: font.mono, fontSize: 8.5, cursor: "pointer", color: C.dim, border: `1px solid ${C.line}`, borderRadius: 3, padding: "2px 7px" }}
                >
                  NOT A FIRE
                </span>
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- incident intelligence panel ---------------- */

function StatTile({ label, children, sub }: { label: string; children: ReactNode; sub?: string }) {
  return (
    <div style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6, padding: "7px 9px", minWidth: 0 }}>
      <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, letterSpacing: 1 }}>{label}</div>
      <div style={{ fontFamily: font.disp, fontSize: 17, fontWeight: 700, marginTop: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {children}
      </div>
      {sub && <div style={{ fontFamily: font.mono, fontSize: 8, color: C.dim, marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub}</div>}
    </div>
  );
}

function Delta({ d, invert }: { d: number | null; invert?: boolean }) {
  if (d == null || d === 0) return null;
  const good = invert ? d > 0 : d < 0;
  return (
    <span style={{ fontFamily: font.mono, fontSize: 10, color: good ? C.ok : C.emberHot, marginLeft: 5 }}>
      {d > 0 ? "+" : ""}
      {d.toLocaleString()}
    </span>
  );
}

function IntelPanel({ sel, toast }: { sel: Incident; toast: (m: string) => void }) {
  const intel = usePolling(() => fetchIntel(sel.id), [sel.id]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [noteText, setNoteText] = useState("");
  const [impactOpen, setImpactOpen] = useState<"evac" | "road" | null>(null);

  const refreshNotes = useCallback(async () => {
    try {
      setNotes(await fetchNotes(sel.id));
    } catch {}
  }, [sel.id]);
  useEffect(() => {
    refreshNotes();
  }, [refreshNotes]);

  if (!intel)
    return <div style={{ padding: 14, fontFamily: font.mono, fontSize: 10, color: C.faint }}>loading intel…</div>;

  const m = intel.metrics;
  const conf = intel.confidence;
  const confPct = conf.avg_confidence != null ? Math.round(conf.avg_confidence * 100) : null;

  const submitNote = async () => {
    if (!noteText.trim()) return;
    try {
      await addNote(noteText.trim(), sel.id);
      setNoteText("");
      refreshNotes();
    } catch (e) {
      toast(`note failed: ${e instanceof Error ? e.message : e}`);
    }
  };

  return (
    <div style={{ padding: "10px 11px", display: "flex", flexDirection: "column", gap: 10 }}>
      {/* overview grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        <StatTile label="ACRES" sub={m.acreage.source_name && `${m.acreage.source_name} · ${fmtTime(m.acreage.observed_at)}`}>
          {m.acreage.value != null ? m.acreage.value.toLocaleString() : "—"}
          <Delta d={m.acreage.delta} />
        </StatTile>
        <StatTile label="CONTAINMENT" sub={m.containment.source_name && `${m.containment.source_name} · ${fmtTime(m.containment.observed_at)}`}>
          {m.containment.value != null ? `${m.containment.value}%` : "—"}
          <Delta d={m.containment.delta} invert />
        </StatTile>
        <StatTile label="STARTED" sub={m.start ? `${m.start.source_name}` : undefined}>
          <span style={{ fontSize: 12.5 }}>{m.start ? fmtTime(m.start.observed_at) : "—"}</span>
        </StatTile>
        <StatTile label="CAUSE" sub={m.cause ? m.cause.source_name : undefined}>
          <span style={{ fontSize: 12.5 }}>{m.cause ? m.cause.content.slice(0, 40) : "under investigation"}</span>
        </StatTile>
      </div>

      {/* source confidence meter — the signature stat */}
      <div style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6, padding: "8px 10px" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, letterSpacing: 1 }}>SOURCE CONFIDENCE</span>
          <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 10, color: confPct != null ? confColor(conf.avg_confidence!) : C.faint }}>
            {confPct != null ? `${confPct}%` : "no assessments yet"}
          </span>
        </div>
        <div style={{ height: 5, background: C.line, borderRadius: 3, marginTop: 6, overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${confPct ?? 0}%`, background: confPct != null ? confColor(conf.avg_confidence!) : C.line, borderRadius: 3 }} />
        </div>
        <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, marginTop: 5 }}>
          Sources: {conf.source_count} · {conf.corroborated_facts} corroborated fact{conf.corroborated_facts === 1 ? "" : "s"} · {conf.fact_count} total
        </div>
      </div>

      {/* Orders and warnings are different instructions to a person deciding
          whether to leave, so they are never collapsed into one number. */}
      {(intel.impact_counts?.orders > 0 ||
        intel.impact_counts?.warnings > 0 ||
        intel.impact_counts?.road_closures > 0) && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6 }}>
          {[
            { label: "EVAC ORDERS", n: intel.impact_counts.orders, color: C.evac },
            { label: "WARNINGS", n: intel.impact_counts.warnings, color: C.warn },
            { label: "ROAD CLOSURES", n: intel.impact_counts.road_closures, color: C.blue },
          ].map((b) => (
            <div
              key={b.label}
              style={{
                background: b.n ? b.color + "14" : C.bg,
                border: `1px solid ${b.n ? b.color + "66" : C.line}`,
                borderRadius: 6,
                padding: "5px 8px",
                textAlign: "center",
              }}
            >
              <div style={{ fontFamily: font.disp, fontSize: 17, fontWeight: 700, color: b.n ? b.color : C.dim }}>
                {b.n}
              </div>
              <div style={{ fontFamily: font.mono, fontSize: 7.5, color: C.dim, letterSpacing: 0.5 }}>
                {b.label}
              </div>
            </div>
          ))}
        </div>
      )}
      {intel.impact_counts?.lifted > 0 && (
        <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.ok }}>
          ✓ {intel.impact_counts.lifted} evacuation{intel.impact_counts.lifted === 1 ? "" : "s"} lifted
        </div>
      )}

      {/* full verbatim detail behind the counts */}
      <div style={{ display: "flex", gap: 6 }}>
        {(["evac", "road"] as const).map((k) => (
          <div
            key={k}
            onClick={() => setImpactOpen(impactOpen === k ? null : k)}
            style={{
              flex: 1,
              background: intel[k].length ? C.evac + "18" : C.bg,
              border: `1px solid ${intel[k].length ? C.evac : C.line}`,
              borderRadius: 6,
              padding: "6px 10px",
              cursor: intel[k].length ? "pointer" : "default",
              textAlign: "center",
            }}
          >
            <span style={{ fontFamily: font.disp, fontSize: 16, fontWeight: 700, color: intel[k].length ? C.evac : C.dim }}>
              {intel[k].length}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, marginLeft: 6, letterSpacing: 1 }}>
              {k === "evac" ? "EVACUATION" : "ROAD"} {impactOpen === k ? "▾" : ""}
            </span>
          </div>
        ))}
      </div>
      {impactOpen && intel[impactOpen].length > 0 && (
        <div style={{ border: `1px solid ${C.evac}55`, borderRadius: 6, padding: "8px 10px", background: C.bg }}>
          <div style={{ fontFamily: font.mono, fontSize: 8, color: C.evac, letterSpacing: 1, marginBottom: 6 }}>
            OFFICIAL TEXT, SHOWN EXACTLY AS RECEIVED — VERIFY WITH YOUR COUNTY OFFICIALS
          </div>
          {intel[impactOpen].map((f) => (
            <div key={f.id} style={{ borderTop: `1px solid ${C.line}44`, padding: "6px 0" }}>
              <div style={{ fontSize: 11.5, whiteSpace: "pre-wrap" }}>{f.content}</div>
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, marginTop: 3 }}>
                <span style={{ color: TIER[f.tier] }}>T{f.tier}</span> {f.source_name} · {fmtTime(f.observed_at)}
                {f.evac_status && <span style={{ color: C.evac }}> · {f.evac_status.toUpperCase()}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* current situation */}
      <div>
        <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, letterSpacing: 1, marginBottom: 4 }}>CURRENT SITUATION</div>
        {intel.situation.length ? (
          intel.situation.map((s) => (
            <div key={s.kind} style={{ display: "flex", gap: 7, padding: "4px 0", fontSize: 11 }}>
              <span style={{ color: C.ember }}>▸</span>
              <span style={{ flex: 1 }}>
                {s.content}
                <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, marginLeft: 6 }}>
                  <span style={{ color: TIER[s.tier] }}>T{s.tier}</span> {s.source_name} · {fmtTime(s.observed_at)}
                </span>
              </span>
            </div>
          ))
        ) : (
          <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.faint }}>no situation reports yet</div>
        )}
      </div>

      {/* analyst notes & tasks */}
      <div>
        <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, letterSpacing: 1, marginBottom: 4 }}>ANALYST NOTES &amp; TASKS</div>
        <div style={{ display: "flex", gap: 5 }}>
          <input
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitNote()}
            placeholder="add a note or task…"
            style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.txt, fontFamily: font.mono, fontSize: 10, padding: "5px 8px", outline: "none" }}
          />
          <button onClick={submitNote} style={{ background: C.ember, color: C.bg, border: "none", borderRadius: 4, fontFamily: font.disp, fontWeight: 700, fontSize: 11, padding: "0 10px", cursor: "pointer" }}>
            +
          </button>
        </div>
        {notes.map((n) => (
          <div key={n.id} style={{ display: "flex", gap: 7, alignItems: "flex-start", padding: "4px 0", fontSize: 11, opacity: n.done ? 0.5 : 1 }}>
            <span
              onClick={async () => {
                await toggleNote(n.id);
                refreshNotes();
              }}
              style={{ cursor: "pointer", color: n.done ? C.ok : C.dim, fontFamily: font.mono }}
            >
              {n.done ? "☑" : "☐"}
            </span>
            <span style={{ flex: 1, textDecoration: n.done ? "line-through" : "none" }}>
              {n.body}
              {!n.incident_id && <span style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginLeft: 5 }}>GLOBAL</span>}
            </span>
            <span
              onClick={async () => {
                await deleteNote(n.id);
                refreshNotes();
              }}
              title="delete note"
              style={{ cursor: "pointer", color: C.faint, fontFamily: font.mono, fontSize: 10 }}
            >
              ✕
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* WEATHER — real observations from the nearest NWS/RAWS station, always shown
   with which station, how far away, and how old. Weather from 30 km away over
   a ridge is not weather at the fire. */
function WeatherPanel({ incidentId }: { incidentId: string }) {
  const [wx, setWx] = useState<IncidentWeather | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    async (refresh = false) => {
      setBusy(true);
      try {
        setWx(await fetchWeather(incidentId, refresh));
      } catch {
        setWx({ available: false, reason: "could not reach the weather service" });
      } finally {
        setBusy(false);
      }
    },
    [incidentId],
  );
  useEffect(() => {
    load();
  }, [load]);

  if (!wx)
    return <div style={{ padding: 14, fontFamily: font.mono, fontSize: 10, color: C.faint }}>loading weather…</div>;

  if (wx.available === false)
    return (
      <div style={{ padding: 14, fontFamily: font.mono, fontSize: 9.5, color: C.faint }}>
        {wx.reason}
        <div
          onClick={() => load(true)}
          style={{ marginTop: 8, color: C.blue, cursor: "pointer", border: `1px solid ${C.blue}55`, borderRadius: 4, padding: "4px 8px", display: "inline-block" }}
        >
          ↻ TRY AGAIN
        </div>
      </div>
    );

  const cells: [string, string][] = [
    ["WIND", wx.wind_mph != null ? `${wx.wind_compass ?? ""} ${wx.wind_mph} mph` : "—"],
    ["GUSTS", wx.gust_mph != null ? `${wx.gust_mph} mph` : "—"],
    ["RH", wx.relative_humidity != null ? `${Math.round(wx.relative_humidity)}%` : "—"],
    ["TEMP", wx.temperature_f != null ? `${Math.round(wx.temperature_f)}°F` : "—"],
  ];

  return (
    <div style={{ padding: "10px 11px", display: "flex", flexDirection: "column", gap: 10 }}>
      {wx.red_flag && (
        <div style={{ border: `1px solid ${C.evac}`, background: C.evac + "18", borderRadius: 6, padding: "7px 10px" }}>
          <div style={{ fontFamily: font.disp, fontSize: 13, fontWeight: 700, color: C.evac, letterSpacing: 1 }}>
            ⚠ RED FLAG WARNING
          </div>
          {wx.alerts?.filter((a) => a.event === "Red Flag Warning").map((a) => (
            <div key={a.headline} style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, marginTop: 3 }}>
              {a.headline}
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 6 }}>
        {cells.map(([label, value]) => (
          <div key={label} style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6, padding: "7px 9px" }}>
            <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, letterSpacing: 1 }}>{label}</div>
            <div style={{ fontFamily: font.disp, fontSize: 17, fontWeight: 700, marginTop: 1 }}>{value}</div>
          </div>
        ))}
      </div>

      {wx.conditions && (
        <div style={{ fontSize: 11, color: C.dim }}>{wx.conditions}</div>
      )}

      {(wx.alerts?.length ?? 0) > 0 && (
        <div>
          <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, letterSpacing: 1, marginBottom: 4 }}>
            ACTIVE NWS ALERTS
          </div>
          {wx.alerts!.map((a) => (
            <div key={a.headline} style={{ display: "flex", gap: 6, padding: "3px 0", fontSize: 10.5 }}>
              <span style={{ color: a.critical ? C.evac : C.warn }}>▸</span>
              <span style={{ flex: 1 }}>
                {a.event}
                <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginLeft: 5 }}>
                  {a.severity}
                  {a.ends && ` · until ${fmtStamp(a.ends)}`}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}

      {/* provenance — a reading is only as good as where and when it was taken */}
      <div style={{ borderTop: `1px solid ${C.line}44`, paddingTop: 7, fontFamily: font.mono, fontSize: 8.5, color: C.faint, lineHeight: 1.6 }}>
        station <span style={{ color: C.dim }}>{wx.station?.name || wx.station?.id}</span>
        {wx.station?.distance_km != null && ` · ${wx.station.distance_km} km from the fire`}
        <br />
        observed {fmtStamp(wx.observed_at)} ({age(wx.observed_at)} old){wx.cached && " · cached"}
        <br />
        {wx.source}
        <span
          onClick={() => load(true)}
          style={{ color: C.blue, cursor: "pointer", marginLeft: 6 }}
        >
          {busy ? "…" : "↻ refresh"}
        </span>
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint }}>
        Fire behaviour (flame length, fuel moisture) is not shown: it needs a fuels
        and terrain model we do not have, and estimating it would be a guess.
      </div>
    </div>
  );
}

function TimelinePanel({ incidentId }: { incidentId: string }) {
  const raw = usePolling(() => fetchTimeline(incidentId), [incidentId]);
  const [newestFirst, setNewestFirst] = useState(
    () => loadDisplayPrefs().timelineOrder !== "oldest",
  );
  const tl = useMemo(() => (raw ? (newestFirst ? raw : [...raw].reverse()) : null), [raw, newestFirst]);
  if (!tl)
    return <div style={{ padding: 14, fontFamily: font.mono, fontSize: 10, color: C.faint }}>loading timeline…</div>;
  return (
    <div style={{ padding: "8px 11px" }}>
      <div
        onClick={() => setNewestFirst((v) => !v)}
        title="flip the timeline direction"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          cursor: "pointer",
          fontFamily: font.mono,
          fontSize: 8.5,
          color: C.dim,
          border: `1px solid ${C.line}`,
          borderRadius: 4,
          padding: "3px 7px",
          marginBottom: 7,
          width: "fit-content",
        }}
      >
        <span style={{ color: C.ember }}>{newestFirst ? "↓" : "↑"}</span>
        {newestFirst ? "NEWEST FIRST" : "OLDEST FIRST"}
      </div>
      {tl.map((e, i) => (
        <div key={i} style={{ display: "flex", gap: 9, padding: "5px 0", borderBottom: `1px solid ${C.line}33`, opacity: e.superseded ? 0.5 : 1 }}>
          <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, width: 46, flexShrink: 0 }} title={e.at}>
            {fmtTime(e.at)}
          </span>
          <div style={{ minWidth: 0, flex: 1 }}>
            <span
              style={{
                fontFamily: font.mono,
                fontSize: 8,
                letterSpacing: 1,
                color: e.type === "event" ? C.violet : e.tier != null ? TIER[e.tier] : C.dim,
                border: `1px solid ${e.type === "event" ? C.violet + "66" : C.line}`,
                borderRadius: 3,
                padding: "1px 5px",
                marginRight: 6,
              }}
            >
              {e.label}
            </span>
            {e.type === "fact" ? (
              <span style={{ fontSize: 10.5, textDecoration: e.superseded ? "line-through" : "none" }}>
                {e.content}
                <span style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginLeft: 5 }}>{e.source_name}</span>
              </span>
            ) : (
              <span style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim }}>by {e.actor}</span>
            )}
          </div>
        </div>
      ))}
      {!tl.length && <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.faint }}>no history yet</div>}
    </div>
  );
}

/* ---------------- OPS mode ---------------- */

type SortMode = "received" | "published" | "oldest" | "confidence" | "tier";

const SORTERS: Record<SortMode, (a: Fact, b: Fact) => number> = {
  received: (a, b) => b.ingested_at.localeCompare(a.ingested_at),
  published: (a, b) => b.observed_at.localeCompare(a.observed_at),
  oldest: (a, b) => a.observed_at.localeCompare(b.observed_at),
  confidence: (a, b) => b.confidence - a.confidence,
  tier: (a, b) => a.tier - b.tier || b.observed_at.localeCompare(a.observed_at),
};

const SORT_LABELS: Record<SortMode, string> = {
  received: "NEWEST IN",
  published: "NEWEST PUB",
  oldest: "OLDEST",
  confidence: "CONFIDENCE",
  tier: "TRUST TIER",
};

function OpsMode({ toast }: { toast: (m: string) => void }) {
  const incidents = usePolling(fetchIncidents);
  const intake = usePolling(fetchIntake);
  const adjudication = usePolling(fetchAdjudication);
  const [resolved, setResolved] = useState<Record<string, boolean>>({});
  const onResolve = async (factId: string, incidentId: string, name: string) => {
    try {
      await resolveAdjudication(factId, incidentId);
      setResolved((s) => ({ ...s, [factId]: true }));
      toast(`fact attributed to ${name}`);
    } catch (e) {
      toast(`resolve failed: ${e instanceof Error ? e.message : e}`);
    }
  };
  const onTriage = async (factId: string, state: TriageState) => {
    try {
      await triageFact(factId, state);
      setResolved((s) => ({ ...s, [factId]: true }));
      toast(state === "watch" ? "sent to watch board" : "cleared — not a tracked fire");
    } catch (e) {
      toast(`triage failed: ${e instanceof Error ? e.message : e}`);
    }
  };
  const onTriageAllUnmatched = async (factIds: string[]) => {
    try {
      const r = await triageBulk(factIds, "not_incident");
      setResolved((s) => ({ ...s, ...Object.fromEntries(factIds.map((id) => [id, true])) }));
      toast(`cleared ${r.triaged} unmatched fact${r.triaged === 1 ? "" : "s"}`);
    } catch (e) {
      toast(`bulk triage failed: ${e instanceof Error ? e.message : e}`);
    }
  };
  const [selected, setSelected] = useState<string | null>(null);
  const [tiers, setTiers] = useState<Record<number, boolean>>({ 1: true, 2: true, 3: true, 4: true, 5: true });
  const [kw, setKw] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("received");
  const [groupByFire, setGroupByFire] = useState(false);
  const [kindFilter, setKindFilter] = useState<string>("");
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const [hideStale, setHideStale] = useState(false);
  const [onlyLinked, setOnlyLinked] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const facts = usePolling(
    () => (selected ? fetchIncidentFacts(selected) : Promise.resolve([] as Fact[])),
    [selected],
  );

  useEffect(() => {
    if (!selected && incidents?.length) setSelected(incidents[0].id);
  }, [incidents, selected]);

  const kinds = useMemo(
    () => [...new Set((intake ?? []).map((f) => f.kind))].sort(),
    [intake],
  );
  const sourceNames = useMemo(
    () => [...new Set((intake ?? []).map((f) => f.source_name))].sort(),
    [intake],
  );

  const shown = useMemo(() => {
    const list = (intake ?? []).filter((f) => {
      if (!tiers[f.tier]) return false;
      if (kindFilter && f.kind !== kindFilter) return false;
      if (sourceFilter && f.source_name !== sourceFilter) return false;
      if (hideStale && f.stale) return false;
      if (onlyLinked && !f.source_url) return false;
      if (
        kw &&
        !(f.content + (f.source_name ?? "") + (f.incident_name ?? ""))
          .toLowerCase()
          .includes(kw.toLowerCase())
      )
        return false;
      return true;
    });
    return [...list].sort(SORTERS[sortMode]);
  }, [intake, tiers, kw, kindFilter, sourceFilter, hideStale, onlyLinked, sortMode]);

  const grouped = useMemo(() => {
    if (!groupByFire) return null;
    const map = new Map<string, Fact[]>();
    for (const f of shown) {
      const key = f.incident_name ?? "UNATTRIBUTED";
      const arr = map.get(key);
      if (arr) arr.push(f);
      else map.set(key, [f]);
    }
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [shown, groupByFire]);

  const [starred, setStarred] = useState<Record<string, boolean>>({});
  const onStar = async (f: Fact) => {
    const next = !(starred[f.id] ?? !!f.starred);
    setStarred((s) => ({ ...s, [f.id]: next }));
    try {
      await starFact(f.id, next);
    } catch {
      setStarred((s) => ({ ...s, [f.id]: !next }));
    }
  };

  const filtersOn =
    kindFilter || sourceFilter || hideStale || onlyLinked || kw || Object.values(tiers).some((v) => !v);
  const clearFilters = () => {
    setKindFilter("");
    setSourceFilter("");
    setHideStale(false);
    setOnlyLinked(false);
    setKw("");
    setTiers({ 1: true, 2: true, 3: true, 4: true, 5: true });
  };

  const sel = incidents?.find((i) => i.id === selected);
  const staleCount = facts?.filter((f) => f.stale).length ?? 0;
  const [panelTab, setPanelTab] = useState<"intel" | "weather" | "ledger" | "timeline">("intel");
  const [basemap] = useState(() => loadDisplayPrefs().basemap);
  const [focus, setFocus] = useState<{
    lat: number;
    lon: number;
    zoom: number;
    nonce: number;
    basemap?: string;
  } | null>(null);
  const [originCheck, setOriginCheck] = useState<OriginCheck | null>(null);
  const [checking, setChecking] = useState(false);

  // Zooming to street level makes a coordinate look authoritative, so it is
  // re-verified against CAL FIRE at that exact moment. We still fly there —
  // the operator asked — but they see what the point is worth.
  const onZoomToOrigin = async (inc: Incident) => {
    if (inc.lat == null || inc.lon == null) return;
    setChecking(true);
    setOriginCheck(null);
    setFocus({ lat: inc.lat, lon: inc.lon, zoom: 16, nonce: Date.now(), basemap: "satellite" });
    try {
      const check = await fetchOriginCheck(inc.id);
      setOriginCheck(check);
      // CAL FIRE has moved the point since we recorded it — go where CAL FIRE
      // says, and say so, rather than showing the operator a stale spot.
      if (check.official && check.official.delta_km > 1) {
        setFocus({
          lat: check.official.lat,
          lon: check.official.lon,
          zoom: 16,
          nonce: Date.now(),
          basemap: "satellite",
        });
        toast(`CAL FIRE has this fire ${check.official.delta_km} km from our stored point — showing CAL FIRE's`);
      }
    } catch {
      setOriginCheck(null);
    } finally {
      setChecking(false);
    }
  };
  const [followOverride, setFollowOverride] = useState<Record<string, boolean>>({});
  const isFollowed = sel ? (followOverride[sel.id] ?? !!sel.followed) : false;
  const toggleFollow = async () => {
    if (!sel) return;
    const next = !isFollowed;
    setFollowOverride((s) => ({ ...s, [sel.id]: next }));
    try {
      await followIncident(sel.id, next);
    } catch (e) {
      setFollowOverride((s) => ({ ...s, [sel.id]: !next }));
      toast(`follow failed: ${e instanceof Error ? e.message : e}`);
    }
  };

  return (
    <div style={{ flex: 1, display: "grid", gridTemplateColumns: "330px 1fr 340px", minHeight: 0 }}>
      {/* intake stream */}
      <div style={{ borderRight: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ display: "flex", gap: 6, padding: "7px 9px", alignItems: "center", borderBottom: `1px solid ${C.line}`, flexWrap: "wrap" }}>
          <span style={{ fontFamily: font.disp, fontSize: 13, fontWeight: 700, letterSpacing: 1.5, color: C.txt }}>INTAKE</span>
          <div style={{ display: "flex", gap: 3 }}>
            {[1, 2, 3, 4, 5].map((t) => (
              <span
                key={t}
                onClick={() => setTiers((s) => ({ ...s, [t]: !s[t] }))}
                title={TIER_TITLES[t]}
                style={{
                  fontFamily: font.mono,
                  fontSize: 8.5,
                  padding: "2px 5px",
                  borderRadius: 3,
                  cursor: "pointer",
                  background: tiers[t] ? TIER[t] + "22" : "transparent",
                  border: `1px solid ${tiers[t] ? TIER[t] : C.line}`,
                  color: tiers[t] ? TIER[t] : C.faint,
                }}
              >
                T{t}
              </span>
            ))}
          </div>
          <span
            onClick={() => setShowFilters((v) => !v)}
            title="more filters"
            style={{
              fontFamily: font.mono,
              fontSize: 9,
              cursor: "pointer",
              color: filtersOn ? C.ember : C.dim,
              border: `1px solid ${filtersOn ? C.ember : C.line}`,
              borderRadius: 3,
              padding: "1px 6px",
            }}
          >
            ⚙{filtersOn ? " •" : ""}
          </span>
          <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>
            {shown.length}/{intake?.length ?? 0}
          </span>
          <input
            value={kw}
            onChange={(e) => setKw(e.target.value)}
            placeholder="⌕ keyword"
            style={{
              flex: "1 1 100%",
              background: C.bg,
              border: `1px solid ${C.line}`,
              borderRadius: 4,
              color: C.txt,
              fontFamily: font.mono,
              fontSize: 9.5,
              padding: "4px 7px",
              outline: "none",
            }}
          />
          {/* sort + group row */}
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap", width: "100%" }}>
            {(Object.keys(SORT_LABELS) as SortMode[]).map((m) => (
              <span
                key={m}
                onClick={() => setSortMode(m)}
                title={
                  m === "received"
                    ? "order by when we received it"
                    : m === "published"
                      ? "order by when the source published it"
                      : m === "oldest"
                        ? "oldest publication first"
                        : m === "confidence"
                          ? "highest confidence first"
                          : "most trusted sources first"
                }
                style={{
                  fontFamily: font.mono,
                  fontSize: 8,
                  padding: "2px 6px",
                  borderRadius: 3,
                  cursor: "pointer",
                  border: `1px solid ${sortMode === m ? C.blue : C.line}`,
                  color: sortMode === m ? C.blue : C.faint,
                }}
              >
                {SORT_LABELS[m]}
              </span>
            ))}
            <span
              onClick={() => setGroupByFire((v) => !v)}
              title="group the stream by fire"
              style={{
                marginLeft: "auto",
                fontFamily: font.mono,
                fontSize: 8,
                padding: "2px 6px",
                borderRadius: 3,
                cursor: "pointer",
                border: `1px solid ${groupByFire ? C.ember : C.line}`,
                color: groupByFire ? C.ember : C.faint,
              }}
            >
              {groupByFire ? "BY FIRE" : "STREAM"}
            </span>
          </div>
          {showFilters && (
            <div style={{ width: "100%", display: "flex", flexWrap: "wrap", gap: 5, paddingTop: 5, borderTop: `1px solid ${C.line}44` }}>
              <select
                value={kindFilter}
                onChange={(e) => setKindFilter(e.target.value)}
                style={{ flex: 1, minWidth: 90, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: kindFilter ? C.ember : C.dim, fontFamily: font.mono, fontSize: 9, padding: "3px 5px", outline: "none" }}
              >
                <option value="">all kinds</option>
                {kinds.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
              <select
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value)}
                style={{ flex: 1, minWidth: 90, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: sourceFilter ? C.ember : C.dim, fontFamily: font.mono, fontSize: 9, padding: "3px 5px", outline: "none" }}
              >
                <option value="">all sources</option>
                {sourceNames.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <span
                onClick={() => setHideStale((v) => !v)}
                title="hide facts whose publication long predates our receipt"
                style={{ fontFamily: font.mono, fontSize: 8, padding: "3px 6px", borderRadius: 3, cursor: "pointer", border: `1px solid ${hideStale ? C.warn : C.line}`, color: hideStale ? C.warn : C.faint }}
              >
                HIDE STALE
              </span>
              <span
                onClick={() => setOnlyLinked((v) => !v)}
                title="only facts that link back to a source page"
                style={{ fontFamily: font.mono, fontSize: 8, padding: "3px 6px", borderRadius: 3, cursor: "pointer", border: `1px solid ${onlyLinked ? C.blue : C.line}`, color: onlyLinked ? C.blue : C.faint }}
              >
                ⧉ LINKED
              </span>
              {filtersOn && (
                <span
                  onClick={clearFilters}
                  style={{ fontFamily: font.mono, fontSize: 8, padding: "3px 6px", borderRadius: 3, cursor: "pointer", border: `1px solid ${C.evac}66`, color: C.evac }}
                >
                  CLEAR
                </span>
              )}
            </div>
          )}
        </div>
        <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {grouped
            ? grouped.map(([name, rows]) => (
                <div key={name}>
                  <div
                    onClick={() => {
                      const inc = incidents?.find((i) => i.canonical_name === name);
                      if (inc) setSelected(inc.id);
                    }}
                    style={{
                      position: "sticky",
                      top: 0,
                      zIndex: 2,
                      background: C.panelUp,
                      borderBottom: `1px solid ${C.line}`,
                      padding: "4px 10px",
                      fontFamily: font.mono,
                      fontSize: 9,
                      letterSpacing: 1,
                      color: name === "UNATTRIBUTED" ? C.faint : C.ember,
                      cursor: "pointer",
                      display: "flex",
                      gap: 6,
                    }}
                  >
                    <span>{name}</span>
                    <span style={{ marginLeft: "auto", color: C.faint }}>{rows.length}</span>
                  </div>
                  {rows.map((f) => (
                    <FactRow key={f.id} f={{ ...f, starred: (starred[f.id] ?? !!f.starred) ? 1 : 0 }} showIncident onStar={onStar} />
                  ))}
                </div>
              ))
            : shown.map((f, i) => (
                <div key={f.id} className={i === 0 ? "tr-in" : undefined}>
                  <FactRow f={{ ...f, starred: (starred[f.id] ?? !!f.starred) ? 1 : 0 }} showIncident onStar={onStar} />
                </div>
              ))}
          {!shown.length && (
            <div style={{ padding: 16, fontFamily: font.mono, fontSize: 10, color: C.faint }}>
              {filtersOn ? "nothing matches these filters" : "waiting for intake…"}
            </div>
          )}
        </div>
      </div>

      {/* map + compact incident list */}
      <div style={{ background: C.bg, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <AnalyzeBox toast={toast} />
        <AdjudicationStrip
          items={(adjudication ?? []).filter((it) => !resolved[it.fact.id])}
          onResolve={onResolve}
          onTriage={onTriage}
          onTriageAllUnmatched={onTriageAllUnmatched}
        />
        <div style={{ flex: 1, minHeight: 0 }}>
          <MapPanel
            incidents={incidents ?? []}
            selected={selected}
            onSelect={setSelected}
            basemap={basemap}
            focus={focus}
          />
        </div>
        {/* compact incident chip strip below the map */}
        <div style={{ flexShrink: 0, borderTop: `1px solid ${C.line}`, maxHeight: 150, overflowY: "auto", background: C.panel }}>
          {(incidents ?? []).map((inc) => (
            <div
              key={inc.id}
              onClick={() => setSelected(inc.id)}
              className="tr-row"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "5px 11px",
                borderBottom: `1px solid ${C.line}44`,
                borderLeft: `3px solid ${inc.id === selected ? C.ember : "transparent"}`,
                cursor: "pointer",
              }}
            >
              <span style={{ fontFamily: font.disp, fontSize: 13, fontWeight: 700 }}>{inc.canonical_name}</span>
              <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>
                {inc.county ? `${inc.county} · ` : ""}
                {inc.acreage != null ? `${inc.acreage.toLocaleString()} ac · ` : ""}
                {inc.containment_pct != null ? `${inc.containment_pct}% cont` : ""}
              </span>
              <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 8, color: inc.status === "active" ? C.emberHot : C.dim }}>
                {inc.status.toUpperCase()}
              </span>
            </div>
          ))}
          {!incidents?.length && (
            <div style={{ padding: 14, fontFamily: font.mono, fontSize: 10, color: C.faint }}>
              no active incidents — waiting for first CAL FIRE poll
            </div>
          )}
        </div>
      </div>

      {/* ledger panel */}
      <div style={{ borderLeft: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: "10px 13px", borderBottom: `1px solid ${C.line}`, flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <div style={{ fontFamily: font.disp, fontSize: 20, fontWeight: 700, flex: 1, minWidth: 0 }}>
              {sel?.canonical_name ?? "—"}
            </div>
            {sel?.lat != null && sel?.lon != null && (
              <span
                onClick={() => onZoomToOrigin(sel)}
                title={
                  `fly the map to the reported origin (${sel.lat.toFixed(4)}, ${sel.lon.toFixed(4)}) ` +
                  `at street zoom.\n\nThis is the point the reporting source gives for the fire — ` +
                  `usually its origin area, not a surveyed ignition point.`
                }
                style={{ fontFamily: font.mono, fontSize: 9, color: C.ok, border: `1px solid ${C.ok}66`, borderRadius: 3, padding: "2px 7px", cursor: "pointer", whiteSpace: "nowrap" }}
              >
                ⌖ ZOOM TO ORIGIN
              </span>
            )}
            {sel && (
              <span
                onClick={toggleFollow}
                title={isFollowed ? "following — unstar to stop" : "follow this incident"}
                style={{ cursor: "pointer", fontSize: 15, color: isFollowed ? C.warn : C.faint, userSelect: "none" }}
              >
                {isFollowed ? "★" : "☆"}
              </span>
            )}
            {sel && (
              <span
                onClick={async () => {
                  try {
                    await startResearch(
                      `California wildfire "${sel.canonical_name}"${sel.county ? ` in ${sel.county} County` : ""} — latest status, size, containment, evacuations, road closures, resources. Only this specific fire.`,
                      sel.id,
                    );
                    toast(`deep research queued for ${sel.canonical_name} — result lands in NEWSROOM`);
                  } catch (e) {
                    toast(`research failed: ${e instanceof Error ? e.message : e}`);
                  }
                }}
                title="agentic web research — cited dossier lands as an analysis draft in NEWSROOM"
                style={{ fontFamily: font.mono, fontSize: 9, color: C.blue, border: `1px solid ${C.blue}66`, borderRadius: 3, padding: "2px 7px", cursor: "pointer", whiteSpace: "nowrap" }}
              >
                ⌕ RESEARCH
              </span>
            )}
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 10, color: C.dim, marginTop: 2 }}>
            {sel ? `${sel.county ?? "?"} County · ${sel.status} · ${sel.fact_count} facts` : "select an incident"}
          </div>

          {/* origin verification — what the coordinate we just flew to is worth */}
          {(checking || (originCheck && originCheck.incident_id === sel?.id)) && (
            <div
              style={{
                marginTop: 6,
                borderRadius: 5,
                border: `1px solid ${
                  checking
                    ? C.line
                    : originCheck!.verified
                      ? C.ok + "66"
                      : originCheck!.confidence === "low"
                        ? C.evac + "66"
                        : C.warn + "66"
                }`,
                background: checking ? "transparent" : C.bg,
                padding: "6px 8px",
              }}
            >
              {checking ? (
                <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>
                  ⌖ verifying origin against CAL FIRE…
                </div>
              ) : (
                <>
                  <div
                    style={{
                      fontFamily: font.mono,
                      fontSize: 9,
                      letterSpacing: 0.5,
                      color: originCheck!.verified
                        ? C.ok
                        : originCheck!.confidence === "low"
                          ? C.evac
                          : C.warn,
                    }}
                  >
                    {originCheck!.verified
                      ? "✓ ORIGIN VERIFIED"
                      : originCheck!.confidence === "low"
                        ? "⚠ ORIGIN NOT VERIFIED"
                        : "⚠ ORIGIN PARTLY VERIFIED"}
                    {originCheck!.lat != null &&
                      ` · ${originCheck!.lat.toFixed(4)}, ${originCheck!.lon!.toFixed(4)}`}
                  </div>
                  {originCheck!.checks.map((c) => (
                    <div
                      key={c.check}
                      style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 2 }}
                    >
                      <span style={{ color: c.ok === true ? C.ok : c.ok === false ? C.evac : C.dim }}>
                        {c.ok === true ? "✓" : c.ok === false ? "✕" : "?"}
                      </span>{" "}
                      {c.detail}
                    </div>
                  ))}
                  {originCheck!.official?.url && (
                    <a
                      href={originCheck!.official.url}
                      target="_blank"
                      rel="noreferrer"
                      style={{ fontFamily: font.mono, fontSize: 8.5, color: C.blue, marginTop: 3, display: "inline-block" }}
                    >
                      ↗ CAL FIRE incident page
                    </a>
                  )}
                </>
              )}
            </div>
          )}
        </div>
        <div style={{ display: "flex", borderBottom: `1px solid ${C.line}`, flexShrink: 0 }}>
          {(["intel", "weather", "ledger", "timeline"] as const).map((t) => (
            <span
              key={t}
              onClick={() => setPanelTab(t)}
              style={{
                flex: 1,
                textAlign: "center",
                padding: "6px 0",
                fontFamily: font.disp,
                fontSize: 11,
                letterSpacing: 2,
                fontWeight: 700,
                cursor: "pointer",
                color: panelTab === t ? C.ember : C.dim,
                borderBottom: `2px solid ${panelTab === t ? C.ember : "transparent"}`,
              }}
            >
              {t.toUpperCase()}
            </span>
          ))}
        </div>
        <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {panelTab === "intel" && sel && <IntelPanel sel={sel} toast={toast} />}
          {panelTab === "weather" && sel && <WeatherPanel incidentId={sel.id} />}
          {panelTab === "timeline" && sel && <TimelinePanel incidentId={sel.id} />}
          {panelTab === "ledger" && (
            <>
          {staleCount > 0 && (
            <div
              style={{
                margin: 9,
                padding: 7,
                borderRadius: 5,
                border: `1px solid ${C.warn}44`,
                background: C.warn + "0d",
                fontFamily: font.mono,
                fontSize: 9.5,
                color: C.dim,
              }}
            >
              <span style={{ color: C.warn }}>⏱ STALE CAUGHT</span> — {staleCount} fact
              {staleCount > 1 ? "s" : ""} where observed_at trails ingested_at by &gt;6h; excluded
              from what&apos;s-new logic.
            </div>
          )}
          {(facts ?? []).map((f) => (
            <FactRow key={f.id} f={f} />
          ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------- SOURCES mode ---------------- */

function AddSource({ toast, onAdded }: { toast: (m: string) => void; onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const [tier, setTier] = useState(4);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [busy, setBusy] = useState(false);

  const runProbe = async () => {
    if (!url.trim() || busy) return;
    setBusy(true);
    setProbe(null);
    try {
      const r = await probeSource(url.trim());
      setProbe(r);
      setTier(r.suggested_tier);
    } catch (e) {
      toast(`probe failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const add = async () => {
    if (!probe) return;
    try {
      await createSource({
        name: probe.title || probe.url,
        adapter: probe.adapter,
        config: probe.config,
        tier,
        type: probe.detected === "rss" ? "scrape" : probe.detected,
        enabled: probe.supported,
      });
      toast(`source added${probe.supported ? "" : " (disabled — unsupported)"}`);
      setUrl("");
      setProbe(null);
      onAdded();
    } catch (e) {
      toast(`add failed: ${e instanceof Error ? e.message : e}`);
    }
  };

  return (
    <div style={{ border: `1px solid ${C.ember}44`, borderRadius: 8, background: C.panel, padding: 12, marginBottom: 14 }}>
      <div style={{ fontFamily: font.disp, fontSize: 13, letterSpacing: 1.5, fontWeight: 700, marginBottom: 8 }}>
        ADD SOURCE — paste a feed / page / channel URL, AI probes what it is
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runProbe()}
          placeholder="https://…  (RSS/Atom feed, news page, YouTube channel)"
          style={{ flex: "1 1 300px", background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.txt, fontFamily: font.mono, fontSize: 11, padding: "8px 10px", outline: "none" }}
        />
        <button onClick={runProbe} style={{ background: busy ? C.faint : C.ember, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 12, letterSpacing: 1, padding: "0 14px", cursor: "pointer" }}>
          {busy ? "…" : "PROBE"}
        </button>
      </div>
      {probe && (
        <div style={{ marginTop: 10, borderTop: `1px solid ${C.line}`, paddingTop: 10 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontFamily: font.mono, fontSize: 9, color: probe.supported ? C.ok : C.warn, border: `1px solid ${probe.supported ? C.ok : C.warn}`, borderRadius: 3, padding: "2px 7px" }}>
              {probe.detected.toUpperCase()}
            </span>
            <span style={{ fontSize: 12, fontWeight: 600 }}>{probe.title || probe.url}</span>
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, marginTop: 5 }}>{probe.note}</div>
          {probe.warnings.map((w) => (
            <div key={w} style={{ fontFamily: font.mono, fontSize: 9, color: C.warn, marginTop: 3 }}>⚠ {w}</div>
          ))}
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 9 }}>
            <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint }}>start at tier</span>
            {[3, 4, 5].map((t) => (
              <span key={t} onClick={() => setTier(t)} style={{ fontFamily: font.mono, fontSize: 9, padding: "2px 8px", borderRadius: 3, cursor: "pointer", border: `1px solid ${tier === t ? TIER[t] : C.line}`, color: tier === t ? TIER[t] : C.dim }}>
                T{t}
              </span>
            ))}
            <button
              onClick={add}
              disabled={!probe.supported}
              title={probe.supported ? "" : "adapter not available yet"}
              style={{ marginLeft: "auto", background: probe.supported ? C.ok : C.faint, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 12, letterSpacing: 1, padding: "6px 14px", cursor: probe.supported ? "pointer" : "not-allowed" }}
            >
              ADD SOURCE
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function SuggestedSources({ toast, onAdded }: { toast: (m: string) => void; onAdded: () => void }) {
  const [items, setItems] = useState<SourceSuggestion[] | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    try {
      setItems(await fetchSourceSuggestions());
    } catch {}
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const approve = async (s: SourceSuggestion) => {
    setBusy((b) => ({ ...b, [s.id]: true }));
    try {
      const r = await approveSuggestion(s.id);
      toast(`following ${s.domain} — feed ${r.feed_url}`);
      onAdded();
      refresh();
    } catch (e) {
      toast(`${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy((b) => ({ ...b, [s.id]: false }));
    }
  };

  const dismiss = async (s: SourceSuggestion) => {
    try {
      await dismissSuggestion(s.id);
      setItems((prev) => (prev ?? []).filter((x) => x.id !== s.id));
    } catch (e) {
      toast(`dismiss failed: ${e instanceof Error ? e.message : e}`);
    }
  };

  if (!items?.length) return null;
  return (
    <div style={{ border: `1px solid ${C.violet}44`, borderRadius: 8, background: C.panel, padding: 12, marginBottom: 14 }}>
      <div style={{ fontFamily: font.disp, fontSize: 13, letterSpacing: 1.5, fontWeight: 700, marginBottom: 2 }}>
        <span style={{ color: C.violet }}>SUGGESTED</span> — outlets our research and articles cite that we don&apos;t follow
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, marginBottom: 8 }}>
        approve = find its feed and start following at T4 · nothing is added without your yes ·{" "}
        {items.length} pending
      </div>
      <div style={{ maxHeight: 300, overflowY: "auto" }}>
      {items.map((s) => (
        <div
          key={s.id}
          style={{ display: "flex", gap: 10, alignItems: "center", padding: "7px 4px", borderTop: `1px solid ${C.line}44`, flexWrap: "wrap" }}
        >
          <span style={{ fontSize: 12, fontWeight: 600, minWidth: 160 }}>
            <a href={s.sample_url} target="_blank" rel="noreferrer" style={{ color: C.txt, textDecoration: "none", borderBottom: `1px dotted ${C.dim}` }}>
              {s.domain}
            </a>
          </span>
          <span style={{ fontFamily: font.mono, fontSize: 9, color: C.warn }}>×{s.hit_count} link{s.hit_count === 1 ? "" : "s"}</span>
          <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, flex: "1 1 200px" }}>
            seen in: {s.contexts.join(" · ") || "—"}
          </span>
          <button
            onClick={() => approve(s)}
            disabled={busy[s.id]}
            style={{ background: busy[s.id] ? C.faint : C.ok, color: C.bg, border: "none", borderRadius: 4, fontFamily: font.disp, fontWeight: 700, fontSize: 10.5, letterSpacing: 1, padding: "4px 10px", cursor: "pointer" }}
          >
            {busy[s.id] ? "PROBING…" : "APPROVE"}
          </button>
          <button
            onClick={() => dismiss(s)}
            style={{ background: "transparent", color: C.dim, border: `1px solid ${C.line}`, borderRadius: 4, fontFamily: font.disp, fontSize: 10.5, letterSpacing: 1, padding: "3px 10px", cursor: "pointer" }}
          >
            DISMISS
          </button>
        </div>
      ))}
      </div>
    </div>
  );
}

/* Trust: how a source has actually performed. Lead time is measured against
   the official record on the same incident+kind; correction rate is how often
   a higher tier had to supersede it. */
function TrustCells({ s }: { s: Source }) {
  const lead = s.lead_time_median_s;
  const corr = s.superseded_rate;
  const leadLabel =
    lead == null
      ? "—"
      : lead >= 3600
        ? `+${(lead / 3600).toFixed(1)}h`
        : lead >= 0
          ? `+${Math.round(lead / 60)}m`
          : `${Math.round(lead / 60)}m`;
  return (
    <>
      <span
        style={{ fontFamily: font.mono, fontSize: 10, color: lead == null ? C.faint : lead > 0 ? C.ok : C.dim }}
        title="median lead over the official record on the same incident + kind"
      >
        {leadLabel}
      </span>
      <span
        style={{ fontFamily: font.mono, fontSize: 10, color: corr == null ? C.faint : corr > 0.2 ? C.evac : corr > 0.05 ? C.warn : C.ok }}
        title="share of this source's facts a higher tier had to correct"
      >
        {corr == null ? "—" : `${Math.round(corr * 100)}%`}
      </span>
      <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }} title="facts per day (30-day window)">
        {s.yield_facts_per_day == null ? "—" : s.yield_facts_per_day.toFixed(1)}
      </span>
    </>
  );
}

function SourcesMode({ toast }: { toast: (m: string) => void }) {
  const [refreshKey, setRefreshKey] = useState(0);
  const sources = usePolling(fetchSources, [refreshKey]);
  const [busy, setBusy] = useState(false);
  const cols = "1.9fr .6fr .5fr .7fr .6fr .7fr .6fr .6fr .6fr .9fr";
  const bump = () => setRefreshKey((k) => k + 1);

  const recompute = async () => {
    setBusy(true);
    try {
      const r = await recomputeSourceStats();
      toast(`trust recomputed for ${r.computed} sources — ${r.suggestions.length} suggestion(s)`);
      bump();
    } catch (e) {
      toast(`recompute failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const approve = async (s: Source) => {
    if (s.suggested_tier == null) return;
    try {
      await setSourceTier(s.id, s.suggested_tier);
      toast(`${s.name}: T${s.tier} → T${s.suggested_tier}`);
      bump();
    } catch (e) {
      toast(`tier change failed: ${e instanceof Error ? e.message : e}`);
    }
  };

  const dismiss = async (s: Source) => {
    await dismissTierSuggestion(s.id);
    bump();
  };

  const computedAt = (sources ?? []).find((s) => s.stats_computed_at)?.stats_computed_at ?? null;

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
        <span style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>
          SOURCE MANAGER
        </span>
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>
          trust is earned — measured nightly{computedAt ? ` · last ${fmtStamp(computedAt)}` : ""}
        </span>
        <span
          onClick={busy ? undefined : recompute}
          style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 9, cursor: busy ? "default" : "pointer", color: busy ? C.faint : C.blue, border: `1px solid ${busy ? C.line : C.blue}66`, borderRadius: 4, padding: "3px 9px" }}
        >
          {busy ? "MEASURING…" : "↻ RECOMPUTE TRUST"}
        </span>
      </div>
      <AddSource toast={toast} onAdded={() => setRefreshKey((k) => k + 1)} />
      <SuggestedSources toast={toast} onAdded={() => setRefreshKey((k) => k + 1)} />
      <div style={{ background: C.panel, borderRadius: 8, border: `1px solid ${C.line}`, overflow: "hidden" }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: cols,
            padding: "8px 14px",
            fontFamily: font.mono,
            fontSize: 9,
            color: C.faint,
            letterSpacing: 1,
            borderBottom: `1px solid ${C.line}`,
          }}
        >
          <span>SOURCE</span><span>TYPE</span><span>TIER</span><span>POLL</span><span>ON</span>
          <span>FACTS</span>
          <span title="median lead over official reporting">LEAD</span>
          <span title="corrected by a higher tier">CORR</span>
          <span title="facts per day">YIELD</span>
          <span>LAST</span>
        </div>
        {(sources ?? []).map((s) => (
          <div
            key={s.id}
            className="tr-row"
            style={{
              display: "grid",
              gridTemplateColumns: cols,
              padding: "9px 14px",
              borderBottom: `1px solid ${C.line}44`,
              fontSize: 11.5,
              alignItems: "center",
            }}
          >
            <span>
              {s.name}
              {s.tos_notes && (
                <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 1 }}>
                  {s.tos_notes}
                </div>
              )}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>{s.type.toUpperCase()}</span>
            <span style={{ fontFamily: font.mono, fontSize: 10.5, color: TIER[s.tier], display: "flex", gap: 4, alignItems: "center" }}>
              T{s.tier}
              {s.suggested_tier != null && (
                <span
                  title={
                    s.suggested_tier < s.tier
                      ? "earned a promotion — fast and rarely corrected"
                      : "corrected too often — suggested demotion"
                  }
                  style={{ color: s.suggested_tier < s.tier ? C.ok : C.warn, fontSize: 9 }}
                >
                  {s.suggested_tier < s.tier ? "↑" : "↓"}T{s.suggested_tier}
                </span>
              )}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>
              {s.schedule ? `${s.schedule}s` : "stream"}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: s.enabled ? C.ok : C.evac }}>
              {s.enabled ? "ON" : "OFF"}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>{s.fact_count}</span>
            <TrustCells s={s} />
            <span style={{ display: "flex", gap: 5, alignItems: "center" }}>
              {s.suggested_tier != null ? (
                <>
                  <span
                    onClick={() => approve(s)}
                    style={{ fontFamily: font.mono, fontSize: 8.5, cursor: "pointer", color: C.bg, background: C.ok, borderRadius: 3, padding: "2px 6px" }}
                  >
                    APPROVE
                  </span>
                  <span
                    onClick={() => dismiss(s)}
                    title="keep the current tier"
                    style={{ fontFamily: font.mono, fontSize: 8.5, cursor: "pointer", color: C.dim }}
                  >
                    ✕
                  </span>
                </>
              ) : (
                <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>{fmtTime(s.last_fact_at)}</span>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- historical fire search ---------------- */

function FireSearch({ toast }: { toast: (m: string) => void }) {
  const [q, setQ] = useState("");
  const [year, setYear] = useState("");
  const [results, setResults] = useState<FireSearchResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState<Record<string, boolean>>({});
  const [imported, setImported] = useState<Record<string, boolean>>({});

  const run = async () => {
    if (busy) return;
    setBusy(true);
    try {
      setResults(await searchFires(q.trim(), year ? Number(year) : undefined));
    } catch (e) {
      toast(`search failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const doImport = async (r: FireSearchResult) => {
    setImporting((s) => ({ ...s, [r.ref_id]: true }));
    try {
      const res = await importFire(r.ref_id);
      setImported((s) => ({ ...s, [r.ref_id]: true }));
      toast(`imported ${res.name} (${res.status}, ${res.facts} facts)`);
    } catch (e) {
      toast(`import failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setImporting((s) => ({ ...s, [r.ref_id]: false }));
    }
  };

  return (
    <div style={{ border: `1px solid ${C.line}`, borderRadius: 8, background: C.panel, marginBottom: 14 }}>
      <div style={{ display: "flex", gap: 6, padding: 10, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontFamily: font.disp, fontSize: 13, letterSpacing: 1.5, fontWeight: 700, color: C.ember }}>
          SEARCH HISTORICAL FIRES
        </span>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="fire name or county (CAL FIRE closed incidents)…"
          style={{
            flex: 1,
            minWidth: 180,
            background: C.bg,
            border: `1px solid ${C.line}`,
            borderRadius: 5,
            color: C.txt,
            fontFamily: font.mono,
            fontSize: 10.5,
            padding: "6px 8px",
            outline: "none",
          }}
        />
        <input
          value={year}
          onChange={(e) => setYear(e.target.value.replace(/\D/g, "").slice(0, 4))}
          placeholder="year"
          style={{
            width: 56,
            background: C.bg,
            border: `1px solid ${C.line}`,
            borderRadius: 5,
            color: C.txt,
            fontFamily: font.mono,
            fontSize: 10.5,
            padding: "6px 8px",
            outline: "none",
          }}
        />
        <button
          onClick={run}
          style={{
            background: busy ? C.faint : C.ember,
            color: C.bg,
            border: "none",
            borderRadius: 5,
            fontFamily: font.disp,
            fontWeight: 700,
            fontSize: 12,
            letterSpacing: 1,
            padding: "0 14px",
            cursor: "pointer",
          }}
        >
          {busy ? "…" : "SEARCH"}
        </button>
      </div>
      {results !== null && (
        <div style={{ borderTop: `1px solid ${C.line}`, maxHeight: 260, overflowY: "auto" }}>
          {!results.length && (
            <div style={{ padding: 12, fontFamily: font.mono, fontSize: 10, color: C.faint }}>
              no matching historical fires
            </div>
          )}
          {results.map((r) => {
            const done = r.already_in_ledger || imported[r.ref_id];
            return (
              <div
                key={r.ref_id}
                className="tr-row"
                style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 11px", borderBottom: `1px solid ${C.line}44` }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>
                    {r.name}
                    {r.is_active && (
                      <span style={{ color: C.emberHot, fontFamily: font.mono, fontSize: 8.5, marginLeft: 6 }}>ACTIVE</span>
                    )}
                  </div>
                  <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>
                    {r.county ? `${r.county} County · ` : ""}
                    {r.year ?? "—"} · {r.acreage != null ? `${r.acreage.toLocaleString()} ac` : "size ?"} ·{" "}
                    {r.containment_pct != null ? `${r.containment_pct}% cont` : ""}
                  </div>
                </div>
                <button
                  onClick={() => !done && doImport(r)}
                  disabled={done || importing[r.ref_id]}
                  style={{
                    background: done ? "transparent" : C.ok,
                    color: done ? C.faint : C.bg,
                    border: done ? `1px solid ${C.line}` : "none",
                    borderRadius: 5,
                    fontFamily: font.disp,
                    fontWeight: 700,
                    fontSize: 11,
                    letterSpacing: 1,
                    padding: "5px 12px",
                    cursor: done ? "default" : "pointer",
                  }}
                >
                  {done ? "IN LEDGER" : importing[r.ref_id] ? "…" : "IMPORT"}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---------------- ARCHIVE mode ---------------- */

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

/* ARCHIVE — fires filed by when they burned, because that is how anyone looks
   for one later ("the Butte County fire in July"). Year → month → incident,
   with counts at every level so the shape of a season is visible at a glance. */
function ArchiveMode({ toast }: { toast: (m: string) => void }) {
  const incidents = usePolling(fetchIncidents);
  const [q, setQ] = useState("");
  const [county, setCounty] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const all = useMemo(() => incidents ?? [], [incidents]);
  const counties = useMemo(
    () => [...new Set(all.map((i) => i.county).filter(Boolean))].sort() as string[],
    [all],
  );

  const filtered = useMemo(
    () =>
      all.filter((i) => {
        if (county && i.county !== county) return false;
        if (statusFilter && i.status !== statusFilter) return false;
        if (q && !`${i.canonical_name} ${i.county ?? ""}`.toLowerCase().includes(q.toLowerCase()))
          return false;
        return true;
      }),
    [all, q, county, statusFilter],
  );

  // group by year → month of the fire's start (earliest observed fact),
  // falling back to when we first recorded it
  const tree = useMemo(() => {
    const years = new Map<string, Map<number, Incident[]>>();
    for (const inc of filtered) {
      const when = new Date(inc.started_at ?? inc.created_at);
      if (isNaN(when.getTime())) continue;
      const y = String(when.getUTCFullYear());
      const m = when.getUTCMonth();
      const months = years.get(y) ?? new Map<number, Incident[]>();
      months.set(m, [...(months.get(m) ?? []), inc]);
      years.set(y, months);
    }
    return [...years.entries()]
      .sort((a, b) => b[0].localeCompare(a[0]))
      .map(([year, months]) => ({
        year,
        count: [...months.values()].reduce((n, v) => n + v.length, 0),
        months: [...months.entries()]
          .sort((a, b) => b[0] - a[0])
          .map(([m, list]) => ({
            month: m,
            list: [...list].sort((a, b) =>
              (b.started_at ?? "").localeCompare(a.started_at ?? ""),
            ),
          })),
      }));
  }, [filtered]);

  const acresTotal = filtered.reduce((n, i) => n + (i.acreage ?? 0), 0);
  const inputStyle = {
    background: C.bg,
    border: `1px solid ${C.line}`,
    borderRadius: 4,
    color: C.txt,
    fontFamily: font.mono,
    fontSize: 10,
    padding: "5px 8px",
    outline: "none",
  } as const;

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 10, flexWrap: "wrap" }}>
        <span style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>ARCHIVE</span>
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>
          filed by when they burned · append-only ledger · frozen fires immutable
        </span>
        <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 9.5, color: C.faint }}>
          {filtered.length} fire{filtered.length === 1 ? "" : "s"}
          {acresTotal > 0 && ` · ${Math.round(acresTotal).toLocaleString()} acres`}
        </span>
      </div>

      {/* browse tools */}
      <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="⌕ filter archived fires by name or county"
          style={{ ...inputStyle, flex: "1 1 240px" }}
        />
        <select value={county} onChange={(e) => setCounty(e.target.value)} style={{ ...inputStyle, color: county ? C.ember : C.dim }}>
          <option value="">all counties</option>
          {counties.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} style={{ ...inputStyle, color: statusFilter ? C.ember : C.dim }}>
          <option value="">any status</option>
          {["active", "contained", "candidate", "frozen"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <FireSearch toast={toast} />

      {tree.map(({ year, count, months }) => (
        <div key={year} style={{ marginBottom: 14 }}>
          <div
            onClick={() => setCollapsed((s) => ({ ...s, [year]: !s[year] }))}
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 9,
              cursor: "pointer",
              borderBottom: `1px solid ${C.ember}44`,
              paddingBottom: 4,
              marginBottom: 8,
            }}
          >
            <span style={{ fontFamily: font.disp, fontSize: 18, fontWeight: 700, color: C.ember, letterSpacing: 2 }}>
              {collapsed[year] ? "▸" : "▾"} {year}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim }}>
              {count} fire{count === 1 ? "" : "s"}
            </span>
          </div>
          {!collapsed[year] &&
            months.map(({ month, list }) => (
              <div key={month} style={{ marginBottom: 10 }}>
                <div style={{ fontFamily: font.mono, fontSize: 9.5, letterSpacing: 1.5, color: C.dim, margin: "0 0 5px 4px" }}>
                  {MONTHS[month]} {year} · {list.length}
                </div>
                {list.map((inc) => (
                  <div
                    key={inc.id}
                    className="tr-row"
                    style={{
                      background: C.panel,
                      border: `1px solid ${hasActiveEvac(inc) ? C.evac + "66" : C.line}`,
                      borderRadius: 7,
                      padding: "9px 13px",
                      marginBottom: 6,
                      borderLeft: `3px solid ${inc.status === "active" ? C.emberHot : inc.status === "frozen" ? C.dim : C.warn}`,
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                      <span style={{ fontFamily: font.disp, fontSize: 15, fontWeight: 700 }}>
                        {inc.canonical_name}
                      </span>
                      <span style={{ display: "flex", gap: 7, alignItems: "baseline" }}>
                        {hasActiveEvac(inc) && (
                          <span style={{ fontFamily: font.mono, fontSize: 8, color: C.evac, border: `1px solid ${C.evac}66`, borderRadius: 3, padding: "1px 5px" }}>
                            EVAC
                          </span>
                        )}
                        <span style={{ fontFamily: font.mono, fontSize: 9, color: inc.status === "frozen" ? C.dim : C.emberHot }}>
                          {inc.status.toUpperCase()}
                        </span>
                      </span>
                    </div>
                    <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, marginTop: 3, display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {inc.county && <span>{inc.county} County</span>}
                      {inc.acreage != null && <span>{inc.acreage.toLocaleString()} ac</span>}
                      {inc.containment_pct != null && <span>{inc.containment_pct}% cont</span>}
                      <span>{inc.fact_count} facts</span>
                      <span title={`started ${fmtDate(inc.started_at ?? inc.created_at)}`}>
                        {fmtTime(inc.started_at ?? inc.created_at)}
                      </span>
                      <span style={{ color: C.faint }}>
                        {inc.calfire_id ? "CAL FIRE" : inc.irwin_id ? "IRWIN" : "operator"}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            ))}
        </div>
      ))}

      {!tree.length && (
        <div style={{ padding: 16, fontFamily: font.mono, fontSize: 10, color: C.faint }}>
          {all.length ? "nothing matches these filters" : "no fires archived yet"}
        </div>
      )}
    </div>
  );
}

/* ---------------- shell ---------------- */

const MODES: [string, string, boolean][] = [
  ["ops", "OPS", true],
  ["news", "NEWSROOM", true],
  ["watch", "WATCH", true],
  ["archive", "ARCHIVE", true],
  ["sources", "SOURCES", true],
  ["analytics", "ANALYTICS", true],
];

export default function Workstation() {
  const [mode, setMode] = useState("ops");
  const health = usePolling(fetchHealth);
  const [clock, setClock] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [uiKey, setUiKey] = useState(0); // bump to re-render after settings change
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const toast = useCallback((m: string) => {
    setToastMsg(m);
    setTimeout(() => setToastMsg(null), 4000);
  }, []);
  useEffect(() => {
    loadDisplayPrefs(); // cached prefs paint immediately…
    syncDisplayPrefs().then(() => setUiKey((k) => k + 1)); // …server is the truth
  }, []);
  useEffect(() => {
    const tick = () => setClock(fmtStamp(new Date().toISOString()));
    tick();
    const id = setInterval(tick, 15000);
    return () => clearInterval(id);
  }, [uiKey]);

  const dots: [string, boolean | null][] = [
    ["FEEDS", health ? (health.queue.dead ?? 0) < 5 : null],
    ["API", health ? health.api : null],
  ];

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
          padding: "7px 14px",
          borderBottom: `1px solid ${C.line}`,
          background: C.panel,
          flexShrink: 0,
        }}
      >
        <div style={{ fontFamily: font.disp, fontWeight: 700, fontSize: 19, letterSpacing: 1, color: C.ember }}>
          TOAST REPORT
        </div>
        <div style={{ display: "flex", gap: 2, background: C.bg, borderRadius: 6, padding: 2 }}>
          {MODES.map(([k, label, ready]) => (
            <div
              key={k}
              onClick={() => ready && setMode(k)}
              title={ready ? undefined : "arrives in a later phase"}
              style={{
                fontFamily: font.disp,
                fontSize: 13,
                fontWeight: 700,
                letterSpacing: 1.5,
                padding: "5px 13px",
                borderRadius: 5,
                cursor: ready ? "pointer" : "not-allowed",
                background: mode === k ? C.panelUp : "transparent",
                color: mode === k ? C.ember : ready ? C.dim : C.faint,
              }}
            >
              {label}
            </div>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <AlertBell />
        <span
          onClick={() => setShowSettings(true)}
          title="options — time display, local AI models"
          style={{ cursor: "pointer", fontSize: 13, color: C.dim, userSelect: "none" }}
        >
          ⚙
        </span>
        <div style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>{clock}</div>
        {dots.map(([label, ok]) => (
          <div
            key={label}
            style={{ display: "flex", alignItems: "center", gap: 4, fontFamily: font.mono, fontSize: 9.5, color: C.dim }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 99,
                background: ok == null ? C.faint : ok ? C.ok : C.evac,
              }}
            />
            {label}
          </div>
        ))}
        {health && (
          <Chip color={C.blue} title="job queue">
            {Object.entries(health.queue)
              .map(([k, v]) => `${k}:${v}`)
              .join(" ") || "queue idle"}
          </Chip>
        )}
      </div>

      {mode === "ops" && <OpsMode toast={toast} />}
      {mode === "news" && <NewsroomMode toast={toast} />}
      {mode === "watch" && <WatchMode toast={toast} />}
      {mode === "archive" && <ArchiveMode toast={toast} />}
      {mode === "sources" && <SourcesMode toast={toast} />}
      {mode === "analytics" && <AnalyticsMode />}

      {showSettings && (
        <SettingsPanel
          onClose={() => setShowSettings(false)}
          onApplied={() => setUiKey((k) => k + 1)}
          toast={toast}
        />
      )}

      {toastMsg && (
        <div
          className="tr-in"
          style={{
            position: "fixed",
            bottom: 18,
            left: "50%",
            transform: "translateX(-50%)",
            background: C.panelUp,
            border: `1px solid ${C.ok}66`,
            color: C.txt,
            fontFamily: font.mono,
            fontSize: 11,
            padding: "9px 16px",
            borderRadius: 7,
            boxShadow: "0 8px 30px #000c",
            zIndex: 50,
          }}
        >
          ✓ {toastMsg}
        </div>
      )}
    </div>
  );
}
