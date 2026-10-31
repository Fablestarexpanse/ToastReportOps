"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  FloodAlert,
  Gauge,
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
   and a missing reading is a dash, never a zero. */

const POLL_MS = 60_000;

const CAT_COLOR: Record<string, string> = {
  major: C.evac,
  moderate: C.emberHot,
  minor: C.ember,
  action: C.warn,
};
const catColor = (c: string | null | undefined) => CAT_COLOR[(c ?? "").toLowerCase()] ?? C.dim;
const fmt = (n: number | null | undefined, digits = 0) =>
  n == null ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: digits });

function Heading({ children, note }: { children: React.ReactNode; note?: string }) {
  return (
    <div style={{ margin: "18px 0 8px" }}>
      <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.ember }}>{children}</div>
      {note && <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 2 }}>{note}</div>}
    </div>
  );
}

function AlertCard({ a }: { a: FloodAlert }) {
  const [open, setOpen] = useState(false);
  const urgent = /warning/i.test(a.event);
  const color = urgent ? C.evac : C.warn;
  return (
    <div style={{ border: `1px solid ${color}`, background: color + "14", borderRadius: 7, padding: "9px 12px", marginBottom: 8 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", cursor: "pointer" }} onClick={() => setOpen(!open)}>
        <span style={{ fontFamily: font.disp, fontSize: 13, fontWeight: 700, letterSpacing: 1, color }}>
          {a.event.toUpperCase()}
        </span>
        <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>{a.area}</span>
        <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 9, color: C.faint }}>
          {a.ends ? `until ${fmtStamp(a.ends)}` : ""} {open ? "▲" : "▼"}
        </span>
      </div>
      {open && (
        <div style={{ marginTop: 8, fontFamily: font.mono, fontSize: 10, color: C.txt, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
          {a.description}
          {a.instruction && (
            <div style={{ marginTop: 8, color: C.warn }}>{a.instruction}</div>
          )}
          <div style={{ marginTop: 6, color: C.faint }}>Official text from {a.sender ?? "NWS"} — shown as issued.</div>
        </div>
      )}
    </div>
  );
}

function GaugeRow({ g }: { g: Gauge }) {
  const o = g.observed;
  const f = g.forecast;
  return (
    <tr style={{ borderTop: `1px solid ${C.line}` }}>
      <td style={td}>
        {g.pinned && <span style={{ color: C.blue }} title="pinned">★ </span>}
        {g.name ?? g.lid} <span style={{ color: C.faint }}>{g.lid}</span>
      </td>
      <td style={{ ...td, color: catColor(o?.category) }}>{(o?.category ?? "—").replace(/_/g, " ")}</td>
      <td style={td}>{o?.stage != null ? `${fmt(o.stage, 2)} ft` : "—"}</td>
      <td style={td}>{o?.flow != null ? `${fmt(o.flow, 2)} ${o.flow_unit ?? ""}` : "—"}</td>
      <td style={{ ...td, color: catColor(f?.category) }}>{(f?.category ?? "—").replace(/_/g, " ")}</td>
      <td style={{ ...td, color: g.stale ? C.warn : C.dim }}>
        {o?.valid_time ? age(o.valid_time) : "no time"}
        {g.stale ? " · STALE" : ""}
      </td>
    </tr>
  );
}

const th: React.CSSProperties = { textAlign: "left", fontWeight: 400, padding: "4px 8px", color: C.faint };
const td: React.CSSProperties = { padding: "6px 8px", fontFamily: font.mono, fontSize: 10, color: C.txt };

function Bar({ pct, color }: { pct: number | null; color: string }) {
  return (
    <div style={{ width: 110, height: 8, background: C.line, borderRadius: 4, overflow: "hidden" }}>
      {pct != null && <div style={{ width: `${Math.min(100, pct)}%`, height: "100%", background: color }} />}
    </div>
  );
}

export default function WaterMode({ toast }: { toast: (m: string) => void }) {
  const [water, setWater] = useState<WaterBoard | null>(null);
  const [res, setRes] = useState<ReservoirBoard | null>(null);
  const [busy, setBusy] = useState(false);
  const [only, setOnly] = useState<"all" | "attention" | "pinned">("all");

  const load = useCallback(
    async (refresh = false) => {
      setBusy(true);
      const [w, r] = await Promise.allSettled([fetchWater(refresh), fetchReservoirs(refresh)]);
      if (w.status === "fulfilled") setWater(w.value);
      else toast("flood feeds unreachable — showing last data");
      if (r.status === "fulfilled") setRes(r.value);
      setBusy(false);
    },
    [toast],
  );

  useEffect(() => {
    load();
    const t = setInterval(() => load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const basins = useMemo(() => {
    const list = (res?.reservoirs ?? []).filter(
      (r) => only === "all" || (only === "pinned" ? r.pinned : r.near_full || r.below_average),
    );
    const m = new Map<string, typeof list>();
    for (const r of list) m.set(r.basin ?? "Other", [...(m.get(r.basin ?? "Other") ?? []), r]);
    return [...m.entries()];
  }, [res, only]);

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: "14px 22px 40px", background: C.bg, color: C.txt }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
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
          Fire season profile: this view fetches on demand, but background polling and phone alerts for
          floods are off. Switch HAZARD SEASON in OPTIONS to turn them on.
        </div>
      )}
      {water?.errors?.length ? (
        <div style={{ fontFamily: font.mono, fontSize: 9, color: C.warn, marginTop: 6 }}>
          ⚠ partial data — {water.errors.join(", ")}
        </div>
      ) : null}

      <Heading note="NWS flood watches, warnings and advisories for California, as issued.">FLOOD ALERTS</Heading>
      {water?.alerts.length ? (
        water.alerts.map((a, i) => <AlertCard key={a.id ?? i} a={a} />)
      ) : (
        <div style={{ fontFamily: font.mono, fontSize: 10, color: C.ok }}>
          {water ? "No active flood alerts for California." : ""}
        </div>
      )}

      <Heading note="Gauges at or above action stage (observed or NWS-forecast), plus your pinned gauges. Readings are provisional.">
        RIVER GAUGES
      </Heading>
      {water && water.gauges.length === 0 ? (
        <div style={{ fontFamily: font.mono, fontSize: 10, color: C.ok }}>
          No gauge is at or above action stage. Pin gauges in OPTIONS to keep specific rivers on this board.
        </div>
      ) : (
        <table style={{ borderCollapse: "collapse", width: "100%", fontFamily: font.mono, fontSize: 9 }}>
          <thead>
            <tr>
              {["GAUGE", "OBSERVED", "STAGE", "FLOW", "NWS FORECAST", "READING"].map((h) => (
                <th key={h} style={th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>{water?.gauges.map((g) => <GaugeRow key={g.lid} g={g} />)}</tbody>
        </table>
      )}

      <Heading note={res?.note}>RESERVOIRS</Heading>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        {(["all", "attention", "pinned"] as const).map((k) => (
          <span
            key={k}
            onClick={() => setOnly(k)}
            style={{ cursor: "pointer", fontFamily: font.mono, fontSize: 9, padding: "3px 9px", borderRadius: 3, border: `1px solid ${only === k ? C.ember : C.line}`, color: only === k ? C.ember : C.dim }}
          >
            {k === "attention" ? "NEAR FULL / LOW" : k.toUpperCase()}
          </span>
        ))}
        {res?.statewide_pct_capacity != null && (
          <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 10, color: C.dim }}>
            tracked reservoirs combined: {res.statewide_pct_capacity}% of capacity
          </span>
        )}
      </div>
      {res?.error && (
        <div style={{ fontFamily: font.mono, fontSize: 9, color: C.warn, marginBottom: 6 }}>
          ⚠ could not refresh ({res.error}) — showing the last stored values
        </div>
      )}
      {basins.map(([basin, items]) => (
        <div key={basin} style={{ marginBottom: 10 }}>
          <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 1.5, color: C.dim, margin: "8px 0 2px" }}>
            {basin.toUpperCase()}
          </div>
          <table style={{ borderCollapse: "collapse", width: "100%" }}>
            <thead>
              <tr>
                {["RESERVOIR", "% CAPACITY", "", "STORAGE (AF)", "% AVG", "7-DAY", "IN / OUT (CFS)"].map((h) => (
                  <th key={h} style={{ ...th, fontFamily: font.mono, fontSize: 8.5 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.station} style={{ borderTop: `1px solid ${C.line}` }}>
                  <td style={td}>
                    {r.pinned && <span style={{ color: C.blue }}>★ </span>}
                    {r.name} <span style={{ color: C.faint }}>{r.station}</span>
                  </td>
                  <td style={{ ...td, color: r.near_full ? C.emberHot : C.txt }}>
                    {r.pct_capacity != null ? `${fmt(r.pct_capacity)}%` : "—"}
                  </td>
                  <td style={td}><Bar pct={r.pct_capacity} color={r.near_full ? C.emberHot : C.blue} /></td>
                  <td style={td}>
                    {fmt(r.storage_af)} <span style={{ color: C.faint }}>/ {fmt(r.capacity_af)}</span>
                  </td>
                  <td style={{ ...td, color: r.below_average ? C.warn : C.txt }}>
                    {r.pct_average != null ? `${fmt(r.pct_average)}%` : "—"}
                  </td>
                  <td style={td}>
                    {r.change_7d_pts == null ? "—" : `${r.change_7d_pts > 0 ? "+" : ""}${r.change_7d_pts} pts`}
                  </td>
                  <td style={td}>{fmt(r.inflow_cfs)} / {fmt(r.outflow_cfs)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      {res && basins.length === 0 && (
        <div style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>Nothing matches this filter.</div>
      )}
    </div>
  );
}
