"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Incident,
  IncidentTimeline,
  fetchIncidents,
  fetchFireTimeline,
  hasActiveEvac,
} from "@/lib/api";
import { C, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

/* TIMELINE: one fire's whole life on a single axis, straight from the ledger.

   Start to containment, every acreage and containment revision, evacuation
   notices verbatim, cause, damage, road closures, fire weather, resources,
   perimeter maps, and each article at its release time. Nothing invented — each
   row is a real record carrying its source and tier, newest or oldest first. */

const POLL_MS = 20_000;

const CAT: Record<string, { color: string; glyph: string; label: string }> = {
  start: { color: C.ok, glyph: "◉", label: "START" },
  acreage: { color: C.ember, glyph: "△", label: "ACREAGE" },
  containment: { color: C.ok, glyph: "▣", label: "CONTAINMENT" },
  evac: { color: C.evac, glyph: "⚠", label: "EVACUATION" },
  cause: { color: C.dim, glyph: "?", label: "CAUSE" },
  damage: { color: C.emberHot, glyph: "✖", label: "DAMAGE" },
  road: { color: C.warn, glyph: "⛌", label: "ROAD / CLOSURE" },
  weather: { color: C.blue, glyph: "✻", label: "FIRE WEATHER" },
  resource: { color: C.violet, glyph: "▲", label: "RESOURCES" },
  article: { color: C.blue, glyph: "▤", label: "ARTICLE" },
  report: { color: C.violet, glyph: "✎", label: "REPORT" },
  perimeter: { color: C.ember, glyph: "◯", label: "PERIMETER" },
  status: { color: C.faint, glyph: "●", label: "LIFECYCLE" },
  note: { color: C.dim, glyph: "·", label: "NOTE" },
};
const catOf = (c: string) => CAT[c] ?? CAT.note;

function durationText(start: string, end: string | null): string {
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const d = Math.max(0, e - s);
  const days = Math.floor(d / 86_400_000);
  const hrs = Math.floor((d % 86_400_000) / 3_600_000);
  if (days > 0) return `${days}d ${hrs}h`;
  const mins = Math.floor((d % 3_600_000) / 60_000);
  return hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
}

function statusColor(inc: { status: string; evac_status?: string | null }): string {
  if (inc.evac_status && hasActiveEvac({ evac_status: inc.evac_status })) return C.evac;
  if (inc.status === "contained" || inc.status === "frozen") return C.dim;
  return C.emberHot;
}

export default function TimelineMode({
  toast,
  focusIncident,
}: {
  toast: (m: string) => void;
  focusIncident?: { id: string; nonce: number } | null;
}) {
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<IncidentTimeline | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [newestFirst, setNewestFirst] = useState(false);
  const handledNonce = useRef<number | null>(null);

  useEffect(() => {
    let live = true;
    const tick = () => fetchIncidents().then((i) => live && setIncidents(i)).catch(() => undefined);
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  // active + under-evac fires first — the ones an operator is actually watching
  const fires = useMemo(() => {
    const list = (incidents ?? []).filter(
      (i) => !search.trim() || i.canonical_name.toLowerCase().includes(search.toLowerCase()),
    );
    return [...list].sort((a, b) => {
      const rank = (i: Incident) =>
        (hasActiveEvac(i) ? 0 : 1) * 10 + (i.status === "active" ? 0 : i.status === "candidate" ? 1 : 2);
      const r = rank(a) - rank(b);
      return r !== 0 ? r : (b.last_observed || "").localeCompare(a.last_observed || "");
    });
  }, [incidents, search]);

  // default to the first fire once loaded
  useEffect(() => {
    if (!selected && fires.length) setSelected(fires[0].id);
  }, [fires, selected]);

  // jump to a fire handed in from elsewhere (e.g. an OPS "see timeline" action)
  useEffect(() => {
    if (focusIncident && handledNonce.current !== focusIncident.nonce) {
      handledNonce.current = focusIncident.nonce;
      setSelected(focusIncident.id);
    }
  }, [focusIncident]);

  useEffect(() => {
    if (!selected) return;
    let live = true;
    setLoading(true);
    const load = () =>
      fetchFireTimeline(selected)
        .then((t) => live && setTimeline(t))
        .catch(() => live && toast("could not load that fire's timeline"))
        .finally(() => live && setLoading(false));
    load();
    const t = setInterval(load, POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [selected, toast]);

  const events = useMemo(() => {
    const evs = (timeline?.events ?? []).filter((e) => !hidden.has(e.category));
    return newestFirst ? [...evs].reverse() : evs;
  }, [timeline, hidden, newestFirst]);

  // group events by calendar day for readable section headers
  const grouped = useMemo(() => {
    const out: { day: string; items: typeof events }[] = [];
    for (const e of events) {
      const day = e.at.slice(0, 10);
      const last = out[out.length - 1];
      if (last && last.day === day) last.items.push(e);
      else out.push({ day, items: [e] });
    }
    return out;
  }, [events]);

  const toggleCat = (c: string) =>
    setHidden((prev) => {
      const n = new Set(prev);
      if (n.has(c)) n.delete(c);
      else n.add(c);
      return n;
    });

  const inc = timeline?.incident;

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      {/* fire picker */}
      <div style={{ width: 300, minWidth: 300, borderRight: `1px solid ${C.line}`, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: "10px 12px", borderBottom: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.faint }}>
            FIRES {incidents ? `· ${fires.length}` : ""}
          </div>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="⌕ find a fire…"
            style={{ width: "100%", marginTop: 6, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.txt, fontFamily: font.mono, fontSize: 10, padding: "4px 8px" }}
          />
        </div>
        <div style={{ overflowY: "auto", flex: 1 }}>
          {fires.map((f) => (
            <div
              key={f.id}
              onClick={() => setSelected(f.id)}
              className="tr-row"
              style={{
                display: "flex", gap: 8, alignItems: "center", padding: "7px 12px",
                cursor: "pointer", borderBottom: `1px solid ${C.line}44`,
                background: selected === f.id ? C.panelUp : "transparent",
              }}
            >
              <span style={{ width: 7, height: 7, borderRadius: 4, background: statusColor(f), flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: font.mono, fontSize: 11, color: C.txt, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {f.canonical_name}
                </div>
                <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint }}>
                  {f.county ? `${f.county} County · ` : ""}{f.status}
                  {hasActiveEvac(f) ? " · EVAC" : ""}
                </div>
              </div>
            </div>
          ))}
          {incidents && fires.length === 0 && (
            <div style={{ padding: 14, fontFamily: font.mono, fontSize: 10, color: C.dim }}>no fires match</div>
          )}
        </div>
      </div>

      {/* the timeline */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {!inc ? (
          <div style={{ padding: 20, fontFamily: font.mono, fontSize: 11, color: C.dim }}>
            {loading ? "loading timeline…" : "select a fire"}
          </div>
        ) : (
          <>
            {/* header */}
            <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.line}` }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontFamily: font.disp, fontSize: 18, color: C.txt, letterSpacing: 1 }}>
                  {inc.name}
                </span>
                <span style={{ fontFamily: font.mono, fontSize: 10, color: statusColor({ status: inc.status, evac_status: null }) }}>
                  {inc.status.toUpperCase()}
                </span>
                {inc.county && <span style={{ fontFamily: font.mono, fontSize: 10, color: C.faint }}>{inc.county} County</span>}
              </div>
              <div style={{ display: "flex", gap: 16, marginTop: 6, fontFamily: font.mono, fontSize: 10, color: C.dim, flexWrap: "wrap" }}>
                <span>▶ started {timeline.start ? fmtStamp(timeline.start) : "—"}</span>
                <span>
                  {timeline.ongoing ? (
                    <span style={{ color: C.emberHot }}>● burning {durationText(timeline.start ?? "", null)}</span>
                  ) : (
                    <span style={{ color: C.ok }}>■ contained {timeline.finish ? fmtStamp(timeline.finish) : ""} ({durationText(timeline.start ?? "", timeline.finish)})</span>
                  )}
                </span>
                <span>{timeline.event_count} events</span>
              </div>
              {/* category filters */}
              <div style={{ display: "flex", gap: 5, marginTop: 9, flexWrap: "wrap", alignItems: "center" }}>
                {Object.entries(timeline.counts)
                  .sort((a, b) => b[1] - a[1])
                  .map(([cat, n]) => {
                    const meta = catOf(cat);
                    const off = hidden.has(cat);
                    return (
                      <span
                        key={cat}
                        onClick={() => toggleCat(cat)}
                        title={off ? "show" : "hide"}
                        style={{
                          fontFamily: font.mono, fontSize: 8.5, cursor: "pointer",
                          padding: "2px 7px", borderRadius: 3,
                          border: `1px solid ${off ? C.line : meta.color + "88"}`,
                          color: off ? C.faint : meta.color,
                          opacity: off ? 0.5 : 1,
                        }}
                      >
                        {meta.glyph} {meta.label} {n}
                      </span>
                    );
                  })}
                <span
                  onClick={() => setNewestFirst((v) => !v)}
                  style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 8.5, cursor: "pointer", padding: "2px 8px", borderRadius: 3, border: `1px solid ${C.line}`, color: C.dim }}
                >
                  {newestFirst ? "↓ NEWEST FIRST" : "↑ OLDEST FIRST"}
                </span>
              </div>
            </div>

            {/* body */}
            <div style={{ overflowY: "auto", flex: 1, padding: "6px 0 30px" }}>
              {grouped.length === 0 && (
                <div style={{ padding: 20, fontFamily: font.mono, fontSize: 10, color: C.dim }}>
                  no events in the selected categories
                </div>
              )}
              {grouped.map(({ day, items }) => (
                <div key={day}>
                  <div style={{ position: "sticky", top: 0, background: C.bg, zIndex: 1, padding: "4px 16px", fontFamily: font.mono, fontSize: 9, letterSpacing: 1.5, color: C.faint, borderBottom: `1px solid ${C.line}55` }}>
                    {day}
                  </div>
                  {items.map((e, i) => {
                    const meta = catOf(e.category);
                    return (
                      <div key={`${day}-${i}`} style={{ display: "flex", gap: 10, padding: "6px 16px 6px 14px", position: "relative" }}>
                        {/* rail + node */}
                        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: 16, flexShrink: 0 }}>
                          <span style={{ color: meta.color, fontSize: 13, lineHeight: 1 }}>{meta.glyph}</span>
                          <span style={{ flex: 1, width: 1, background: C.line, marginTop: 2 }} />
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                            <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, minWidth: 58 }}>
                              {fmtStamp(e.at).split(" ").slice(-2).join(" ")}
                            </span>
                            <span
                              style={{
                                fontFamily: font.mono, fontSize: 11, fontWeight: 700,
                                color: e.superseded ? C.faint : meta.color,
                                textDecoration: e.superseded ? "line-through" : "none",
                              }}
                            >
                              {e.title}
                            </span>
                            {e.starred && <span style={{ color: C.warn, fontSize: 9 }}>★</span>}
                            {e.url && (
                              <a href={e.url} target="_blank" rel="noreferrer" style={{ fontFamily: font.mono, fontSize: 8.5, color: C.blue, textDecoration: "none", border: `1px solid ${C.blue}66`, borderRadius: 3, padding: "0 5px" }}>
                                OPEN ↗
                              </a>
                            )}
                          </div>
                          {e.detail && e.detail !== e.title && (
                            <div style={{ fontFamily: font.mono, fontSize: 9.5, color: e.superseded ? C.faint : C.dim, marginTop: 2, lineHeight: 1.5, wordBreak: "break-word" }}>
                              {e.detail.length > 320 ? e.detail.slice(0, 320) + "…" : e.detail}
                            </div>
                          )}
                          <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginTop: 2 }}>
                            {e.source ?? "—"}
                            {e.tier ? ` · T${e.tier}` : ""}
                            {e.superseded ? " · superseded" : ""}
                            {" · "}
                            {age(e.at)} ago
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
