"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  FloodAlert,
  Gauge,
  Reservoir,
  ReservoirBoard,
  WaterBoard,
  fetchReservoirs,
  fetchWater,
} from "@/lib/api";
import { C, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

/* WATER: flood season and reservoir levels, officials only.

   Gauges and alerts are NWS/NOAA categories relayed as issued; reservoirs are
   DWR/CDEC daily values. We compute nothing here that could be mistaken for a
   forecast — no crest estimates, no advice. Every reading shows how old it is,
   and a missing reading is a dash, never a zero.

   Layout: a summary strip says what needs attention at a glance; each section folds
   away; the reservoirs are ONE table (so columns line up across basins) that sorts
   by any column, filters, searches, and groups by basin with foldable groups. Fold
   and sort choices are remembered per browser (a convenience, never required). */

const POLL_MS = 60_000;

const CAT_COLOR: Record<string, string> = {
  major: C.evac,
  moderate: C.emberHot,
  minor: C.ember,
  action: C.warn,
};
const CAT_RANK: Record<string, number> = { major: 4, moderate: 3, minor: 2, action: 1 };
const catColor = (c: string | null | undefined) => CAT_COLOR[(c ?? "").toLowerCase()] ?? C.dim;
const catLabel = (c: string | null | undefined) => (c ?? "—").replace(/_/g, " ");
const fmt = (n: number | null | undefined, digits = 0) =>
  n == null ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: digits });

/* ---------- remembered UI state (localStorage can be blocked: never required) ---------- */

function useSticky<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(initial);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(`water:${key}`);
      if (raw !== null) setValue(JSON.parse(raw) as T);
    } catch {
      /* private window or blocked storage: keep the default */
    }
  }, [key]);
  const set = useCallback(
    (v: T) => {
      setValue(v);
      try {
        localStorage.setItem(`water:${key}`, JSON.stringify(v));
      } catch {
        /* not fatal */
      }
    },
    [key],
  );
  return [value, set];
}

/* ---------- building blocks ---------- */

const th: React.CSSProperties = {
  textAlign: "left",
  fontWeight: 400,
  padding: "6px 10px",
  color: C.faint,
  fontFamily: font.mono,
  fontSize: 8.5,
  letterSpacing: 0.5,
  whiteSpace: "nowrap",
  userSelect: "none",
  borderBottom: `1px solid ${C.line}`,
};
const td: React.CSSProperties = {
  padding: "6px 10px",
  fontFamily: font.mono,
  fontSize: 10,
  color: C.txt,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};
const num: React.CSSProperties = { ...td, textAlign: "right", fontVariantNumeric: "tabular-nums" };

function Section({
  id,
  title,
  count,
  countColor,
  note,
  children,
}: {
  id: string;
  title: string;
  count?: string;
  countColor?: string;
  note?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useSticky(`open:${id}`, true);
  return (
    <section style={{ marginTop: 18, border: `1px solid ${C.line}`, borderRadius: 8, background: C.panel }}>
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        style={{
          all: "unset",
          boxSizing: "border-box",
          width: "100%",
          display: "flex",
          alignItems: "baseline",
          gap: 10,
          padding: "10px 14px",
          cursor: "pointer",
        }}
      >
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.faint, width: 10 }}>{open ? "▾" : "▸"}</span>
        <span style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.ember }}>{title}</span>
        {count && (
          <span style={{ fontFamily: font.mono, fontSize: 10, color: countColor ?? C.dim }}>{count}</span>
        )}
        {note && (
          <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 8.5, color: C.faint, textAlign: "right" }}>
            {note}
          </span>
        )}
      </button>
      {open && <div style={{ padding: "0 14px 12px" }}>{children}</div>}
    </section>
  );
}

function Stat({ label, value, color, hint }: { label: string; value: string; color: string; hint?: string }) {
  return (
    <div
      title={hint}
      style={{
        flex: "1 1 150px",
        minWidth: 140,
        border: `1px solid ${C.line}`,
        borderRadius: 8,
        background: C.panel,
        padding: "10px 14px",
      }}
    >
      <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, letterSpacing: 1 }}>{label}</div>
      <div style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, color, marginTop: 2 }}>{value}</div>
    </div>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        all: "unset",
        cursor: "pointer",
        fontFamily: font.mono,
        fontSize: 9,
        padding: "4px 10px",
        borderRadius: 4,
        border: `1px solid ${active ? C.ember : C.line}`,
        color: active ? C.ember : C.dim,
      }}
    >
      {children}
    </button>
  );
}

