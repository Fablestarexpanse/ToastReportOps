"use client";

import { useEffect, useMemo, useState } from "react";
import { Incident, hasActiveEvac } from "@/lib/api";
import { CA_OUTLINE } from "@/lib/california";
import { C, font } from "@/lib/theme";

/* Offline California map — no tile server (PSPS-safe). The state outline is a
   bundled GeoJSON ring; incidents are projected from lat/lon onto an
   aspect-correct SVG viewBox. */

const PAD = 24;
const WIDTH = 560;

function useProjection() {
  return useMemo(() => {
    const lons = CA_OUTLINE.map((p) => p[0]);
    const lats = CA_OUTLINE.map((p) => p[1]);
    const lonMin = Math.min(...lons);
    const lonMax = Math.max(...lons);
    const latMin = Math.min(...lats);
    const latMax = Math.max(...lats);
    const midLat = (latMin + latMax) / 2;
    // geographic aspect: lon degrees are shorter by cos(lat)
    const geoW = (lonMax - lonMin) * Math.cos((midLat * Math.PI) / 180);
    const geoH = latMax - latMin;
    const inner = WIDTH - PAD * 2;
    // round to 2dp so SSR and client serialize an identical viewBox (last-ULP
    // Math differences between Node and the browser otherwise break hydration)
    const height = Math.round((inner * (geoH / geoW) + PAD * 2) * 100) / 100;
    const project = (lon: number, lat: number): [number, number] => [
      PAD + ((lon - lonMin) / (lonMax - lonMin)) * inner,
      PAD + ((latMax - lat) / (latMax - latMin)) * (height - PAD * 2),
    ];
    return { project, height, lonMin, lonMax, latMin, latMax };
  }, []);
}

const LAYERS = [
  ["Incidents", true],
  ["Hotspots", false],
  ["Perimeter", false],
  ["Evac", false],
  ["Aircraft", false],
  ["Cams", false],
] as const;

function pinRadius(acreage: number | null): number {
  if (!acreage || acreage <= 0) return 4;
  return Math.max(4, Math.min(13, 3 + Math.log10(acreage + 1) * 2.2));
}

function pinColor(inc: Incident): string {
  // People-at-risk outranks fire behavior: an active evacuation is the thing
  // the operator must see first, whatever the containment number says.
  if (hasActiveEvac(inc)) return C.evac;
  if (inc.status === "contained" || inc.status === "frozen") return C.dim;
  const c = inc.containment_pct ?? 0;
  return c >= 60 ? C.warn : C.emberHot;
}

export default function MapPanel({
  incidents,
  selected,
  onSelect,
}: {
  incidents: Incident[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  // Render only after mount — the SVG geometry derives from Math.cos, whose
  // last-ULP result differs between Node (SSR) and the browser, which would
  // otherwise trip React hydration. Client-only is also correct: the map has
  // no meaning without live data.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const { project, height } = useProjection();
  const outlinePath = useMemo(() => {
    return (
      CA_OUTLINE.map((p, i) => {
        const [x, y] = project(p[0], p[1]);
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
      }).join(" ") + " Z"
    );
  }, [project]);

  const placed = incidents.filter((i) => i.lat != null && i.lon != null);
  const evacCount = placed.filter(hasActiveEvac).length;

  if (!mounted) {
    return <div style={{ background: "#10141a", height: "100%", minWidth: 0 }} />;
  }

  return (
    <div style={{ position: "relative", background: "#10141a", minWidth: 0, height: "100%", overflow: "hidden" }}>
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        style={{ width: "100%", height: "100%", display: "block" }}
        preserveAspectRatio="xMidYMid meet"
      >
        {/* faint graticule */}
        {[...Array(6)].map((_, i) => (
          <line key={"h" + i} x1={0} y1={(height / 6) * i} x2={WIDTH} y2={(height / 6) * i} stroke="#1b2128" strokeWidth={1} />
        ))}
        {[...Array(6)].map((_, i) => (
          <line key={"v" + i} x1={(WIDTH / 6) * i} y1={0} x2={(WIDTH / 6) * i} y2={height} stroke="#1b2128" strokeWidth={1} />
        ))}
        {/* California outline */}
        <path d={outlinePath} fill="#161c22" stroke={C.line} strokeWidth={1.5} />
        {/* incident pins */}
        {placed.map((inc) => {
          const [x, y] = project(inc.lon as number, inc.lat as number);
          const r = pinRadius(inc.acreage);
          const color = pinColor(inc);
          const isSel = inc.id === selected;
          const evac = hasActiveEvac(inc);
          return (
            <g key={inc.id} style={{ cursor: "pointer" }} onClick={() => onSelect(inc.id)}>
              {/* an evacuation pulses whether or not it is selected — it is the
                  one thing that should catch the eye across the whole map */}
              {(isSel || evac) && (
                <circle cx={x} cy={y} r={r + 8} fill="none" stroke={color} strokeWidth={1.5} className="tr-pulse" />
              )}
              {evac && (
                <circle cx={x} cy={y} r={r + 4} fill="none" stroke={C.evac} strokeWidth={1} strokeOpacity={0.75} />
              )}
              <circle cx={x} cy={y} r={r} fill={color} fillOpacity={0.85} stroke={isSel ? C.txt : color} strokeWidth={isSel ? 1.5 : 0.5} />
              {isSel && (
                <text x={x + r + 4} y={y + 3} fill={C.txt} fontSize={10} fontFamily={font.mono}>
                  {inc.canonical_name}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {/* layer chips */}
      <div style={{ position: "absolute", top: 8, left: 10, display: "flex", gap: 5, flexWrap: "wrap" }}>
        {LAYERS.map(([label, on]) => (
          <span
            key={label}
            title={on ? "" : "coming in a later Phase 3 step"}
            style={{
              fontFamily: font.mono,
              fontSize: 9,
              padding: "2px 7px",
              borderRadius: 3,
              border: `1px solid ${on ? C.ember : C.line}`,
              color: on ? C.ember : C.faint,
            }}
          >
            {label}
          </span>
        ))}
      </div>

      {/* count */}
      <div style={{ position: "absolute", bottom: 8, left: 10, fontFamily: font.mono, fontSize: 9, color: C.dim, display: "flex", gap: 8, alignItems: "center" }}>
        <span>
          {placed.length} active incident{placed.length === 1 ? "" : "s"} · click a pin
        </span>
        {evacCount > 0 && (
          <span style={{ color: C.evac, display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ width: 7, height: 7, borderRadius: 4, background: C.evac, display: "inline-block" }} />
            {evacCount} under evacuation
          </span>
        )}
      </div>
    </div>
  );
}
