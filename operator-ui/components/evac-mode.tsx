"use client";

/* EVAC — a section just for evacuations.
 *
 * Everything here comes from the Cal OES aggregation layer, the authoritative,
 * self-correcting source: one row per elevated zone, statuses verbatim, a
 * reduced or cancelled zone simply drops out. Zones are grouped by the fire
 * they sit around, sortable, and each group can be pulled up on the map, copied
 * out as a verbatim summary, or turned into a shareable card straight from the
 * official statuses. Nothing on this screen is invented or paraphrased. */

import { useCallback, useEffect, useState } from "react";

import {
  type Closure,
  evacGraphicUrl,
  fetchClosures,
  fetchEvacZones,
  type EvacZoneFeature,
} from "@/lib/api";
import { C, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

type Zone = EvacZoneFeature["properties"];

const RANK: Record<string, number> = { order: 4, shelter_in_place: 3, warning: 2, advisory: 1 };

function norm(status: string | null | undefined): string {
  const s = (status || "").toLowerCase();
  if (s.includes("order")) return "order";
  if (s.includes("shelter")) return "shelter_in_place";
  if (s.includes("warning")) return "warning";
  if (s.includes("advisory")) return "advisory";
  return "other";
}

function colorOf(status: string): string {
  return status === "order"
    ? C.evac
    : status === "warning"
      ? C.warn
      : status === "shelter_in_place"
        ? C.violet
        : C.dim;
}

function word(status: string): string {
  return (
    {
      order: "EVACUATION ORDER",
      warning: "EVACUATION WARNING",
      shelter_in_place: "SHELTER IN PLACE",
      advisory: "ADVISORY",
    }[status] || status.toUpperCase()
  );
}

type Group = {
  key: string;
  incidentId: string | null;
  name: string;
  county: string | null;
  zones: Zone[];
  closures: Closure[]; // Caltrans full closures matched to this fire
  top: number; // strongest status rank, for severity sort + badge
};

function closureLabel(c: Closure): string {
  const parts = [c.route, c.place].filter(Boolean).join(" · ");
  return parts || c.county || "closure";
}

const SORTS: [string, string][] = [
  ["severity", "MOST SEVERE FIRST"],
  ["fire", "FIRE NAME"],
  ["county", "COUNTY"],
  ["issued", "NEWEST ISSUED"],
  ["zones", "MOST ZONES"],
];

function summaryText(g: Group, fetchedAt: string): string {
  const byStatus: Record<string, string[]> = {};
  for (const z of g.zones) {
    const st = norm(z.STATUS);
    (byStatus[st] ||= []).push(z.ZONE_ID || z.ZONE_NAME || "?");
  }
  const lines = [`${g.name} — Evacuation status${g.county ? ` · ${g.county} County, CA` : ""}`];
  lines.push(`Per Cal OES, as of ${fmtStamp(fetchedAt)}:`);
  for (const st of ["order", "shelter_in_place", "warning", "advisory", "other"]) {
    if (byStatus[st]?.length) lines.push(`${word(st)}: ${byStatus[st].join(", ")}`);
  }
  if (g.closures.length) {
    lines.push(`ROAD CLOSURES (Caltrans): ${g.closures.map(closureLabel).join("; ")}`);
  }
  lines.push("Always follow the official county notice, not this summary. — Toast Report OPs");
  return lines.join("\n");
}

export default function EvacMode({
  toast,
  onShowOnMap,
}: {
  toast: (m: string) => void;
  onShowOnMap: (incidentId: string) => void;
}) {
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchEvacZones>> | null>(null);
  const [closures, setClosures] = useState<Closure[]>([]);
  const [sort, setSort] = useState("severity");
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const tick = () => {
      fetchEvacZones().then((d) => live && setData(d)).catch(() => {});
      // road closures are part of the evacuation picture — which roads out are shut
      fetchClosures().then((c) => live && setClosures(c.closures || [])).catch(() => {});
    };
    tick();
    const id = setInterval(tick, 30_000); // the highest-stakes layer — keep it fresh
    return () => {
      live = false;
      clearInterval(id);
    };
  }, []);

  const copySummary = useCallback(
    async (g: Group, fetchedAt: string) => {
      try {
        await navigator.clipboard.writeText(summaryText(g, fetchedAt));
        setCopied(g.key);
        setTimeout(() => setCopied((c) => (c === g.key ? null : c)), 1600);
      } catch {
        toast("copy failed");
      }
    },
    [toast],
  );

  if (!data) {
    return (
      <div style={{ padding: 30, fontFamily: font.mono, fontSize: 12, color: C.dim }}>
        loading evacuation zones…
      </div>
    );
  }

  const zones = data.features.map((f) => f.properties).filter((p) => p.STATUS);

  // group by the fire each zone was matched to; unmatched zones cluster together
  const groups: Record<string, Group> = {};
  for (const z of zones) {
    const key = z.incident_id || "__unmatched__";
    const g = (groups[key] ||= {
      key,
      incidentId: z.incident_id,
      name: z.incident_name || (z.incident_id ? "Fire" : "Unmatched zones"),
      county: z.COUNTY,
      zones: [],
      closures: [],
      top: 0,
    });
    g.zones.push(z);
    g.top = Math.max(g.top, RANK[norm(z.STATUS)] || 0);
  }
  // attach each matched road closure to its fire's group (already deduped and
  // filtered to full closures near active fires by the backend)
  for (const c of closures) {
    const key = c.near?.incident_id;
    if (key && groups[key]) groups[key].closures.push(c);
  }

  const list = Object.values(groups);
  for (const g of list) g.zones.sort((a, b) => (RANK[norm(b.STATUS)] || 0) - (RANK[norm(a.STATUS)] || 0));
  list.sort((a, b) => {
    if (sort === "fire") return a.name.localeCompare(b.name);
    if (sort === "county") return (a.county || "").localeCompare(b.county || "");
    if (sort === "zones") return b.zones.length - a.zones.length;
    if (sort === "issued") {
      const iss = (g: Group) => g.zones.map((z) => z.issued_at || "").sort().at(-1) || "";
      return iss(b).localeCompare(iss(a));
    }
    return b.top - a.top || b.zones.length - a.zones.length; // severity (default)
  });

  const totals = zones.reduce<Record<string, number>>((acc, z) => {
    const st = norm(z.STATUS);
    acc[st] = (acc[st] || 0) + 1;
    return acc;
  }, {});
  const tile = (label: string, n: number, color: string) => (
    <div
      key={label}
      style={{
        background: n ? color + "18" : C.bg,
        border: `1px solid ${n ? color + "66" : C.line}`,
        borderRadius: 6,
        padding: "4px 12px",
        textAlign: "center",
        minWidth: 74,
      }}
    >
      <div style={{ fontFamily: font.disp, fontSize: 18, fontWeight: 700, color: n ? color : C.dim }}>{n}</div>
      <div style={{ fontFamily: font.mono, fontSize: 7.5, color: C.dim, letterSpacing: 0.5 }}>{label}</div>
    </div>
  );

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "14px 18px" }}>
      {/* header: statewide counts, freshness, sort */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 6 }}>
        <span style={{ fontFamily: font.disp, fontSize: 16, letterSpacing: 2, color: C.evac }}>
          EVACUATIONS
        </span>
        <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint }}>
          Cal OES · verbatim · {data.count} zone{data.count === 1 ? "" : "s"} · updated {age(data.fetched_at)} ago
        </span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, letterSpacing: 1 }}>SORT</span>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            style={{
              background: C.panel,
              color: C.txt,
              border: `1px solid ${C.line}`,
              borderRadius: 4,
              fontFamily: font.mono,
              fontSize: 9,
              padding: "2px 4px",
            }}
          >
            {SORTS.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
        {tile("ORDERS", totals.order || 0, C.evac)}
        {tile("WARNINGS", totals.warning || 0, C.warn)}
        {tile("SHELTER", totals.shelter_in_place || 0, C.violet)}
        {tile("ADVISORY", totals.advisory || 0, C.dim)}
        {tile("ROAD CLOSURES", closures.filter((c) => c.near).length, C.blue)}
      </div>

      {!list.length && (
        <div
          style={{
            fontFamily: font.mono,
            fontSize: 12,
            color: C.ok,
            border: `1px solid ${C.ok}44`,
            borderRadius: 7,
            padding: 20,
            textAlign: "center",
          }}
        >
          ✓ No active evacuation zones statewide.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {list.map((g) => {
          const topColor = colorOf(
            Object.keys(RANK).find((k) => RANK[k] === g.top) || "advisory",
          );
          return (
            <div
              key={g.key}
              style={{
                border: `1px solid ${topColor}44`,
                borderLeft: `3px solid ${topColor}`,
                borderRadius: 7,
                background: C.panel,
                padding: "10px 12px",
              }}
            >
              {/* group header + actions */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span style={{ fontFamily: font.disp, fontSize: 14, color: C.txt }}>{g.name}</span>
                {g.county && (
                  <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>{g.county} Co.</span>
                )}
                <span
                  style={{
                    fontFamily: font.mono,
                    fontSize: 8,
                    fontWeight: 700,
                    color: topColor,
                    border: `1px solid ${topColor}66`,
                    borderRadius: 3,
                    padding: "0 5px",
                    letterSpacing: 0.5,
                  }}
                >
                  {word(Object.keys(RANK).find((k) => RANK[k] === g.top) || "advisory")}
                </span>
                <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint }}>
                  {g.zones.length} zone{g.zones.length === 1 ? "" : "s"}
                </span>
                <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
                  {g.incidentId && (
                    <span
                      onClick={() => onShowOnMap(g.incidentId!)}
                      title="show on map: fly to it, zones + roads on"
                      style={{ cursor: "pointer", color: C.evac, fontFamily: font.mono, fontSize: 9, fontWeight: 700 }}
                    >
                      ◎ MAP
                    </span>
                  )}
                  <span
                    onClick={() => copySummary(g, data.fetched_at)}
                    title="copy a verbatim evacuation summary to push out"
                    style={{
                      cursor: "pointer",
                      color: copied === g.key ? C.ok : C.blue,
                      fontFamily: font.mono,
                      fontSize: 9,
                      fontWeight: 700,
                    }}
                  >
                    {copied === g.key ? "✓ COPIED" : "⧉ COPY"}
                  </span>
                  {g.incidentId && (
                    <a
                      href={evacGraphicUrl(g.incidentId)}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="open a shareable card, rendered verbatim from official zones"
                      style={{
                        color: C.ember,
                        fontFamily: font.mono,
                        fontSize: 9,
                        fontWeight: 700,
                        textDecoration: "none",
                      }}
                    >
                      ▤ GRAPHIC
                    </a>
                  )}
                </span>
              </div>

              {/* the zones, verbatim */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 8 }}>
                {g.zones.map((z, i) => {
                  const st = norm(z.STATUS);
                  const zc = colorOf(st);
                  return (
                    <div key={(z.ZONE_ID || i) + "" + i} style={{ borderLeft: `2px solid ${zc}`, paddingLeft: 8 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                        <span style={{ fontFamily: font.mono, fontSize: 11, color: C.txt }}>
                          {z.ZONE_ID || z.ZONE_NAME || "zone"}
                        </span>
                        <span
                          style={{ fontFamily: font.mono, fontSize: 8.5, fontWeight: 700, color: zc, letterSpacing: 0.5 }}
                        >
                          {(z.STATUS || "").toUpperCase()}
                        </span>
                        {z.issued_at && (
                          <span style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginLeft: "auto" }}>
                            issued {age(z.issued_at)} ago
                          </span>
                        )}
                      </div>
                      {z.CRITICAL_INFO && (
                        <div style={{ fontSize: 10.5, color: C.dim, whiteSpace: "pre-wrap", marginTop: 1 }}>
                          {z.CRITICAL_INFO}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* road closures around this fire — which ways out are shut */}
              {g.closures.length > 0 && (
                <div style={{ marginTop: 8, borderTop: `1px solid ${C.line}`, paddingTop: 6 }}>
                  <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, letterSpacing: 1, marginBottom: 3 }}>
                    ROAD CLOSURES · CALTRANS · STATE HIGHWAYS ONLY
                  </div>
                  {g.closures.map((c, i) => (
                    <div key={i} style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                      <span style={{ color: C.blue, fontFamily: font.mono, fontSize: 10 }}>⛔ {closureLabel(c)}</span>
                      <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim }}>
                        {c.type_of_closure}
                        {c.type_of_work ? ` · ${c.type_of_work}` : ""}
                      </span>
                      {c.started && (
                        <span style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginLeft: "auto" }}>
                          since {age(c.started)} ago
                        </span>
                      )}
                    </div>
                  ))}
                  <div style={{ fontFamily: font.mono, fontSize: 7.5, color: C.faint, marginTop: 2 }}>
                    County/local roads are not in this feed — follow official routing.
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginTop: 14 }}>
        {data.source} · verify locally before acting.
      </div>
    </div>
  );
}
