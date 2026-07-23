"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  Aircraft,
  AircraftSet,
  Camera,
  CameraSet,
  ClosureSet,
  EvacZoneSet,
  FirisSet,
  FireWeatherSet,
  Hotspot,
  HotspotSet,
  Incident,
  PerimeterCollection,
  WindField,
  WindSet,
  fetchAircraft,
  fetchCameras,
  fetchClosures,
  fetchEvacZones,
  fetchFiris,
  fetchFireWeather,
  fetchHotspots,
  fetchPerimeters,
  fetchWind,
  fetchWindField,
  hasActiveEvac,
  saveSettings,
} from "@/lib/api";
import { CA_OUTLINE } from "@/lib/california";
import { escHtml } from "@/lib/html";
import { C, Z, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

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
// you want" half. Place labels for a clean terrain base; water features; and
// the satellite layers below. All require network — in a PSPS outage on the
// OFFLINE basemap they simply fail to tiles and the vector outline stands.
type OverlaySpec = {
  url: string;
  label: string;
  attribution: string;
  // NASA GIBS layers bake a date/time into the path. "day" = yesterday UTC
  // (today's global mosaic is often incomplete); "latest" = the literal token
  // GIBS resolves to the most recent frame (GOES, ~10-min cadence).
  time?: "day" | "latest";
  maxNativeZoom?: number; // GIBS caps native zoom per layer; upscale past it
  opacity?: number;
  note?: string; // hover explanation on the toggle chip
};

// GIBS WMTS REST, EPSG:3857. Note the {z}/{y}/{x} order (GIBS puts row before
// column) — Leaflet fills by name, so the template controls it.
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";

const OVERLAYS: Record<string, OverlaySpec> = {
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
  // CLOUDS = GOES-West geostationary GeoColor: live cloud/smoke imagery,
  // refreshed every ~10 minutes. Enabling it auto-plays a looping time-lapse.
  // (VIIRS true-colour was dropped — same picture, once a day instead of live.)
  clouds: {
    url: `${GIBS}/GOES-West_ABI_GeoColor/default/{time}/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png`,
    label: "CLOUDS",
    attribution: "NASA GIBS · NOAA GOES-West ABI GeoColor",
    time: "latest",
    maxNativeZoom: 7,
    note: "GOES-West geostationary cloud + smoke imagery — enable to auto-play a looping ~2-hour time-lapse. Network required.",
  },
  // Aerosol optical depth: a coarse "smoke over this area" density wash.
  // Translucent so it reads on top of imagery or the basemap.
  smoke: {
    url: `${GIBS}/MODIS_Combined_Value_Added_AOD/default/{time}/GoogleMapsCompatible_Level6/{z}/{y}/{x}.png`,
    label: "SMOKE (AOD)",
    attribution: "NASA GIBS · MODIS aerosol optical depth (prev. day)",
    time: "day",
    maxNativeZoom: 6,
    opacity: 0.55,
    note: "MODIS aerosol optical depth — coarse smoke-density signal, previous day. Network required.",
  },
  // VIIRS short-wave IR composite (M11-I2-I1): active fire glows orange, fresh
  // burn scars go dark red, healthy vegetation green — the satellite's heat
  // signature of a fire, from yesterday's polar overpass. A full image, so it
  // covers the basemap when on (fire pins ride above it).
  fireswir: {
    url: `${GIBS}/VIIRS_NOAA20_CorrectedReflectance_BandsM11-I2-I1/default/{time}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`,
    label: "FIRE (SWIR)",
    attribution: "NASA GIBS · VIIRS NOAA-20 SWIR M11-I2-I1 — active fire glows orange (prev. day)",
    time: "day",
    maxNativeZoom: 9,
    note: "VIIRS short-wave IR — active fire glows orange, burn scars dark red. Previous-day overpass. Network required.",
  },
};

// yesterday UTC as YYYY-MM-DD — the safe date for GIBS daily mosaics
function gibsDay(): string {
  return new Date(Date.now() - 24 * 3600 * 1000).toISOString().slice(0, 10);
}

// The CLOUDS layer, made genuinely transparent. GOES GeoColor is a full image
// (clouds ON a coloured land/ocean surface), so as a plain tile it covers the
// map. This tile layer redraws each tile on a canvas and keys the surface out
// by alpha: white clouds have a high minimum-channel value, any coloured or
// dark surface has a low one — so alpha ∝ min(R,G,B) leaves only the clouds,
// everything else fully clear, over whatever layers are beneath. GIBS sends
// Access-Control-Allow-Origin:* so the pixel readback is not tainted.
const CloudTileLayer = L.TileLayer.extend({
  createTile(this: L.TileLayer, coords: L.Coords, done: L.DoneCallback): HTMLElement {
    const size = this.getTileSize();
    const tile = document.createElement("canvas");
    tile.width = size.x;
    tile.height = size.y;
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      const ctx = tile.getContext("2d");
      if (!ctx) {
        done(undefined, tile);
        return;
      }
      ctx.drawImage(img, 0, 0, size.x, size.y);
      try {
        const d = ctx.getImageData(0, 0, size.x, size.y);
        const p = d.data;
        for (let i = 0; i < p.length; i += 4) {
          const mn = Math.min(p[i], p[i + 1], p[i + 2]); // whiteness of the pixel
          // only genuine cloud-white survives: haze, bright land and dark
          // surface all sit below ~135 and go fully clear; above it ramps
          // steeply to opaque so real clouds stay solid
          const a = mn < 135 ? 0 : Math.min(255, (mn - 135) * 2.6);
          p[i + 3] = a;
        }
        ctx.putImageData(d, 0, 0);
      } catch {
        // if readback ever fails, the drawn tile still shows — never blank
      }
      done(undefined, tile);
    };
    img.onerror = (e) => done(e instanceof Error ? e : new Error("tile load failed"), tile);
    img.src = this.getTileUrl(coords);
    return tile;
  },
});

