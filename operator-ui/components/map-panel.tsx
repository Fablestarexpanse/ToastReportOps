"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Incident, hasActiveEvac } from "@/lib/api";
import { CA_OUTLINE } from "@/lib/california";
import { C, font } from "@/lib/theme";

/* Real slippy map (Leaflet, bundled locally — no CDN, no API key).

   PSPS is an operating condition here, so tiles are optional: the bundled
   California outline is always drawn underneath, and with basemap "offline"
   no tile server is contacted at all. Fire positions come from our own ledger
   either way, so the map keeps working with the network down. */

const BASEMAPS: Record<string, { url: string; attribution: string; label: string } | null> = {
  offline: null,
  dark: {
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    attribution: '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> · © CARTO',
    label: "DARK",
  },
  osm: {
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    label: "STREET",
  },
};

const CA_CENTER: [number, number] = [37.4, -119.6];

function pinColor(inc: Incident): string {
  // People-at-risk outranks fire behavior: an active evacuation is the thing
  // the operator must see first, whatever the containment number says.
  if (hasActiveEvac(inc)) return C.evac;
  if (inc.status === "contained" || inc.status === "frozen") return C.dim;
  return (inc.containment_pct ?? 0) >= 60 ? C.warn : C.emberHot;
}

function pinRadius(acreage: number | null): number {
  if (!acreage || acreage <= 0) return 5;
  return Math.max(5, Math.min(22, 4 + Math.log10(acreage + 1) * 3.4));
}

export default function MapPanel({
  incidents,
  selected,
  onSelect,
  basemap = "dark",
  focus,
}: {
  incidents: Incident[];
  selected: string | null;
  onSelect: (id: string) => void;
  basemap?: string;
  /** bump `nonce` to fly to a point; `zoom` 16+ is street level */
  focus?: { lat: number; lon: number; zoom: number; nonce: number } | null;
}) {
  const holder = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);
  const tiles = useRef<L.TileLayer | null>(null);
  const markers = useRef<L.LayerGroup | null>(null);
  const [tileError, setTileError] = useState(false);
  const [ready, setReady] = useState(false);

  // create once
  useEffect(() => {
    if (map.current || !holder.current) return;
    const m = L.map(holder.current, {
      center: CA_CENTER,
      zoom: 6,
      zoomControl: true,
      attributionControl: true,
      preferCanvas: true,
    });
    L.polyline([CA_OUTLINE.map(([lon, lat]) => [lat, lon] as [number, number])], {
      color: C.line,
      weight: 1.5,
      opacity: 0.9,
      interactive: false,
    }).addTo(m);
    markers.current = L.layerGroup().addTo(m);
    map.current = m;
    setReady(true);
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  // basemap switching
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (tiles.current) {
      m.removeLayer(tiles.current);
      tiles.current = null;
    }
    setTileError(false);
    const spec = BASEMAPS[basemap] ?? null;
    if (!spec) return; // offline: outline only, nothing fetched
    const layer = L.tileLayer(spec.url, {
      attribution: spec.attribution,
      maxZoom: 18,
      crossOrigin: true,
    });
    layer.on("tileerror", () => setTileError(true));
    layer.addTo(m);
    layer.bringToBack();
    tiles.current = layer;
  }, [basemap, ready]);

  const draw = useCallback(() => {
    const group = markers.current;
    if (!group) return;
    group.clearLayers();
    for (const inc of incidents) {
      if (inc.lat == null || inc.lon == null) continue;
      const color = pinColor(inc);
      const evac = hasActiveEvac(inc);
      const isSel = inc.id === selected;
      if (evac || isSel) {
        // pulse ring — an evacuation must catch the eye anywhere on the map
        L.circleMarker([inc.lat, inc.lon], {
          radius: pinRadius(inc.acreage) + 7,
          color,
          weight: 1.5,
          fill: false,
          className: "tr-pulse",
          interactive: false,
        }).addTo(group);
      }
      const dot = L.circleMarker([inc.lat, inc.lon], {
        radius: pinRadius(inc.acreage),
        color: isSel ? C.txt : color,
        weight: isSel ? 2 : 1,
        fillColor: color,
        fillOpacity: 0.8,
      })
        .addTo(group)
        .on("click", () => onSelect(inc.id));
      dot.bindTooltip(
        `<b>${inc.canonical_name}</b><br/>${inc.county ? inc.county + " County · " : ""}` +
          `${inc.acreage != null ? Math.round(inc.acreage).toLocaleString() + " ac" : "size unknown"}` +
          `${inc.containment_pct != null ? " · " + inc.containment_pct + "% contained" : ""}` +
          `${evac ? '<br/><b style="color:#ff4d8f">EVACUATION IN EFFECT</b>' : ""}`,
        { direction: "top", opacity: 0.95 },
      );
    }
  }, [incidents, selected, onSelect]);

  useEffect(() => {
    if (ready) draw();
  }, [ready, draw]);

  // operator asked to go somewhere specific — obey exactly, and animate so it
  // stays obvious that the view moved rather than the data changing
  useEffect(() => {
    if (!focus || !map.current) return;
    map.current.flyTo([focus.lat, focus.lon], focus.zoom, { duration: 1.2 });
  }, [focus]);

  // keep the selected fire in view without yanking the map around
  useEffect(() => {
    const m = map.current;
    const inc = incidents.find((i) => i.id === selected);
    if (!m || !inc || inc.lat == null || inc.lon == null) return;
    if (!m.getBounds().contains([inc.lat, inc.lon])) m.panTo([inc.lat, inc.lon]);
  }, [selected, incidents]);

  const placed = incidents.filter((i) => i.lat != null && i.lon != null);
  const evacCount = placed.filter(hasActiveEvac).length;

  return (
    <div style={{ position: "relative", height: "100%", minWidth: 0, background: "#10141a" }}>
      <div ref={holder} style={{ position: "absolute", inset: 0 }} />

      {tileError && basemap !== "offline" && (
        <div
          style={{
            position: "absolute",
            top: 8,
            left: 52,
            zIndex: 500,
            fontFamily: font.mono,
            fontSize: 9,
            color: C.warn,
            background: C.panel + "e6",
            border: `1px solid ${C.warn}55`,
            borderRadius: 4,
            padding: "3px 7px",
          }}
        >
          ⚠ basemap tiles unavailable — fires still plotted on the offline outline
        </div>
      )}

      <div
        style={{
          position: "absolute",
          bottom: 8,
          left: 10,
          zIndex: 500,
          fontFamily: font.mono,
          fontSize: 9,
          color: C.dim,
          background: C.panel + "cc",
          borderRadius: 4,
          padding: "3px 7px",
          display: "flex",
          gap: 8,
          alignItems: "center",
          pointerEvents: "none",
        }}
      >
        <span>
          {placed.length} plotted · click a pin
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
