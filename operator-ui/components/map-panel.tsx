"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  Aircraft,
  AircraftSet,
  Hotspot,
  HotspotSet,
  Incident,
  PerimeterCollection,
  WindSet,
  fetchAircraft,
  fetchHotspots,
  fetchPerimeters,
  fetchWind,
  hasActiveEvac,
  saveSettings,
} from "@/lib/api";
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
  // Esri World Hillshade — pure land shape, no roads or labels, and it stays
  // sharp all the way to street zoom (USGS shaded relief blanked out past ~z13,
  // which was the "terrain disappears when I zoom in"). Ridgelines, drainages
  // and aspect — how a fire runs. Add names with the LABELS overlay when wanted.
  terrain: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}",
    attribution: "Terrain: Esri, USGS, NGA, NASA — hillshade",
    label: "TERRAIN",
    maxNativeZoom: 16,
  },
  // USGS topographic — the full sheet: contour lines, roads, labels, water.
  topo: {
    url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
    attribution: 'Topo: <a href="https://www.usgs.gov/">USGS</a> The National Map (public domain)',
    label: "TOPO",
    maxNativeZoom: 16,
  },
};

// Transparent overlays that ride on top of any basemap — the "enable the items
// you want" half. Place labels for a clean terrain base; water features.
const OVERLAYS: Record<string, { url: string; label: string; attribution: string }> = {
  labels: {
    url: "https://{s}.basemaps.cartocdn.com/rastertiles/light_only_labels/{z}/{x}/{y}{r}.png",
    label: "LABELS",
    attribution: "© OpenStreetMap · © CARTO",
  },
  water: {
    url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}",
    label: "WATER",
    attribution: "USGS The National Map (public domain)",
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

// Aircraft symbols by firefighting role — so a retardant bomber, the lead plane
// spotting for it, and a helo read differently at a glance. Glyphs, not photos,
// because they must stay legible at map scale.
const ROLE_STYLE: Record<string, { glyph: string; color: string; size: number; label: string }> = {
  tanker: { glyph: "✈", color: C.emberHot, size: 22, label: "AIR TANKER (retardant)" },
  air_attack: { glyph: "▲", color: C.warn, size: 18, label: "air attack / lead plane" },
  helicopter: { glyph: "✜", color: C.blue, size: 21, label: "helicopter" },
  other: { glyph: "▲", color: C.faint, size: 14, label: "aircraft" },
};

const COMPASS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
function compassOf(deg: number): string {
  return COMPASS_16[Math.round(deg / 22.5) % 16];
}

// A hotspot's colour is its heat: fire radiative power in MW. This is the
// "how intense" read the pins cannot give — a satellite saw this pixel glowing.
function hotspotColor(frp: number | null): string {
  if (frp == null) return C.warn;
  if (frp >= 50) return C.emberHot; // roaring
  if (frp >= 10) return C.ember; // active
  return C.warn; // smouldering / small
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
  const hotspotLayer = useRef<L.LayerGroup | null>(null);
  const aircraftLayerRef = useRef<L.LayerGroup | null>(null);
  const windLayerRef = useRef<L.LayerGroup | null>(null);
  const overlayRefs = useRef<Record<string, L.TileLayer>>({});
  const [overlays, setOverlays] = useState<Record<string, boolean>>({});
  const [perimeters, setPerimeters] = useState<PerimeterCollection | null>(null);
  const [showPerimeters, setShowPerimeters] = useState(true);
  const [hotspots, setHotspots] = useState<HotspotSet | null>(null);
  const [showHotspots, setShowHotspots] = useState(true);
  const [aircraft, setAircraft] = useState<AircraftSet | null>(null);
  const [showAircraft, setShowAircraft] = useState(true);
  const [wind, setWind] = useState<WindSet | null>(null);
  const [showWind, setShowWind] = useState(true);
  const [showKey, setShowKey] = useState(false);
  const [tileError, setTileError] = useState(false);
  const [ready, setReady] = useState(false);
  // A zoom-to-origin swaps in imagery so the operator can see the ground.
  // It is temporary: zooming back out to a regional view restores whatever
  // basemap they actually chose, so the swap never quietly becomes permanent.
  const [override, setOverride] = useState<string | null>(null);
  // the operator's on-map basemap choice, seeded from settings and persisted so
  // it survives a reload and stays in sync with OPTIONS
  const [chosen, setChosen] = useState(basemap);
  useEffect(() => setChosen(basemap), [basemap]); // a change in OPTIONS wins
  const pickBasemap = useCallback((key: string) => {
    setChosen(key);
    setOverride(null); // an explicit pick ends any temporary zoom-to-origin swap
    try {
      localStorage.setItem("toast-map-basemap", key);
    } catch {
      // ignore
    }
    // basemap is a display preference — persist it the same way OPTIONS does, or
    // syncDisplayPrefs would pull the old server value back on the next reload
    saveSettings({ map_basemap: key }).catch(() => undefined);
  }, []);
  const effective = override ?? chosen;

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

  // transparent overlays (labels, water) on top of the basemap — toggled on
  // demand, so a clean terrain base stays clean until you ask for detail
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    for (const [key, spec] of Object.entries(OVERLAYS)) {
      const on = !!overlays[key];
      const existing = overlayRefs.current[key];
      if (on && !existing) {
        const ol = L.tileLayer(spec.url, {
          attribution: spec.attribution,
          maxZoom: 19,
          crossOrigin: true,
          zIndex: 5, // above the basemap tiles, below markers (their own pane)
        }).addTo(m);
        overlayRefs.current[key] = ol;
      } else if (!on && existing) {
        m.removeLayer(existing);
        delete overlayRefs.current[key];
      }
    }
  }, [overlays, ready]);

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

  // satellite hotspots: the same 10-minute refresh cadence as perimeters, and
  // silent on failure — the map is still useful without them
  useEffect(() => {
    let live = true;
    const load = () =>
      fetchHotspots(24)
        .then((h) => live && setHotspots(h))
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
    if (hotspotLayer.current) {
      m.removeLayer(hotspotLayer.current);
      hotspotLayer.current = null;
    }
    if (!showHotspots || !hotspots?.hotspots?.length) return;
    const group = L.layerGroup();
    for (const h of hotspots.hotspots as Hotspot[]) {
      const color = hotspotColor(h.frp);
      // a small diamond-ish dot; low FIRMS confidence reads as fainter, never hidden
      L.circleMarker([h.lat, h.lon], {
        radius: 3.5,
        color,
        weight: 0,
        fillColor: color,
        fillOpacity: h.confidence === "l" ? 0.4 : 0.85,
      })
        .bindTooltip(
          `satellite hotspot<br/>${h.frp != null ? `${Math.round(h.frp)} MW radiated` : "intensity n/a"}` +
            `<br/>detected ${age(h.observed_at)} ago` +
            `<br/><i>a hot pixel at overpass, not a confirmed flame</i>`,
          { direction: "top", opacity: 0.95 },
        )
        .addTo(group);
    }
    group.addTo(m);
    group.eachLayer((l) => (l as L.Path).bringToBack?.());
    hotspotLayer.current = group;
    // keep incident pins above the hotspot dusting
    markers.current?.eachLayer((l) => (l as L.CircleMarker).bringToFront?.());
  }, [hotspots, showHotspots, ready]);

  // aircraft near active fires: faster refresh than the static layers because
  // an orbit only reads if sampled often. Silent on failure like the rest.
  useEffect(() => {
    let live = true;
    const load = () =>
      fetchAircraft()
        .then((a) => live && setAircraft(a))
        .catch(() => undefined);
    load();
    const t = setInterval(load, 45 * 1000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (aircraftLayerRef.current) {
      m.removeLayer(aircraftLayerRef.current);
      aircraftLayerRef.current = null;
    }
    if (!showAircraft || !aircraft?.aircraft?.length) return;
    const group = L.layerGroup();
    for (const a of aircraft.aircraft as Aircraft[]) {
      const style = ROLE_STYLE[a.role] ?? ROLE_STYLE.other;
      // the trail shows the orbit — a tanker's racetrack over the fire
      if (a.fire_ops && a.trail.length > 1) {
        L.polyline(a.trail, { color: style.color, weight: 1.5, opacity: 0.5, dashArray: "3 4" }).addTo(group);
      }
      // helicopters do not have a meaningful heading arrow when hovering, and a
      // rotor glyph reads at a glance; fixed-wing get a heading-rotated shape
      const heading = a.track ?? 0;
      const rotate = a.role === "helicopter" ? 0 : heading;
      const icon = L.divIcon({
        className: "",
        html:
          `<div style="transform:rotate(${rotate}deg);color:${style.color};` +
          `font-size:${a.fire_ops ? style.size : 14}px;line-height:1;` +
          `text-shadow:0 0 3px #000;opacity:${a.fire_ops ? 1 : 0.7}">${style.glyph}</div>`,
        iconSize: [26, 26],
        iconAnchor: [13, 13],
      });
      const label =
        `${a.flight || a.type || "aircraft"}${a.descr ? ` — ${a.descr}` : ""}` +
        `<br/>${a.alt_ft != null ? `${a.alt_ft.toLocaleString()} ft` : "alt n/a"}` +
        `${a.gs != null ? ` · ${Math.round(a.gs)} kt` : ""}` +
        `<br/>seen ${age(a.seen_at)} ago` +
        (a.fire_ops
          ? `<br/><b style="color:${style.color}">${style.label}</b>`
          : "<br/><i>aircraft near the fire — role unknown</i>");
      L.marker([a.lat, a.lon], { icon }).bindTooltip(label, { direction: "top", opacity: 0.95 }).addTo(group);
    }
    group.addTo(m);
    aircraftLayerRef.current = group;
  }, [aircraft, showAircraft, ready]);

  // wind at each fire: refreshed on the server's own 20-min cadence
  useEffect(() => {
    let live = true;
    const load = () =>
      fetchWind()
        .then((w) => live && setWind(w))
        .catch(() => undefined);
    load();
    const t = setInterval(load, 5 * 60 * 1000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (windLayerRef.current) {
      m.removeLayer(windLayerRef.current);
      windLayerRef.current = null;
    }
    if (!showWind || !wind?.winds?.length) return;
    const group = L.layerGroup();
    for (const w of wind.winds) {
      // wind_degrees is where the wind blows FROM (met convention); the fire is
      // pushed the OTHER way, so the arrow points toward where it is going.
      const toward = (w.wind_degrees + 180) % 360;
      const mph = w.wind_mph ?? 0;
      // longer arrow for stronger wind; red when it is fire-driving (>15 mph)
      const strong = mph >= 15;
      const color = strong ? C.emberHot : C.blue;
      const icon = L.divIcon({
        className: "",
        html:
          `<div style="transform:rotate(${toward}deg);color:${color};` +
          `font-size:${Math.min(26, 13 + mph * 0.5)}px;line-height:1;` +
          `text-shadow:0 0 3px #000;font-weight:700">↑</div>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12],
      });
      L.marker([w.lat, w.lon], { icon, interactive: true })
        .bindTooltip(
          `wind ${w.wind_mph != null ? Math.round(w.wind_mph) : "?"} mph from ${w.wind_compass ?? "?"}` +
            `${w.gust_mph ? ` · gusts ${Math.round(w.gust_mph)}` : ""}` +
            `<br/>pushing the fire toward the ${compassOf(toward)}` +
            (w.forecast
              ? `<br/>next ${w.forecast.window_hours}h: peak ${Math.round(w.forecast.peak_wind_mph)}` +
                `${w.forecast.peak_gust_mph ? ` g ${Math.round(w.forecast.peak_gust_mph)}` : ""} mph` +
                `${w.forecast.rising ? " — rising" : ""} (NWS forecast)`
              : "") +
            `<br/><i>NWS obs${w.station_km != null ? ` · station ${w.station_km.toFixed(0)} km` : ""}</i>`,
          { direction: "top", opacity: 0.95, offset: [0, -6] },
        )
        .addTo(group);
    }
    group.addTo(m);
    windLayerRef.current = group;
  }, [wind, showWind, ready]);

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

  // Bring a NEWLY selected fire into view — but only on the selection change.
  // `incidents` is deliberately out of the deps: it refetches every poll, and
  // with it in, every refresh re-ran this and yanked the map back to the fire
  // the moment the operator panned away to look at something else.
  const pannedTo = useRef<string | null>(null);
  useEffect(() => {
    if (selected === pannedTo.current) return; // same selection, do not chase it
    pannedTo.current = selected;
    const m = map.current;
    const inc = incidents.find((i) => i.id === selected);
    if (!m || !inc || inc.lat == null || inc.lon == null) return;
    if (!m.getBounds().contains([inc.lat, inc.lon])) m.panTo([inc.lat, inc.lon]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

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

      {/* basemap switcher, right on the map — one click, no going into OPTIONS.
          Topo for terrain, satellite for the ground, dark for everything else. */}
      <div
        style={{
          position: "absolute",
          top: 114,
          left: 10,
          zIndex: Z.mapControl,
          display: "flex",
          flexDirection: "column",
          gap: 2,
          background: "#1b2128",
          border: `1px solid ${C.line}`,
          borderRadius: 4,
          padding: 2,
          boxShadow: "0 1px 5px rgba(0,0,0,0.4)",
        }}
      >
        {(["dark", "terrain", "topo", "satellite", "osm", "offline"] as const).map((key) => {
          const label = key === "osm" ? "STREET" : (BASEMAPS[key]?.label ?? "OFFLINE");
          const active = chosen === key;
          return (
            <span
              key={key}
              onClick={() => pickBasemap(key)}
              title={`basemap: ${label.toLowerCase()}`}
              style={{
                fontFamily: font.mono,
                fontSize: 8,
                letterSpacing: 0.5,
                textAlign: "center",
                padding: "3px 6px",
                borderRadius: 3,
                cursor: "pointer",
                color: active ? C.bg : C.dim,
                background: active ? C.ember : "transparent",
                fontWeight: active ? 700 : 400,
              }}
            >
              {label}
            </span>
          );
        })}
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
        {!!hotspots?.count && (
          <span
            onClick={() => setShowHotspots((v) => !v)}
            title={hotspots.note}
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: showHotspots ? C.emberHot : C.dim,
              border: `1px solid ${showHotspots ? C.emberHot + "66" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
            }}
          >
            {showHotspots ? "◼" : "◻"} HOTSPOTS ({hotspots.count})
            {hotspots.latest_pass ? ` · last pass ${age(hotspots.latest_pass)} ago` : ""}
          </span>
        )}
        {(["labels", "water"] as const).map((key) => (
          <span
            key={key}
            onClick={() => setOverlays((o) => ({ ...o, [key]: !o[key] }))}
            title={
              key === "labels"
                ? "place names on top of any basemap — turn on for a clean terrain base"
                : "USGS water features (streams, lakes)"
            }
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: overlays[key] ? C.txt : C.dim,
              border: `1px solid ${overlays[key] ? C.txt + "66" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
            }}
          >
            {overlays[key] ? "◼" : "◻"} {OVERLAYS[key].label}
          </span>
        ))}
        {!!wind?.count && (
          <span
            onClick={() => setShowWind((v) => !v)}
            title="surface wind at each fire (NWS). Arrow points where the fire is being pushed."
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: showWind ? C.blue : C.dim,
              border: `1px solid ${showWind ? C.blue + "66" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
            }}
          >
            {showWind ? "◼" : "◻"} WIND ({wind.count})
          </span>
        )}
        {!!aircraft?.count && (
          <span
            onClick={() => setShowAircraft((v) => !v)}
            title={aircraft.note}
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: showAircraft ? C.blue : C.dim,
              border: `1px solid ${showAircraft ? C.blue + "66" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
            }}
          >
            {showAircraft ? "◼" : "◻"} AIRCRAFT ({aircraft.count})
            {aircraft.fire_ops ? ` · ${aircraft.fire_ops} air ops` : ""}
          </span>
        )}
        {evacCount > 0 && (
          <span style={{ color: C.evac, display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ width: 7, height: 7, borderRadius: 4, background: C.evac, display: "inline-block" }} />
            {evacCount} under evacuation
          </span>
        )}
      </div>

      <MapKey open={showKey} onToggle={() => setShowKey((v) => !v)} />
    </div>
  );
}

/* What everything on the map means. A colour-coded map with no key is a puzzle,
   and this one carries life-safety colours — the evac pink especially must never
   be guessed at. Collapsed to a small ? by default so it never covers the map. */
function MapKey({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const dot = (color: string, ring = false) => (
    <span
      style={{
        width: 10,
        height: 10,
        borderRadius: 6,
        background: ring ? "transparent" : color,
        border: ring ? `2px solid ${color}` : "none",
        display: "inline-block",
        flexShrink: 0,
      }}
    />
  );
  const Row = ({ swatch, label }: { swatch: React.ReactNode; label: string }) => (
    <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "2px 0" }}>
      {swatch}
      <span>{label}</span>
    </div>
  );
  return (
    <div style={{ position: "absolute", top: 8, right: 10, zIndex: Z.mapControl }}>
      <div
        onClick={onToggle}
        title={open ? "hide the key" : "what do the colours mean?"}
        style={{
          marginLeft: "auto",
          width: open ? "auto" : 24,
          height: 24,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 5,
          background: "#1b2128",
          border: `1px solid ${C.line}`,
          borderRadius: 4,
          color: C.dim,
          fontFamily: font.mono,
          fontSize: 10,
          cursor: "pointer",
          padding: open ? "0 8px" : 0,
          boxShadow: "0 1px 5px rgba(0,0,0,0.4)",
        }}
      >
        {open ? "✕ KEY" : "?"}
      </div>
      {open && (
        <div
          style={{
            marginTop: 4,
            background: C.panel + "f2",
            border: `1px solid ${C.line}`,
            borderRadius: 6,
            padding: "8px 10px",
            fontFamily: font.mono,
            fontSize: 9,
            color: C.dim,
            width: 210,
            boxShadow: "0 4px 16px rgba(0,0,0,0.5)",
          }}
        >
          <div style={{ color: C.faint, letterSpacing: 1, marginBottom: 4 }}>INCIDENT PINS</div>
          <Row swatch={dot(C.evac)} label="under evacuation" />
          <Row swatch={dot(C.emberHot)} label="active, under 60% contained" />
          <Row swatch={dot(C.warn)} label="active, 60%+ contained" />
          <Row swatch={dot(C.dim)} label="contained / closed" />
          <Row swatch={dot(C.evac, true)} label="pulsing ring = evac or selected" />
          <div style={{ color: C.faint, letterSpacing: 1, margin: "6px 0 4px" }}>SATELLITE HOTSPOTS</div>
          <Row swatch={dot(C.emberHot)} label="≥ 50 MW — roaring" />
          <Row swatch={dot(C.ember)} label="10–50 MW — active" />
          <Row swatch={dot(C.warn)} label="under 10 MW — small / faint" />
          <div style={{ color: C.faint, letterSpacing: 1, margin: "6px 0 4px" }}>WIND</div>
          <Row swatch={<span style={{ color: C.blue }}>↑</span>} label="points where the fire is pushed" />
          <Row swatch={<span style={{ color: C.emberHot }}>↑</span>} label="red / longer = ≥15 mph (fire-driving)" />
          <div style={{ color: C.faint, letterSpacing: 1, margin: "6px 0 4px" }}>AIRCRAFT</div>
          <Row swatch={<span style={{ color: C.emberHot }}>✈</span>} label="air tanker (retardant bomber)" />
          <Row swatch={<span style={{ color: C.warn }}>▲</span>} label="air attack / lead plane (spotter)" />
          <Row swatch={<span style={{ color: C.blue }}>✜</span>} label="helicopter" />
          <Row swatch={<span style={{ color: C.faint }}>▲</span>} label="other aircraft near the fire" />
          <Row swatch={<span style={{ color: C.blue }}>┄</span>} label="dashed = its recent orbit" />
          <div style={{ color: C.faint, letterSpacing: 1, margin: "6px 0 4px" }}>SHAPES</div>
          <Row swatch={dot(C.dim)} label="big dot = fire (size ∝ acreage)" />
          <Row swatch={<span style={{ width: 10, height: 6, border: `1px solid ${C.dim}`, display: "inline-block" }} />} label="small dot = satellite pixel" />
          <Row swatch={<span style={{ width: 12, height: 8, border: `1.5px solid ${C.ember}`, background: C.ember + "22", display: "inline-block" }} />} label="outline = mapped perimeter" />
          <div style={{ color: C.faint, marginTop: 6, lineHeight: 1.4 }}>
            Hotspots and perimeters are hours old — a satellite pass, not a live edge.
          </div>
        </div>
      )}
    </div>
  );
}