function AlertCard({ a }: { a: FloodAlert }) {
  const [open, setOpen] = useState(false);
  const urgent = /warning|emergency/i.test(a.event);
  const color = urgent ? C.evac : C.warn;
  return (
    <div style={{ border: `1px solid ${color}`, background: color + "14", borderRadius: 6, marginTop: 8 }}>
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        style={{ all: "unset", boxSizing: "border-box", width: "100%", display: "flex", gap: 12, alignItems: "baseline", padding: "8px 12px", cursor: "pointer" }}
      >
        <span style={{ fontFamily: font.disp, fontSize: 12, fontWeight: 700, letterSpacing: 1, color, whiteSpace: "nowrap" }}>
          {a.event.toUpperCase()}
        </span>
        <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {a.area}
        </span>
        <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, whiteSpace: "nowrap" }}>
          {a.ends ? `until ${fmtStamp(a.ends)}` : ""} {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (
        <div style={{ padding: "0 12px 10px", fontFamily: font.mono, fontSize: 10, color: C.txt, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
          {a.description}
          {a.instruction && <div style={{ marginTop: 8, color: C.warn }}>{a.instruction}</div>}
          <div style={{ marginTop: 6, color: C.faint }}>Official text from {a.sender ?? "NWS"}, shown as issued.</div>
        </div>
      )}
    </div>
  );
}

/* ---------- sortable tables ---------- */

type SortDir = "asc" | "desc";
type Sort<K extends string> = { key: K; dir: SortDir };

/** Missing values always sort last, whichever direction. */
function compare(a: number | string | null | undefined, b: number | string | null | undefined, dir: SortDir): number {
  const aMissing = a == null || a === "";
  const bMissing = b == null || b === "";
  if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1;
  const r = typeof a === "string" ? a.localeCompare(String(b)) : (a as number) - (b as number);
  return dir === "asc" ? r : -r;
}

function SortHeader<K extends string>({
  label,
  k,
  sort,
  setSort,
  align = "left",
  firstDir = "desc",
}: {
  label: string;
  k: K;
  sort: Sort<K>;
  setSort: (s: Sort<K>) => void;
  align?: "left" | "right";
  firstDir?: SortDir;
}) {
  const active = sort.key === k;
  const arrow = active ? (sort.dir === "asc" ? " ▲" : " ▼") : "";
  return (
    <th
      onClick={() => setSort(active ? { key: k, dir: sort.dir === "asc" ? "desc" : "asc" } : { key: k, dir: firstDir })}
      title="Click to sort"
      style={{ ...th, textAlign: align, cursor: "pointer", color: active ? C.ember : C.faint }}
    >
      {label}
      {arrow}
    </th>
  );
}

type GaugeKey = "name" | "severity" | "stage" | "flow" | "valid";

