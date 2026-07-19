"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Fact,
  fetchHealth,
  fetchIncidentFacts,
  fetchIncidents,
  fetchIntake,
  fetchSources,
} from "@/lib/api";
import { C, TIER, TIER_TITLES, confColor, font } from "@/lib/theme";

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

/* ---------------- OPS mode ---------------- */

function OpsMode() {
  const incidents = usePolling(fetchIncidents);
  const intake = usePolling(fetchIntake);
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

      {/* incident list */}
      <div style={{ background: C.bg, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <SectionLabel>ACTIVE INCIDENTS — CALIFORNIA (map lands in Phase 3)</SectionLabel>
        <div style={{ flex: 1, overflowY: "auto", padding: "0 10px 10px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(280px,1fr))", gap: 8 }}>
            {(incidents ?? []).map((inc) => (
              <div
                key={inc.id}
                onClick={() => setSelected(inc.id)}
                className="tr-row"
                style={{
                  background: C.panel,
                  border: `1px solid ${inc.id === selected ? C.ember : C.line}`,
                  borderRadius: 7,
                  padding: "9px 11px",
                  cursor: "pointer",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <span style={{ fontFamily: font.disp, fontSize: 15, fontWeight: 700 }}>
                    {inc.canonical_name}
                  </span>
                  <span
                    style={{
                      fontFamily: font.mono,
                      fontSize: 8.5,
                      color: inc.status === "active" ? C.emberHot : C.dim,
                    }}
                  >
                    {inc.status.toUpperCase()}
                  </span>
                </div>
                <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, marginTop: 3 }}>
                  {inc.county ? `${inc.county} County · ` : ""}
                  {inc.fact_count} facts · last {fmtTime(inc.last_observed)}
                </div>
              </div>
            ))}
          </div>
          {!incidents?.length && (
            <div style={{ padding: 16, fontFamily: font.mono, fontSize: 10, color: C.faint }}>
              no incidents yet — waiting for first WFIGS poll
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

function SourcesMode() {
  const sources = usePolling(fetchSources);
  const cols = "2.2fr .7fr .5fr .8fr .8fr 1.1fr .7fr";
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
        <span style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>
          SOURCE MANAGER
        </span>
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>
          trust is earned — performance stats land in Phase 2, AI probe in Phase 3
        </span>
      </div>
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

/* ---------------- ARCHIVE mode ---------------- */

function ArchiveMode() {
  const incidents = usePolling(fetchIncidents);
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
        <span style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>ARCHIVE</span>
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>
          append-only ledger · frozen fires immutable · semantic search lands in Phase 2
        </span>
      </div>
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
            {inc.fact_count} facts · first seen {fmtDate(inc.created_at)} · IRWIN{" "}
            {inc.irwin_id ? inc.irwin_id.slice(0, 8) + "…" : "—"}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ---------------- shell ---------------- */

const MODES: [string, string, boolean][] = [
  ["ops", "OPS", true],
  ["news", "NEWSROOM", false],
  ["watch", "WATCH", false],
  ["archive", "ARCHIVE", true],
  ["sources", "SOURCES", true],
];

export default function Workstation() {
  const [mode, setMode] = useState("ops");
  const health = usePolling(fetchHealth);
  const [clock, setClock] = useState("");
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

      {mode === "ops" && <OpsMode />}
      {mode === "archive" && <ArchiveMode />}
      {mode === "sources" && <SourcesMode />}
    </div>
  );
}
