/** The dark basemap every map shares (OPS, AIR, MESH).
 *
 * Keyless on purpose: CARTO's free dark tiles began answering "API KEY REQUIRED"
 * in 2026 — as a watermark image with HTTP 200, so nothing even errors. This is
 * Esri's public World Dark Gray Canvas, on the same server as the TERRAIN layer.
 * Its tiles stop at zoom 16; Leaflet upscales past that. */
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas";

export const DARK_BASEMAP = {
  url: `${ESRI}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
  attribution: "Basemap: Esri, HERE, Garmin, © OpenStreetMap contributors",
  maxNativeZoom: 16,
};

/** Light-on-dark place labels, drawn over any basemap (the LABELS overlay). */
export const DARK_LABELS = {
  url: `${ESRI}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
  attribution: "Labels: Esri, HERE, Garmin, © OpenStreetMap contributors",
  maxNativeZoom: 16,
};