function GaugeTable({ gauges }: { gauges: Gauge[] }) {
  const [sort, setSort] = useSticky<Sort<GaugeKey>>("gaugeSort", { key: "severity", dir: "desc" });
  const rows = useMemo(() => {
    const value = (g: Gauge): number | string | null => {
      switch (sort.key) {
        case "name":
          return g.name ?? g.lid;
        case "severity":
          return g.severity;
        case "stage":
          return g.observed?.stage ?? null;
        case "flow":
          return g.observed?.flow ?? null;
        case "valid":
          return g.observed?.valid_time ? new Date(g.observed.valid_time).getTime() : null;
      }
    };
    return [...gauges].sort((a, b) => compare(value(a), value(b), sort.dir));
  }, [gauges, sort]);
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed", minWidth: 720 }}>
        <colgroup>
          <col style={{ width: "34%" }} />
          <col style={{ width: "14%" }} />
          <col style={{ width: "12%" }} />
          <col style={{ width: "13%" }} />
          <col style={{ width: "14%" }} />
          <col style={{ width: "13%" }} />
        </colgroup>
        <thead>
          <tr>
            <SortHeader label="GAUGE" k="name" sort={sort} setSort={setSort} firstDir="asc" />
            <SortHeader label="OBSERVED" k="severity" sort={sort} setSort={setSort} />
            <SortHeader label="STAGE" k="stage" sort={sort} setSort={setSort} align="right" />
            <SortHeader label="FLOW" k="flow" sort={sort} setSort={setSort} align="right" />
            <th style={th}>NWS FORECAST</th>
            <SortHeader label="READING" k="valid" sort={sort} setSort={setSort} align="right" />
          </tr>
        </thead>
        <tbody>
          {rows.map((g) => {
            const o = g.observed;
            const f = g.forecast;
            return (
              <tr key={g.lid} style={{ borderTop: `1px solid ${C.line}` }}>
                <td style={td} title={`${g.name ?? ""} (${g.lid})`}>
                  {g.pinned && <span style={{ color: C.blue }} title="pinned">★ </span>}
                  {g.name ?? g.lid} <span style={{ color: C.faint }}>{g.lid}</span>
                </td>
                <td style={{ ...td, color: catColor(o?.category) }}>{catLabel(o?.category)}</td>
                <td style={num}>{o?.stage != null ? `${fmt(o.stage, 2)} ft` : "—"}</td>
                <td style={num}>{o?.flow != null ? `${fmt(o.flow, 2)} ${o.flow_unit ?? ""}` : "—"}</td>
                <td style={{ ...td, color: catColor(f?.category) }}>{catLabel(f?.category)}</td>
                <td style={{ ...num, color: g.stale ? C.warn : C.dim }}>
                  {o?.valid_time ? age(o.valid_time) : "no time"}
                  {g.stale ? " · stale" : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

type ResKey = "name" | "pct_capacity" | "storage_af" | "pct_average" | "change_7d_pts" | "inflow_cfs" | "outflow_cfs";

function Bar({ pct, color }: { pct: number | null; color: string }) {
  return (
    <div style={{ height: 6, background: C.line, borderRadius: 3, overflow: "hidden" }}>
      {pct != null && <div style={{ width: `${Math.max(0, Math.min(100, pct))}%`, height: "100%", background: color }} />}
    </div>
  );
}

function ReservoirTable({ items }: { items: Reservoir[] }) {
  const [sort, setSort] = useSticky<Sort<ResKey>>("resSort", { key: "name", dir: "asc" });
  const [grouped, setGrouped] = useSticky("resGrouped", true);
  const [folded, setFolded] = useSticky<string[]>("resFolded", []);

  const sorted = useMemo(() => {
    const value = (r: Reservoir) => (sort.key === "name" ? r.name : r[sort.key]);
    return [...items].sort((a, b) => compare(value(a), value(b), sort.dir) || a.name.localeCompare(b.name));
  }, [items, sort]);

  const groups = useMemo(() => {
    if (!grouped) return [["", sorted]] as [string, Reservoir[]][];
    const m = new Map<string, Reservoir[]>();
    for (const r of sorted) m.set(r.basin ?? "Other", [...(m.get(r.basin ?? "Other") ?? []), r]);
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [sorted, grouped]);

  const toggleBasin = (b: string) => setFolded(folded.includes(b) ? folded.filter((x) => x !== b) : [...folded, b]);
  const allBasins = groups.map(([b]) => b);

  return (
    <>
      <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "4px 0 8px", flexWrap: "wrap" }}>
        <Chip active={grouped} onClick={() => setGrouped(!grouped)}>
          GROUP BY BASIN
        </Chip>
        {grouped && (
          <>
            <Chip active={false} onClick={() => setFolded([])}>
              EXPAND ALL
            </Chip>
            <Chip active={false} onClick={() => setFolded(allBasins)}>
              COLLAPSE ALL
            </Chip>
          </>
        )}
        <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>
          click a column heading to sort
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed", minWidth: 860 }}>
          <colgroup>
            <col style={{ width: "24%" }} />
            <col style={{ width: "8%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "18%" }} />
            <col style={{ width: "8%" }} />
            <col style={{ width: "10%" }} />
            <col style={{ width: "18%" }} />
          </colgroup>
          <thead>
            <tr>
              <SortHeader label="RESERVOIR" k="name" sort={sort} setSort={setSort} firstDir="asc" />
              <SortHeader label="% CAP" k="pct_capacity" sort={sort} setSort={setSort} align="right" />
              <th style={th} />
              <SortHeader label="STORAGE / CAPACITY (AF)" k="storage_af" sort={sort} setSort={setSort} align="right" />
              <SortHeader label="% AVG" k="pct_average" sort={sort} setSort={setSort} align="right" />
              <SortHeader label="7-DAY" k="change_7d_pts" sort={sort} setSort={setSort} align="right" />
              <SortHeader label="IN / OUT (CFS)" k="inflow_cfs" sort={sort} setSort={setSort} align="right" />
            </tr>
          </thead>
          {groups.map(([basin, rows]) => {
            const isFolded = grouped && folded.includes(basin);
            const nearFull = rows.filter((r) => r.near_full).length;
            return (
              <tbody key={basin || "all"}>
                {grouped && (
                  <tr onClick={() => toggleBasin(basin)} style={{ cursor: "pointer", background: C.panelUp }}>
                    <td colSpan={7} style={{ ...td, fontFamily: font.disp, fontSize: 11, letterSpacing: 1.5, color: C.dim }}>
                      <span style={{ fontFamily: font.mono, color: C.faint, marginRight: 8 }}>{isFolded ? "▸" : "▾"}</span>
                      {basin.toUpperCase()}
                      <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, marginLeft: 10 }}>
                        {rows.length} {rows.length === 1 ? "reservoir" : "reservoirs"}
                        {nearFull ? <span style={{ color: C.emberHot }}> · {nearFull} near full</span> : null}
                      </span>
                    </td>
                  </tr>
                )}
                {!isFolded &&
                  rows.map((r) => (
                    <tr key={r.station} style={{ borderTop: `1px solid ${C.line}` }}>
                      <td style={td} title={`${r.name} (${r.station})`}>
                        {r.pinned && <span style={{ color: C.blue }}>★ </span>}
                        {r.name} <span style={{ color: C.faint }}>{r.station}</span>
                      </td>
                      <td style={{ ...num, color: r.near_full ? C.emberHot : C.txt }}>
                        {r.pct_capacity != null ? `${fmt(r.pct_capacity)}%` : "—"}
                      </td>
                      <td style={td}>
                        <Bar pct={r.pct_capacity} color={r.near_full ? C.emberHot : C.blue} />
                      </td>
                      <td style={num}>
                        {fmt(r.storage_af)} <span style={{ color: C.faint }}>/ {fmt(r.capacity_af)}</span>
                      </td>
                      <td style={{ ...num, color: r.below_average ? C.warn : C.txt }}>
                        {r.pct_average != null ? `${fmt(r.pct_average)}%` : "—"}
                      </td>
                      <td style={{ ...num, color: (r.change_7d_pts ?? 0) > 0 ? C.blue : C.txt }}>
                        {r.change_7d_pts == null ? "—" : `${r.change_7d_pts > 0 ? "+" : ""}${r.change_7d_pts} pts`}
                      </td>
                      <td style={num}>
                        {fmt(r.inflow_cfs)} / {fmt(r.outflow_cfs)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            );
          })}
        </table>
      </div>
    </>
  );
}

/* ---------- the mode ---------- */

export default function WaterMode({ toast }: { toast: (m: string) => void }) {
  const [water, setWater] = useState<WaterBoard | null>(null);
  const [res, setRes] = useState<ReservoirBoard | null>(null);
  const [busy, setBusy] = useState(false);
  const [only, setOnly] = useSticky<"all" | "attention" | "pinned">("resFilter", "all");
  const [query, setQuery] = useState("");

  const load = useCallback(
    async (refresh = false) => {
      setBusy(true);
      const [w, r] = await Promise.allSettled([fetchWater(refresh), fetchReservoirs(refresh)]);
      if (w.status === "fulfilled") setWater(w.value);
      else toast("flood feeds unreachable — showing last data");
      if (r.status === "fulfilled") setRes(r.value);
      else toast("reservoir feed unreachable — showing last data");
      setBusy(false);
    },
    [toast],
  );

  useEffect(() => {
    load();
    const t = setInterval(() => load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const reservoirs = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (res?.reservoirs ?? []).filter(
      (r) =>
        (only === "all" || (only === "pinned" ? r.pinned : r.near_full || r.below_average)) &&
        (!q || `${r.name} ${r.station} ${r.basin ?? ""}`.toLowerCase().includes(q)),
    );
  }, [res, only, query]);

  const alerts = water?.alerts ?? [];
  const warnings = alerts.filter((a) => /warning|emergency/i.test(a.event)).length;
  const gauges = water?.gauges ?? [];
  const flooding = gauges.filter((g) => (CAT_RANK[(g.observed?.category ?? "").toLowerCase()] ?? 0) >= 2).length;
  const all = res?.reservoirs ?? [];
  const nearFull = all.filter((r) => r.near_full).length;
  const low = all.filter((r) => r.below_average).length;

  return (
    <div style={{ flex: 1, overflowY: "auto", background: C.bg, color: C.txt }}>
      <div style={{ maxWidth: 1400, margin: "0 auto", padding: "14px 22px 40px" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
          <span style={{ fontFamily: font.disp, fontSize: 20, fontWeight: 700, letterSpacing: 1.5 }}>WATER</span>
          <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint }}>
            {water ? `flood data ${age(water.fetched_at)} ago` : "loading…"}
            {res?.fetched_at ? ` · reservoirs ${age(res.fetched_at)} ago` : ""}
          </span>
          <button
            onClick={() => load(true)}
            disabled={busy}
            style={{ marginLeft: "auto", background: "transparent", border: `1px solid ${C.line}`, borderRadius: 4, color: C.dim, fontFamily: font.mono, fontSize: 9, padding: "4px 10px", cursor: "pointer" }}
          >
            {busy ? "…" : "REFRESH"}
          </button>
        </div>
        {water && water.profile === "fire" && (
          <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, marginTop: 6 }}>
            Fire season profile: this view fetches on demand, but background polling and phone alerts for floods are
            off. Switch HAZARD SEASON in OPTIONS to turn them on.
          </div>
        )}
        {water?.errors?.length ? (
          <div style={{ fontFamily: font.mono, fontSize: 9, color: C.warn, marginTop: 6 }}>
            ⚠ partial data — {water.errors.join(", ")}
          </div>
        ) : null}

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
          <Stat
            label="FLOOD ALERTS"
            value={water ? String(alerts.length) : "…"}
            color={warnings ? C.evac : alerts.length ? C.warn : C.ok}
            hint={`${warnings} warning(s), ${alerts.length - warnings} watch/advisory`}
          />
          <Stat
            label="GAUGES IN FLOOD"
            value={water ? String(flooding) : "…"}
            color={flooding ? C.emberHot : C.ok}
            hint="observed at minor flood stage or above"
          />
          <Stat label="RESERVOIRS NEAR FULL" value={res ? String(nearFull) : "…"} color={nearFull ? C.emberHot : C.ok} hint="90% of capacity or more" />
          <Stat label="WELL BELOW AVERAGE" value={res ? String(low) : "…"} color={low ? C.warn : C.ok} hint="under 70% of the historical average for the date" />
          <Stat
            label="STATEWIDE STORAGE"
            value={res?.statewide_pct_capacity != null ? `${res.statewide_pct_capacity}%` : "—"}
            color={C.blue}
            hint="all tracked reservoirs combined, % of capacity"
          />
        </div>

        <Section
          id="alerts"
          title="FLOOD ALERTS"
          count={water ? String(alerts.length) : undefined}
          countColor={warnings ? C.evac : C.dim}
          note="NWS watches, warnings and advisories for California, as issued"
        >
          {alerts.length ? (
            alerts.map((a, i) => <AlertCard key={a.id ?? i} a={a} />)
          ) : (
            <div style={{ fontFamily: font.mono, fontSize: 10, color: C.ok }}>
              {water ? "No active flood alerts for California." : ""}
            </div>
          )}
        </Section>

        <Section
          id="gauges"
          title="RIVER GAUGES"
          count={water ? String(gauges.length) : undefined}
          note="at or above action stage (observed or forecast), plus pinned · provisional readings"
        >
          {water && gauges.length === 0 ? (
            <div style={{ fontFamily: font.mono, fontSize: 10, color: C.ok }}>
              No gauge is at or above action stage. Pin gauges in OPTIONS to keep specific rivers here.
            </div>
          ) : (
            <GaugeTable gauges={gauges} />
          )}
        </Section>

        <Section
          id="reservoirs"
          title="RESERVOIRS"
          count={res ? `${reservoirs.length} of ${all.length}` : undefined}
          note="DWR/CDEC daily values · % of total capacity, not flood-control limits · — means no reading"
        >
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 4 }}>
            {(["all", "attention", "pinned"] as const).map((k) => (
              <Chip key={k} active={only === k} onClick={() => setOnly(k)}>
                {k === "attention" ? "NEAR FULL / LOW" : k.toUpperCase()}
              </Chip>
            ))}
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="search name, ID or basin…"
              aria-label="Search reservoirs"
              style={{ marginLeft: "auto", width: 240, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.txt, fontFamily: font.mono, fontSize: 10, padding: "5px 8px", outline: "none" }}
            />
          </div>
          {res?.error && (
            <div style={{ fontFamily: font.mono, fontSize: 9, color: C.warn, marginTop: 6 }}>
              ⚠ could not refresh ({res.error}) — showing the last stored values
            </div>
          )}
          {!res ? (
            <div style={{ fontFamily: font.mono, fontSize: 10, color: C.dim, marginTop: 10 }}>Loading reservoirs…</div>
          ) : reservoirs.length === 0 ? (
            <div style={{ fontFamily: font.mono, fontSize: 10, color: C.dim, marginTop: 10 }}>Nothing matches this filter.</div>
          ) : (
            <ReservoirTable items={reservoirs} />
          )}
        </Section>
      </div>
    </div>
  );
}
