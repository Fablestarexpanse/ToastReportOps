"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AdjudicationItem,
  Fact,
  FireSearchResult,
  ProbeResult,
  analyzeUrl,
  createSource,
  fetchAdjudication,
  fetchHealth,
  fetchIncidentFacts,
  fetchIncidents,
  fetchIntake,
  fetchSources,
  importFire,
  probeSource,
  resolveAdjudication,
  searchFires,
} from "@/lib/api";
import { C, TIER, TIER_TITLES, confColor, font } from "@/lib/theme";
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

const fmtTime = (iso: string | null) =>
  iso ? iso.slice(11, 16) + "Z" : "—";
const fmtDate = (iso: string | null) => (iso ? iso.slice(0, 16).replace("T", " ") + "Z" : "—");

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

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontFamily: font.disp,
        fontSize: 12,
        letterSpacing: 2,
        color: C.dim,
        padding: "8px 12px 4px",
      }}
    >
      {children}
    </div>
  );
}

function FactRow({ f, showIncident }: { f: Fact; showIncident?: boolean }) {
  const superseded = f.superseded_by != null;
  return (
    <div
      className="tr-row"
      style={{
        padding: "7px 10px",
        borderBottom: `1px solid ${C.line}55`,
        display: "flex",
        gap: 8,
        opacity: superseded ? 0.55 : 1,
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
          <span title={f.observed_at}>{fmtTime(f.observed_at)}</span>
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {f.source_name}
          </span>
          {showIncident && f.incident_name && (
            <span style={{ color: C.ember }}>{f.incident_name}</span>
          )}
          <span style={{ marginLeft: "auto", color: confColor(f.confidence) }}>
            {Math.round(f.confidence * 100)}%
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
        <div style={{ fontFamily: font.mono, fontSize: 9, marginTop: 3, display: "flex", gap: 8 }}>
          <span style={{ color: C.faint }}>{f.kind}</span>
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
}: {
  items: AdjudicationItem[];
  onResolve: (factId: string, incidentId: string, name: string) => void;
}) {
  if (!items.length) return null;
  return (
    <div style={{ margin: "0 10px 10px", border: `1px solid ${C.violet}44`, borderRadius: 7, background: C.panel }}>
      <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.violet, padding: "7px 11px 3px" }}>
        ADJUDICATION QUEUE · {items.length} awaiting attribution
      </div>
      <div style={{ maxHeight: 180, overflowY: "auto" }}>
        {items.map(({ fact, candidates }) => (
          <div key={fact.id} style={{ padding: "6px 11px", borderTop: `1px solid ${C.line}44` }}>
            <div style={{ fontSize: 11.5 }}>{fact.content}</div>
            <div style={{ display: "flex", gap: 6, marginTop: 4, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>
                T{fact.tier} · {fact.source_name} · attach to:
              </span>
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
              {!candidates.length && (
                <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>
                  no candidates scored — leave for Phase 5 watch board
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- OPS mode ---------------- */

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
  const [selected, setSelected] = useState<string | null>(null);
  const [tiers, setTiers] = useState<Record<number, boolean>>({ 1: true, 2: true, 3: true, 4: true, 5: true });
  const [kw, setKw] = useState("");
  const facts = usePolling(
    () => (selected ? fetchIncidentFacts(selected) : Promise.resolve([] as Fact[])),
    [selected],
  );

  useEffect(() => {
    if (!selected && incidents?.length) setSelected(incidents[0].id);
  }, [incidents, selected]);

  const shown = useMemo(
    () =>
      (intake ?? []).filter((f) => {
        if (!tiers[f.tier]) return false;
        if (kw && !(f.content + (f.source_name ?? "") + (f.incident_name ?? ""))
          .toLowerCase()
          .includes(kw.toLowerCase()))
          return false;
        return true;
      }),
    [intake, tiers, kw],
  );

  const sel = incidents?.find((i) => i.id === selected);
  const staleCount = facts?.filter((f) => f.stale).length ?? 0;

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
          <input
            value={kw}
            onChange={(e) => setKw(e.target.value)}
            placeholder="⌕ keyword"
            style={{
              flex: 1,
              minWidth: 70,
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
          <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>
            {shown.length}/{intake?.length ?? 0}
          </span>
        </div>
        <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {shown.map((f, i) => (
            <div key={f.id} className={i === 0 ? "tr-in" : undefined}>
              <FactRow f={f} showIncident />
            </div>
          ))}
          {!shown.length && (
            <div style={{ padding: 16, fontFamily: font.mono, fontSize: 10, color: C.faint }}>
              waiting for intake…
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
        />
        <div style={{ flex: 1, minHeight: 0 }}>
          <MapPanel incidents={incidents ?? []} selected={selected} onSelect={setSelected} />
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
          <div style={{ fontFamily: font.disp, fontSize: 20, fontWeight: 700 }}>
            {sel?.canonical_name ?? "—"}
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 10, color: C.dim, marginTop: 2 }}>
            {sel ? `${sel.county ?? "?"} County · ${sel.status} · ${sel.fact_count} facts` : "select an incident"}
          </div>
        </div>
        <SectionLabel>LEDGER · bi-temporal · append-only</SectionLabel>
        <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
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

function SourcesMode({ toast }: { toast: (m: string) => void }) {
  const [refreshKey, setRefreshKey] = useState(0);
  const sources = usePolling(fetchSources, [refreshKey]);
  const cols = "2.2fr .7fr .5fr .8fr .8fr 1.1fr .7fr";
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
        <span style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>
          SOURCE MANAGER
        </span>
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>
          trust is earned — add feeds below; performance-based promotions land in Phase 5
        </span>
      </div>
      <AddSource toast={toast} onAdded={() => setRefreshKey((k) => k + 1)} />
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
          <span>SOURCE</span><span>TYPE</span><span>TIER</span><span>POLL</span><span>ENABLED</span><span>FACTS</span><span>LAST</span>
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
            <span style={{ fontFamily: font.mono, fontSize: 10.5, color: TIER[s.tier] }}>T{s.tier}</span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>
              {s.schedule ? `${s.schedule}s` : "stream"}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: s.enabled ? C.ok : C.evac }}>
              {s.enabled ? "ON" : "OFF"}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>{s.fact_count}</span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>{fmtTime(s.last_fact_at)}</span>
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

function ArchiveMode({ toast }: { toast: (m: string) => void }) {
  const incidents = usePolling(fetchIncidents);
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
        <span style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>ARCHIVE</span>
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>
          append-only ledger · frozen fires immutable · import older fires below
        </span>
      </div>
      <FireSearch toast={toast} />
      {(incidents ?? []).map((inc) => (
        <div
          key={inc.id}
          className="tr-row"
          style={{
            background: C.panel,
            border: `1px solid ${C.line}`,
            borderRadius: 7,
            padding: "10px 13px",
            marginBottom: 8,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <span style={{ fontFamily: font.disp, fontSize: 16, fontWeight: 700 }}>
              {inc.canonical_name}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 9, color: inc.status === "frozen" ? C.dim : C.emberHot }}>
              {inc.status.toUpperCase()}
            </span>
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, marginTop: 3 }}>
            {inc.county ? `${inc.county} County · ` : ""}
            {inc.fact_count} facts · first seen {fmtDate(inc.created_at)} ·{" "}
            {inc.calfire_id ? "CAL FIRE" : inc.irwin_id ? "IRWIN " + inc.irwin_id.slice(0, 8) + "…" : "—"}
          </div>
        </div>
      ))}
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
];

export default function Workstation() {
  const [mode, setMode] = useState("ops");
  const health = usePolling(fetchHealth);
  const [clock, setClock] = useState("");
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const toast = useCallback((m: string) => {
    setToastMsg(m);
    setTimeout(() => setToastMsg(null), 4000);
  }, []);
  useEffect(() => {
    const tick = () =>
      setClock(
        new Date()
          .toLocaleString("en-US", {
            weekday: "short",
            month: "short",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })
          .toUpperCase(),
      );
    tick();
    const id = setInterval(tick, 15000);
    return () => clearInterval(id);
  }, []);

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
