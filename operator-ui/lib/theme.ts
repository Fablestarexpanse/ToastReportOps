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
