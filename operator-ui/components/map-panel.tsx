"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Incident, PerimeterCollection, fetchPerimeters, hasActiveEvac } from "@/lib/api";
import { CA_OUTLINE } from "@/lib/california";
import { C, Z, font } from "@/lib/theme";
import { age } from "@/lib/time";

/* Real slippy map (Leaflet, bundled locally — no CDN, no API key).

   PSPS is an operating condition here, so tiles are optional: the bundled
   California outline is always drawn underneath, and with basemap "offline"
   no tile server is contacted at all. Fire positions come from our own ledger
   either way, so the map keeps working with the network down. */

type BaseSpec = {
  url: string;
  attribution: string;
  label: string;
  maxNativeZoom?: number;
};

const BASEMAPS: Record<string, BaseSpec | null> = {
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
  // USGS National Map orthoimagery: US government, public domain, no key —
  // no third-party terms to honour, which matters for a public-safety tool.
  satellite: {
    url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
    attribution: 'Imagery: <a href="https://www.usgs.gov/">USGS</a> The National Map (public domain)',
    label: "SATELLITE",
    maxNativeZoom: 16, // upscale past this rather than serving blank tiles
  },
};

const CA_CENTER: [number, number] = [37.4, -119.6];
const DEFAULT_ZOOM = 6; // whole state in view
// Below this zoom the operator is looking at the state, not a street corner,
// so a temporary imagery swap reverts to their chosen basemap.
const WIDE_VIEW_ZOOM = 11;

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
  focus?: { lat: number; lon: number; zoom: number; nonce: number; basemap?: string } | null;
}) {
  const holder = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);
  const tiles = useRef<L.TileLayer | null>(null);
  const markers = useRef<L.LayerGroup | null>(null);
  const perimLayer = useRef<L.GeoJSON | null>(null);
  const [perimeters, setPerimeters] = useState<PerimeterCollection | null>(null);
  const [showPerimeters, setShowPerimeters] = useState(true);
  const [tileError, setTileError] = useState(false);
  const [ready, setReady] = useState(false);
  // A zoom-to-origin swaps in imagery so the operator can see the ground.
  // It is temporary: zooming back out to a regional view restores whatever
  // basemap they actually chose, so the swap never quietly becomes permanent.
  const [override, setOverride] = useState<string | null>(null);
  const effective = override ?? basemap;

  // create once
  useEffect(() => {
    if (map.current || !holder.current) return;
    const m = L.map(holder.current, {
      center: CA_CENTER,
      zoom: DEFAULT_ZOOM,
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
    const spec = BASEMAPS[effective] ?? null;
    if (!spec) return; // offline: outline only, nothing fetched
    const layer = L.tileLayer(spec.url, {
      attribution: spec.attribution,
      maxZoom: 19,
      maxNativeZoom: spec.maxNativeZoom,
      crossOrigin: true,
    });
    layer.on("tileerror", () => setTileError(true));
    layer.addTo(m);
    layer.bringToBack();
    tiles.current = layer;
  }, [effective, ready]);

  // perimeters: fetched once, refreshed on the server's own 30-minute cache.
  // A failure here is silent by design — the map is still useful without them.
  useEffect(() => {
    let live = true;
    const load = () =>
      fetchPerimeters()
        .then((p) => live && setPerimeters(p))
        .catch(() => undefined);
    load();
    const t = setInterval(load, 10 * 60 * 1000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (perimLayer.current) {
      m.removeLayer(perimLayer.current);
      perimLayer.current = null;
    }
    if (!showPerimeters || !perimeters?.features?.length) return;
    const byId = new Map(incidents.map((i) => [i.id, i]));
    const layer = L.geoJSON(perimeters as never, {
      style: (feature) => {
        const inc = byId.get(String(feature?.properties?.incident_id ?? ""));
        // an unmatched perimeter is real fire ground we just can't tie to a
        // ledger incident — drawn, but visibly quieter than a known fire
        const color = inc ? pinColor(inc) : C.dim;
        return { color, weight: 1.5, opacity: 0.9, fillColor: color, fillOpacity: 0.12 };
      },
      onEachFeature: (feature, lyr) => {
        const p = feature.properties ?? {};
        const acres = typeof p.poly_GISAcres === "number" ? Math.round(p.poly_GISAcres) : null;
        lyr.bindTooltip(
          `<b>${p.incident_name ?? p.poly_IncidentName ?? "perimeter"}</b><br/>` +
            `${acres != null ? acres.toLocaleString() + " ac mapped<br/>" : ""}` +
            `<i>mapped perimeter — hours old, not a live fire edge</i>`,
          { direction: "top", opacity: 0.95 },
        );
        if (p.incident_id) lyr.on("click", () => onSelect(String(p.incident_id)));
      },
    }).addTo(m);
    layer.bringToBack();
    perimLayer.current = layer;
    // pins stay clickable above the polygons
    markers.current?.eachLayer((l) => (l as L.CircleMarker).bringToFront?.());
  }, [perimeters, showPerimeters, incidents, onSelect, ready]);

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
    if (focus.basemap && focus.basemap !== basemap) setOverride(focus.basemap);
    map.current.flyTo([focus.lat, focus.lon], focus.zoom, { duration: 1.2 });
  }, [focus, basemap]);

  // zooming back out to a regional view ends the temporary imagery swap
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const onZoom = () => {
      if (override && m.getZoom() < WIDE_VIEW_ZOOM) setOverride(null);
    };
    m.on("zoomend", onZoom);
    return () => {
      m.off("zoomend", onZoom);
    };
  }, [override, ready]);

  // keep the selected fire in view without yanking the map around
  useEffect(() => {
    const m = map.current;
    const inc = incidents.find((i) => i.id === selected);
    if (!m || !inc || inc.lat == null || inc.lon == null) return;
    if (!m.getBounds().contains([inc.lat, inc.lon])) m.panTo([inc.lat, inc.lon]);
  }, [selected, incidents]);

  // back to the whole state on the operator's chosen basemap — one action, so
  // there is always a way out of a zoomed-in imagery view
  const resetView = useCallback(() => {
    setOverride(null);
    map.current?.flyTo(CA_CENTER, DEFAULT_ZOOM, { duration: 0.8 });
  }, []);

  const placed = incidents.filter((i) => i.lat != null && i.lon != null);
  const evacCount = placed.filter(hasActiveEvac).length;

  return (
    /* isolation + a z-index floor keeps every Leaflet pane (which numbers
       itself up to 800) inside this box, where it cannot paint over a modal */
    <div style={{ position: "relative", height: "100%", minWidth: 0, background: "#10141a", zIndex: Z.map, isolation: "isolate" }}>
      <div ref={holder} style={{ position: "absolute", inset: 0 }} />

      {/* always-available escape hatch: whole state, chosen basemap. Sits
          directly under the zoom buttons so it reads as part of that cluster. */}
      <div
        onClick={resetView}
        title="reset the map: whole of California, your chosen basemap"
        style={{
          position: "absolute",
          top: 78,
          left: 10,
          zIndex: Z.mapControl,
          width: 30,
          height: 30,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#1b2128",
          border: `1px solid ${C.line}`,
          borderRadius: 4,
          color: C.dim,
          fontSize: 14,
          cursor: "pointer",
          userSelect: "none",
          boxShadow: "0 1px 5px rgba(0,0,0,0.4)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.color = C.ember;
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color = C.dim;
        }}
      >
        ⌂
      </div>

      {override && (
        <div
          onClick={resetView}
          title="click to return to the state view and your chosen basemap"
          style={{
            position: "absolute",
            top: 8,
            right: 10,
            zIndex: Z.mapControl,
            fontFamily: font.mono,
            fontSize: 9,
            color: C.ember,
            background: C.panel + "e6",
            border: `1px solid ${C.ember}66`,
            borderRadius: 4,
            padding: "3px 8px",
            cursor: "pointer",
          }}
        >
          {BASEMAPS[override]?.label ?? override} · ↩ BACK TO MAP
        </div>
      )}

      {tileError && effective !== "offline" && (
        <div
          style={{
            position: "absolute",
            top: 8,
            left: 52,
            zIndex: Z.mapControl,
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
          zIndex: Z.mapControl,
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
        {!!perimeters?.features?.length && (
          <span
            onClick={() => setShowPerimeters((v) => !v)}
            title={perimeters.note ?? "mapped fire perimeters"}
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: showPerimeters ? C.ember : C.dim,
              border: `1px solid ${showPerimeters ? C.ember + "66" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
            }}
          >
            {showPerimeters ? "◼" : "◻"} PERIMETERS ({perimeters.features.length})
            {/* the age travels with the layer: a crisp polygon is still an
                overnight IR map, not a live fire edge */}
            {perimeters.mapped_latest ? ` · newest flown ${age(perimeters.mapped_latest)} ago` : ""}
          </span>
        )}
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
