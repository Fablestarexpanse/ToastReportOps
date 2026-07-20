/** Design tokens — mirror of app/globals.css / the mockup palette. */
export const C = {
  bg: "#14181d",
  panel: "#1b2128",
  panelUp: "#222a33",
  line: "#2e3843",
  txt: "#d7dee6",
  dim: "#7c8894",
  faint: "#4a555f",
  ember: "#ff9a3c",
  emberHot: "#ff5c33",
  evac: "#ff4d8f",
  ok: "#3ddbb4",
  warn: "#ffd23c",
  blue: "#5cb8ff",
  violet: "#a78bfa",
} as const;

export const font = {
  mono: "var(--font-mono), Consolas, monospace",
  disp: "var(--font-disp), 'Arial Narrow', sans-serif",
  body: "var(--font-body), 'Segoe UI', sans-serif",
} as const;

export const TIER: Record<number, string> = {
  1: C.ok,
  2: C.blue,
  3: C.warn,
  4: C.ember,
  5: C.evac,
};

export const TIER_TITLES: Record<number, string> = {
  1: "Official",
  2: "Semi-official / sensors",
  3: "Scanner / trusted",
  4: "Media",
  5: "Community / social",
};

export const confColor = (c: number) => (c > 0.8 ? C.ok : c > 0.6 ? C.warn : C.evac);

/* One z-index scale for the whole workstation.
 *
 * Leaflet numbers its own panes internally — tiles 200, overlays 400, markers
 * 600, tooltips 650, popups 700, controls 800 — and those competed directly
 * with app chrome, because the map container does not isolate them. The OPTIONS
 * panel sat at 200 and the map painted straight over its middle: header and
 * buttons visible, everything between them hidden behind California.
 *
 * So: the map is pinned into its own stacking context at MAP, and every modal
 * sits above Leaflet's highest pane by a wide margin. */
export const Z = {
  map: 0, // the map holder — isolates every Leaflet pane inside it
  mapControl: 500, // chips drawn over the map, inside its context
  toast: 900,
  dropdown: 1200, // alert bell and similar top-bar popovers
  modal: 2000, // OPTIONS, dead-letter, anything that must own the screen
} as const;