function cloudLayer(url: string, opts: L.TileLayerOptions): L.TileLayer {
  const Ctor = CloudTileLayer as unknown as new (u: string, o: L.TileLayerOptions) => L.TileLayer;
  return new Ctor(url, opts);
}

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
  const windFieldLayerRef = useRef<L.LayerGroup | null>(null);
  const cameraLayerRef = useRef<L.LayerGroup | null>(null);
  const firewxLayerRef = useRef<L.GeoJSON | null>(null);
  const closureLayerRef = useRef<L.LayerGroup | null>(null);
  const evacZoneLayerRef = useRef<L.GeoJSON | null>(null);
  const firisLayerRef = useRef<L.GeoJSON | null>(null);
  const pinRenderer = useRef<L.SVG | null>(null);
  const evacLabelLayerRef = useRef<L.LayerGroup | null>(null);
  const evacLabelZoomHandler = useRef<(() => void) | null>(null);
  const overlayRefs = useRef<Record<string, L.TileLayer>>({});
  const [overlays, setOverlays] = useState<Record<string, boolean>>({});
  // GOES is animation-only: enabling it auto-plays a continuous time-lapse.
  // Preloaded frame layers + the play timer + the current-frame clock.
  const goesFramesRef = useRef<L.TileLayer[]>([]);
  const goesTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [goesFrameTime, setGoesFrameTime] = useState<string | null>(null);
  const [perimeters, setPerimeters] = useState<PerimeterCollection | null>(null);
  const [showPerimeters, setShowPerimeters] = useState(true);
  const [hotspots, setHotspots] = useState<HotspotSet | null>(null);
  const [showHotspots, setShowHotspots] = useState(true);
  const [aircraft, setAircraft] = useState<AircraftSet | null>(null);
  const [showAircraft, setShowAircraft] = useState(true);
  const [wind, setWind] = useState<WindSet | null>(null);
  const [showWind, setShowWind] = useState(true);
  const [windField, setWindField] = useState<WindField | null>(null);
  const [cameras, setCameras] = useState<CameraSet | null>(null);
  const [showCameras, setShowCameras] = useState(true);
  const [firewx, setFirewx] = useState<FireWeatherSet | null>(null);
  const [showFirewx, setShowFirewx] = useState(true);
  const [evacZones, setEvacZones] = useState<EvacZoneSet | null>(null);
  const [showEvacZones, setShowEvacZones] = useState(true);
  const [firis, setFiris] = useState<FirisSet | null>(null);
  const [showFiris, setShowFiris] = useState(true);
  const [closures, setClosures] = useState<ClosureSet | null>(null);
  const [showClosures, setShowClosures] = useState(true);
  // the in-tool frame viewer: which camera, and a nonce so REFRESH re-asks
  const [viewCam, setViewCam] = useState<Camera | null>(null);
  const [frameNonce, setFrameNonce] = useState(0);
  const [frameErr, setFrameErr] = useState(false);
  const incidentsRef = useRef<Incident[]>(incidents);
  incidentsRef.current = incidents;
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
    // Fire pins live in their own pane ABOVE the marker pane (600). Wind
    // arrows, aircraft and camera icons are L.markers that would otherwise
    // stack over the canvas-rendered dots and swallow their clicks — a fire
    // with a wind arrow on it (i.e. every fire) became unclickable. The fire
    // is the primary object on this map; nothing outranks it.
    m.createPane("firepins");
    const pinPane = m.getPane("firepins");
    if (pinPane) pinPane.style.zIndex = "650";
    // The pins MUST render as SVG, not canvas: with preferCanvas, a canvas in
    // this pane is a full-viewport sheet above the marker pane that swallows
    // every hover and click on the layers below — cameras, wind arrows, evac
    // zones all went dead. SVG only captures events on the painted circles;
    // everywhere else the mouse falls through to whatever is underneath.
    pinRenderer.current = L.svg({ pane: "firepins" });
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
      // GOES is handled entirely by the time-lapse effect below (it animates,
      // never draws a single static frame), so skip it here
      if (key === "clouds") continue;
      const on = !!overlays[key];
      const existing = overlayRefs.current[key];
      if (on && !existing) {
        const url =
          spec.time === "day"
            ? spec.url.replace("{time}", gibsDay())
            : spec.time === "latest"
              ? spec.url.replace("{time}", "default")
              : spec.url;
        const ol = L.tileLayer(url, {
          attribution: spec.attribution,
          maxZoom: 19,
          // GIBS caps native zoom per layer; beyond it Leaflet upscales the
          // last real tiles instead of requesting non-existent ones (404s)
          maxNativeZoom: spec.maxNativeZoom ?? 19,
          opacity: spec.opacity ?? 1,
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

  // GOES time-lapse — the reason to animate: geostationary frames every 10 min,
  // so ~2 hours of them played on a continuous loop is the smoke plume in
  // motion. Enabling the GOES-WEST layer IS the animation — it auto-plays and
  // never stops until toggled off. GIBS publishes each frame ~40 min behind
  // real time, so the window starts ~50 min back to land only on frames that
  // exist; all preloaded, then cycled by opacity so playback is instant with
  // no per-frame network stall.
  useEffect(() => {
    const m = map.current;
    const stop = () => {
      if (goesTimerRef.current) {
        clearInterval(goesTimerRef.current);
        goesTimerRef.current = null;
      }
      if (m) for (const l of goesFramesRef.current) m.removeLayer(l);
      goesFramesRef.current = [];
      setGoesFrameTime(null);
    };
    if (!m || !ready || !overlays.clouds) {
      stop();
      return;
    }
    const FRAMES = 12;
    const STEP_MS = 10 * 60 * 1000;
    const LAG_MS = 50 * 60 * 1000; // skip GIBS's publish latency at the leading edge
    const end = Math.floor((Date.now() - LAG_MS) / STEP_MS) * STEP_MS;
    const times = Array.from({ length: FRAMES }, (_, i) =>
      new Date(end - (FRAMES - 1 - i) * STEP_MS).toISOString().slice(0, 19) + "Z",
    );
    const layers = times.map((t) =>
      cloudLayer(OVERLAYS.clouds.url.replace("{time}", t), {
        attribution: OVERLAYS.clouds.attribution,
        maxZoom: 19,
        maxNativeZoom: 7,
        opacity: 0,
        crossOrigin: "anonymous", // required so the canvas pixel readback is clean
        zIndex: 5,
      }).addTo(m),
    );
    goesFramesRef.current = layers;
    let idx = 0;
    const show = (i: number) => {
      layers.forEach((l, j) => l.setOpacity(j === i ? 1 : 0));
      setGoesFrameTime(times[i]);
    };
    show(0);
    goesTimerRef.current = setInterval(() => {
      idx = (idx + 1) % layers.length;
      show(idx);
    }, 650);
    return stop;
  }, [overlays.clouds, ready]);

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
          `<b>${escHtml(p.incident_name ?? p.poly_IncidentName ?? "perimeter")}</b><br/>` +
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
        `${escHtml(a.flight || a.type || "aircraft")}${a.descr ? ` — ${escHtml(a.descr)}` : ""}` +
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

  // ALERTCalifornia cameras near the SELECTED fire only — 2,100 statewide
  // markers would be noise; the ones that can see this fire are signal. Reads
  // incidents via a ref so a poll never re-triggers the fetch (the snap-back
  // lesson, applied).
  useEffect(() => {
    setViewCam(null);
    if (!selected) {
      setCameras(null);
      return;
    }
    const inc = incidentsRef.current.find((i) => i.id === selected);
    if (!inc || inc.lat == null || inc.lon == null) {
      setCameras(null);
      return;
    }
    let live = true;
    fetchCameras(inc.lat, inc.lon)
      .then((c) => live && setCameras(c))
      .catch(() => live && setCameras(null));
    return () => {
      live = false;
    };
  }, [selected]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (cameraLayerRef.current) {
      m.removeLayer(cameraLayerRef.current);
      cameraLayerRef.current = null;
    }
    if (!showCameras || !cameras?.cameras?.length) return;
    const group = L.layerGroup();
    // great-circle destination point — for drawing each camera's view cone
    const dest = (lat: number, lon: number, brgDeg: number, km: number): [number, number] => {
      const R = 6371, br = (brgDeg * Math.PI) / 180, la1 = (lat * Math.PI) / 180, lo1 = (lon * Math.PI) / 180, dR = km / R;
      const la2 = Math.asin(Math.sin(la1) * Math.cos(dR) + Math.cos(la1) * Math.sin(dR) * Math.cos(br));
      const lo2 = lo1 + Math.atan2(Math.sin(br) * Math.sin(dR) * Math.cos(la1), Math.cos(dR) - Math.sin(la1) * Math.sin(la2));
      return [(la2 * 180) / Math.PI, (lo2 * 180) / Math.PI];
    };
    for (const cam of cameras.cameras) {
      // while its frame is up in the viewer, the camera on the map blinks —
      // so the eye can connect the picture to the lens that took it
      const viewing = viewCam?.id === cam.id;
      // the view cone: where this lens is looking RIGHT NOW (az drifts as
      // operators pan PTZ cameras — the index refreshes hourly)
      if (cam.az != null) {
        const fov = cam.fov != null && cam.fov > 0 && cam.fov <= 180 ? cam.fov : 60;
        const ring: [number, number][] = [[cam.lat, cam.lon]];
        for (let a = cam.az - fov / 2; a <= cam.az + fov / 2; a += 5) {
          ring.push(dest(cam.lat, cam.lon, a, 4));
        }
        L.polygon(ring, {
          color: "#45d6d0", weight: 1, opacity: 0.5, dashArray: "3 4",
          fillColor: "#45d6d0", fillOpacity: 0.08, interactive: false,
        }).addTo(group);
      }
      const icon = L.divIcon({
        className: "",
        // ◎ = a lens; cyan is unused by any other layer so cameras read as
        // their own thing at a glance. The box is the CLICK TARGET — keep it
        // comfortably larger than the glyph.
        html:
          `<div class="${viewing ? "tr-blink" : ""}" style="color:${viewing ? "#7ef5ef" : "#45d6d0"};` +
          `font-size:${viewing ? 26 : 20}px;line-height:28px;text-align:center;` +
          `text-shadow:0 0 4px #000,0 0 6px #000${viewing ? ",0 0 10px #45d6d0" : ""};` +
          `font-weight:700;cursor:pointer">◎</div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
      });
      L.marker([cam.lat, cam.lon], { icon, interactive: true })
        .bindTooltip(
          `<b>${escHtml(cam.name)}</b><br/>${cam.distance_km.toFixed(1)} km from the fire` +
            (cam.az != null ? ` · pointing ${compassOf(cam.az)} (${Math.round(cam.az)}°)` : "") +
            (cam.elev_m != null ? `<br/>elev ${Math.round(cam.elev_m * 3.281).toLocaleString()} ft` : "") +
            `<br/><i>click to view — ALERTCalifornia</i>`,
          { direction: "top", opacity: 0.95, offset: [0, -10] },
        )
        .on("click", () => {
          setFrameErr(false);
          setFrameNonce((n) => n + 1);
          setViewCam(cam);
        })
        .addTo(group);
    }
    group.addTo(m);
    cameraLayerRef.current = group;
    // viewCam in the deps: opening/closing the viewer redraws the icons so
    // the one being watched blinks and stops blinking at the right moments
  }, [cameras, showCameras, ready, viewCam]);

  // fire-weather (red flag) zones + Caltrans closures: slow-moving layers,
  // refreshed on the server's own ~10-min cache cadence
  useEffect(() => {
    let live = true;
    const load = () => {
      fetchFireWeather().then((f) => live && setFirewx(f)).catch(() => undefined);
      fetchClosures().then((c) => live && setClosures(c)).catch(() => undefined);
    };
    load();
    const t = setInterval(load, 10 * 60 * 1000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  // Evacuation zones (Cal OES, rebuilds every 5 min): the highest-stakes layer
  // on the board — poll on its own faster cadence
  useEffect(() => {
    let live = true;
    const load = () => fetchEvacZones().then((z) => live && setEvacZones(z)).catch(() => undefined);
    load();
    const t = setInterval(load, 2 * 60 * 1000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  // FIRIS IR heat perimeters (CAL FIRE): the flown fire footprint, tier-1.
  // Refreshed on the server's own 15-min cache; a failure is silent.
  useEffect(() => {
    let live = true;
    const load = () => fetchFiris().then((f) => live && setFiris(f)).catch(() => undefined);
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
    if (firisLayerRef.current) {
      m.removeLayer(firisLayerRef.current);
      firisLayerRef.current = null;
    }
    if (!showFiris || !firis?.features?.length) return;
    const withShape = firis.features.filter((f) => f.geometry);
    if (!withShape.length) return;
    // heat-perimeter red; intense heat brighter; isolated heat a dashed hint
    const styleFor = (kind: string | null) => {
      const k = (kind || "").toLowerCase();
      if (k.includes("intense")) return { color: C.emberHot, weight: 2.5, fillOpacity: 0.28 };
      if (k.includes("isolated")) return { color: C.warn, weight: 1.5, dashArray: "4 4", fillOpacity: 0.1 };
      return { color: C.ember, weight: 2, fillOpacity: 0.18 };
    };
    const layer = L.geoJSON(
      { type: "FeatureCollection", features: withShape } as GeoJSON.FeatureCollection,
      {
        style: (feature) => {
          const p = feature?.properties as FirisSet["features"][number]["properties"];
          return { ...styleFor(p?.type), fillColor: C.ember, interactive: true };
        },
        onEachFeature: (feature, lyr) => {
          const p = feature.properties as FirisSet["features"][number]["properties"];
          const flown = p.poly_DateCurrent ? `${age(p.poly_DateCurrent)} ago` : "unknown";
          lyr.bindTooltip(
            `<b style="color:#ff9a3c">IR ${escHtml(p.type ?? "heat perimeter")}</b>` +
              `${p.incident_name ? ` — ${escHtml(p.incident_name)}` : ""}` +
              `${p.area_acres != null ? `<br/>${Math.round(p.area_acres).toLocaleString()} acres` : ""}` +
              `<br/>flown ${flown}` +
              `<br/><i>CAL FIRE FIRIS · infrared flight footprint (tier 1)</i>`,
            { sticky: true, opacity: 0.95 },
          );
        },
      },
    ).addTo(m);
    firisLayerRef.current = layer;
  }, [firis, showFiris, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (evacZoneLayerRef.current) {
      m.removeLayer(evacZoneLayerRef.current);
      evacZoneLayerRef.current = null;
    }
    if (evacLabelLayerRef.current) {
      m.removeLayer(evacLabelLayerRef.current);
      evacLabelLayerRef.current = null;
    }
    if (evacLabelZoomHandler.current) {
      m.off("zoomend", evacLabelZoomHandler.current);
      evacLabelZoomHandler.current = null;
    }
    if (!showEvacZones || !evacZones?.features?.length) return;
    const zoneLabels = L.layerGroup();
    // status colors mirror the official public convention: order = the alarm
    // color, warning = amber, shelter-in-place = blue, anything else dim
    const colorOf = (status: string | null): string => {
      const s = (status || "").toLowerCase();
      if (s.includes("order")) return C.evac;
      if (s.includes("warning")) return C.warn;
      if (s.includes("shelter")) return C.blue;
      return C.dim;
    };
    const layer = L.geoJSON(
      { type: "FeatureCollection", features: evacZones.features.filter((f) => f.geometry) } as GeoJSON.FeatureCollection,
      {
        style: (feature) => {
          const c = colorOf((feature?.properties as EvacZoneSet["features"][number]["properties"])?.STATUS ?? null);
          return { color: c, weight: 2, fillColor: c, fillOpacity: 0.22, interactive: true };
        },
        onEachFeature: (feature, lyr) => {
          const p = feature.properties as EvacZoneSet["features"][number]["properties"];
          const esc = escHtml;
          // VERBATIM: status and official text exactly as the county issued them
          const zoneLabel = [p.ZONE_ID, p.ZONE_NAME && p.ZONE_NAME !== p.ZONE_ID ? p.ZONE_NAME : null]
            .filter(Boolean).map((s) => esc(s)).join(" · ");
          lyr.bindTooltip(
            `<b style="color:#ff4d8f">${esc(p.STATUS)?.toUpperCase()}</b> — zone ${zoneLabel || "?"}` +
              `${p.COUNTY ? `<br/>${esc(p.COUNTY)} County` : ""}` +
              `${p.incident_name ? ` · nearest fire: ${esc(p.incident_name)}` : ""}` +
              `${p.issued_at ? `<br/>issued ${fmtStamp(p.issued_at)} (${age(p.issued_at)} ago)` : ""}` +
              `${p.last_updated_at ? ` · updated ${age(p.last_updated_at)} ago` : ""}` +
              `<br/><b style="color:#ff4d8f">STILL IN EFFECT</b> as of ${age(evacZones.fetched_at)} ago` +
              ` <i>(zones leave this layer when lifted)</i>` +
              `${p.CRITICAL_INFO ? `<br/><b>${esc(p.CRITICAL_INFO)}</b>` : ""}` +
              `${p.PUBLIC_INFO ? `<br/>${esc(p.PUBLIC_INFO).slice(0, 300)}` : ""}` +
              `${p.NOTES ? `<br/>${esc(p.NOTES).slice(0, 200)}` : ""}` +
              `<br/><i>Cal OES · official status, verbatim · verify locally before acting</i>`,
            { sticky: true, opacity: 0.97 },
          );
          // a label ON the section itself: short zone id, status word, issued age
          const b = (lyr as L.Polygon).getBounds?.();
          if (b?.isValid()) {
            const short = esc((p.ZONE_ID || "").split("-").pop() || p.ZONE_ID || "?");
            const s = (p.STATUS || "").toLowerCase();
            const word = s.includes("order") ? "ORDER" : s.includes("warning") ? "WARNING" : esc((p.STATUS || "").toUpperCase().slice(0, 12));
            const col = colorOf(p.STATUS);
            zoneLabels.addLayer(
              L.marker(b.getCenter(), {
                interactive: false,
                keyboard: false,
                icon: L.divIcon({
                  className: "",
                  iconSize: [0, 0],
                  html:
                    `<div style="transform:translate(-50%,-50%);pointer-events:none;text-align:center;` +
                    `font-family:ui-monospace,monospace;font-size:38px;font-weight:700;color:${col};` +
                    `text-shadow:0 0 6px #000,0 0 10px #000,0 2px 8px #000;white-space:nowrap;line-height:1.15">` +
                    `${short}<br/><span style="font-size:20px;font-weight:600">${word}` +
                    `${p.issued_at ? ` · ${age(p.issued_at)}` : ""}</span></div>`,
                }),
              }),
            );
          }
        },
      },
    ).addTo(m);
    evacZoneLayerRef.current = layer;
    // labels only once zoomed in enough to read them — statewide they are noise
    const syncZoneLabels = () => {
      if (m.getZoom() >= 10) {
        if (!m.hasLayer(zoneLabels)) zoneLabels.addTo(m);
      } else if (m.hasLayer(zoneLabels)) {
        m.removeLayer(zoneLabels);
      }
    };
    syncZoneLabels();
    m.on("zoomend", syncZoneLabels);
    evacLabelLayerRef.current = zoneLabels;
    evacLabelZoomHandler.current = syncZoneLabels;
  }, [evacZones, showEvacZones, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (firewxLayerRef.current) {
      m.removeLayer(firewxLayerRef.current);
      firewxLayerRef.current = null;
    }
    if (!showFirewx || !firewx?.features?.length) return;
    const withShape = firewx.features.filter((f) => f.geometry);
    if (!withShape.length) return;
    const layer = L.geoJSON(
      { type: "FeatureCollection", features: withShape } as GeoJSON.FeatureCollection,
      {
        style: () => ({
          color: C.evac,
          weight: 1.5,
          dashArray: "6 4",
          fillColor: C.evac,
          fillOpacity: 0.08,
          interactive: true,
        }),
        onEachFeature: (feature, lyr) => {
          const p = feature.properties as FireWeatherSet["features"][number]["properties"];
          lyr.bindTooltip(
            `<b>${escHtml(p.event)}</b> — FORECAST, not a fire<br/>${escHtml(p.headline)}` +
              `${p.ends ? `<br/>until ${p.ends.slice(0, 16).replace("T", " ")}` : ""}`,
            { sticky: true, opacity: 0.95 },
          );
        },
      },
    ).addTo(m);
    firewxLayerRef.current = layer;
  }, [firewx, showFirewx, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (closureLayerRef.current) {
      m.removeLayer(closureLayerRef.current);
      closureLayerRef.current = null;
    }
    if (!showClosures || !closures?.closures?.length) return;
    const group = L.layerGroup();
    for (const c of closures.closures) {
      const icon = L.divIcon({
        className: "",
        html: `<div style="color:${C.warn};font-size:12px;line-height:1;text-shadow:0 0 3px #000;font-weight:700">⛔</div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      L.marker([c.lat, c.lon], { icon, interactive: true })
        .bindTooltip(
          `<b>${escHtml(c.route ?? "highway")} CLOSED</b>${c.place ? ` near ${escHtml(c.place)}` : ""}` +
            `${c.type_of_work ? `<br/>${escHtml(c.type_of_work)}` : ""}` +
            `${c.near ? `<br/>${c.near.km} km from ${escHtml(c.near.name)}` : ""}` +
            `<br/><i>Caltrans — state highways only</i>`,
          { direction: "top", opacity: 0.95, offset: [0, -6] },
        )
        .addTo(group);
    }
    group.addTo(m);
    closureLayerRef.current = group;
  }, [closures, showClosures, ready]);

  // wind at each fire (authoritative NWS) + the ambient field (Open-Meteo grid)
  // — both refreshed on the servers' own caches
  useEffect(() => {
    let live = true;
    const load = () => {
      fetchWind()
        .then((w) => live && setWind(w))
        .catch(() => undefined);
      fetchWindField()
        .then((f) => live && setWindField(f))
        .catch(() => undefined);
    };
    load();
    const t = setInterval(load, 5 * 60 * 1000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  // the ambient wind field: a dim grid of small arrows behind the per-fire
  // ones, so regional flow reads across the whole map. Same WIND toggle owns
  // both; non-interactive so it never intercepts a fire pin's hover/click.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (windFieldLayerRef.current) {
      m.removeLayer(windFieldLayerRef.current);
      windFieldLayerRef.current = null;
    }
    if (!showWind || !windField?.arrows?.length) return;
    const group = L.layerGroup();
    for (const a of windField.arrows) {
      const toward = (a.deg + 180) % 360; // deg is FROM; arrow points downwind
      const strong = a.mph >= 20;
      const color = strong ? C.emberHot : a.mph >= 10 ? C.warn : C.blue;
      const icon = L.divIcon({
        className: "",
        html:
          `<div style="transform:rotate(${toward}deg);color:${color};opacity:0.5;` +
          `pointer-events:none;font-size:${Math.min(18, 10 + a.mph * 0.28)}px;` +
          `line-height:1;text-shadow:0 0 2px #000">↑</div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      L.marker([a.lat, a.lon], { icon, interactive: false }).addTo(group);
    }
    group.addTo(m);
    windFieldLayerRef.current = group;
  }, [windField, showWind, ready]);

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
          `wind ${w.wind_mph != null ? Math.round(w.wind_mph) : "?"} mph from ${escHtml(w.wind_compass ?? "?")}` +
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
          pane: "firepins",
          renderer: pinRenderer.current ?? undefined,
        }).addTo(group);
      }
      const dot = L.circleMarker([inc.lat, inc.lon], {
        radius: pinRadius(inc.acreage),
        color: isSel ? C.txt : color,
        weight: isSel ? 2 : 1,
        fillColor: color,
        fillOpacity: 0.8,
        pane: "firepins",
        renderer: pinRenderer.current ?? undefined,
      })
        .addTo(group)
        .on("click", () => onSelect(inc.id));
      dot.bindTooltip(
        `<b>${escHtml(inc.canonical_name)}</b><br/>${inc.county ? escHtml(inc.county) + " County · " : ""}` +
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
        {!!firis?.features?.length && (
          <span
            onClick={() => setShowFiris((v) => !v)}
            title={firis.note ?? "FIRIS infrared heat perimeters (CAL FIRE)"}
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: showFiris ? C.emberHot : C.dim,
              border: `1px solid ${showFiris ? C.emberHot + "66" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
            }}
          >
            {showFiris ? "◼" : "◻"} IR HEAT ({firis.features.length}) · FIRIS
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
        {(["labels", "water", "clouds", "smoke", "fireswir"] as const).map((key) => {
          const spec = OVERLAYS[key];
          // satellite layers get their own accent so the network-heavy imagery
          // reads distinct from the lightweight vector overlays
          const sat = key === "clouds" || key === "smoke" || key === "fireswir";
          const accent = sat ? C.ok : C.txt;
          const title =
            spec.note ??
            (key === "labels"
              ? "place names on top of any basemap — turn on for a clean terrain base"
              : "USGS water features (streams, lakes)");
          return (
            <span
              key={key}
              onClick={() => setOverlays((o) => ({ ...o, [key]: !o[key] }))}
              title={title}
              style={{
                pointerEvents: "auto",
                cursor: "pointer",
                color: overlays[key] ? accent : C.dim,
                border: `1px solid ${overlays[key] ? accent + "66" : C.line}`,
                borderRadius: 3,
                padding: "1px 5px",
              }}
            >
              {overlays[key] ? "◼" : "◻"} {spec.label}
              {key === "clouds" && overlays.clouds && goesFrameTime
                ? ` ${goesFrameTime.slice(11, 16)}Z`
                : ""}
            </span>
          );
        })}
        {(!!wind?.count || !!windField?.count) && (
          <span
            onClick={() => setShowWind((v) => !v)}
            title="bright arrows = official NWS wind at each fire (points where the fire is pushed); dim grid = ambient regional flow (Open-Meteo model, context only)"
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: showWind ? C.blue : C.dim,
              border: `1px solid ${showWind ? C.blue + "66" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
            }}
          >
            {showWind ? "◼" : "◻"} WIND ({wind?.count ?? 0})
          </span>
        )}
        {!!evacZones?.count && (
          <span
            onClick={() => setShowEvacZones((v) => !v)}
            title={`${evacZones.note} Statuses right now: ${Object.entries(evacZones.statuses).map(([s, n]) => `${s}: ${n}`).join(", ")}`}
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: showEvacZones ? C.evac : C.dim,
              border: `1px solid ${showEvacZones ? C.evac + "88" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
              fontWeight: 700,
            }}
          >
            {showEvacZones ? "◼" : "◻"} EVAC ZONES ({evacZones.count}) · Cal OES
          </span>
        )}
        {!!firewx?.count && (
          <span
            onClick={() => setShowFirewx((v) => !v)}
            title={`${firewx.note}${firewx.unresolved ? ` (${firewx.unresolved} zone(s) unresolved — listed, not mapped)` : ""}`}
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: showFirewx ? C.evac : C.dim,
              border: `1px solid ${showFirewx ? C.evac + "66" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
            }}
          >
            {showFirewx ? "◼" : "◻"} RED FLAG ({firewx.count}) · forecast
          </span>
        )}
        {!!closures?.count && (
          <span
            onClick={() => setShowClosures((v) => !v)}
            title={closures.note}
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: showClosures ? C.warn : C.dim,
              border: `1px solid ${showClosures ? C.warn + "66" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
            }}
          >
            {showClosures ? "◼" : "◻"} CLOSURES ({closures.count}) · state hwys only
          </span>
        )}
        {!!cameras?.count && (
          <span
            onClick={() => setShowCameras((v) => !v)}
            title={`ALERTCalifornia cameras within 40 km of the selected fire (${cameras.total_in_range} in range). Click a ◎ on the map to view.`}
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              color: showCameras ? "#45d6d0" : C.dim,
              border: `1px solid ${showCameras ? "#45d6d066" : C.line}`,
              borderRadius: 3,
              padding: "1px 5px",
            }}
          >
            {showCameras ? "◼" : "◻"} CAMS ({cameras.count})
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

      {viewCam && cameras && (
        /* In-tool frame viewer. The image loads in THIS browser directly from
           ALERTCalifornia — the backend never touches imagery, nothing is
           stored, and refresh is a deliberate click, not a poll. */
        <div
          style={{
            position: "absolute",
            top: 40,
            right: 10,
            width: 400,
            zIndex: Z.mapControl,
            background: C.panel + "f8",
            border: "1px solid #45d6d066",
            borderRadius: 8,
            boxShadow: "0 6px 24px rgba(0,0,0,0.55)",
            overflow: "hidden",
            fontFamily: font.mono,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 10px" }}>
            <span style={{ color: "#45d6d0", fontSize: 12 }}>◎</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ color: C.txt, fontSize: 11, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {viewCam.name}
              </div>
              <div style={{ color: C.dim, fontSize: 8.5 }}>
                {viewCam.distance_km.toFixed(1)} km from fire
                {viewCam.az != null ? ` · pointing ${compassOf(viewCam.az)}` : ""}
                {viewCam.elev_m != null ? ` · ${Math.round(viewCam.elev_m * 3.281).toLocaleString()} ft` : ""}
              </div>
            </div>
            <span
              onClick={() => {
                setFrameErr(false);
                setFrameNonce((n) => n + 1);
              }}
              title="load the current frame"
              style={{ cursor: "pointer", color: C.blue, fontSize: 12 }}
            >
              ↻
            </span>
            <span onClick={() => setViewCam(null)} style={{ cursor: "pointer", color: C.dim, fontSize: 12 }}>
              ✕
            </span>
          </div>
          {frameErr ? (
            <div style={{ padding: "28px 12px", textAlign: "center", color: C.dim, fontSize: 10 }}>
              frame unavailable — the camera may be offline
            </div>
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={`${cameras.frame_url_template.replace("{id}", viewCam.id)}?t=${frameNonce}`}
              alt={`${viewCam.name} — live view`}
              onError={() => setFrameErr(true)}
              style={{ width: "100%", aspectRatio: "16/9", objectFit: "cover", display: "block", background: "#000" }}
            />
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", fontSize: 8 }}>
            <span style={{ color: C.faint, flex: 1 }}>
              {cameras.attribution} · loads direct from their server, nothing stored
            </span>
            <a
              href={`https://cameras.alertcalifornia.org/?id=${encodeURIComponent(viewCam.id)}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: C.blue, textDecoration: "none", whiteSpace: "nowrap" }}
            >
              FULL VIEWER (PTZ) ↗
            </a>
          </div>
        </div>
      )}
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
          <div style={{ color: C.faint, letterSpacing: 1, margin: "6px 0 4px" }}>EVACUATION ZONES (Cal OES)</div>
          <Row swatch={<span style={{ width: 12, height: 8, border: `1.5px solid ${C.evac}`, background: C.evac + "38", display: "inline-block" }} />} label="EVACUATION ORDER — leave now" />
          <Row swatch={<span style={{ width: 12, height: 8, border: `1.5px solid ${C.warn}`, background: C.warn + "38", display: "inline-block" }} />} label="evacuation warning — be ready" />
          <Row swatch={<span style={{ width: 12, height: 8, border: `1.5px solid ${C.blue}`, background: C.blue + "38", display: "inline-block" }} />} label="shelter in place" />
          <div style={{ color: C.faint, letterSpacing: 1, margin: "6px 0 4px" }}>HAZARD AREAS</div>
          <Row swatch={<span style={{ width: 12, height: 8, border: `1.5px dashed ${C.evac}`, background: C.evac + "14", display: "inline-block" }} />} label="red flag zone — forecast, not fire" />
          <Row swatch={<span style={{ color: C.warn }}>⛔</span>} label="highway closed (state hwys only)" />
          <div style={{ color: C.faint, letterSpacing: 1, margin: "6px 0 4px" }}>CAMERAS</div>
          <Row swatch={<span style={{ color: "#45d6d0" }}>◎</span>} label="ALERTCalifornia camera — click to view" />
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
