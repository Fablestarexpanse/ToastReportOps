# operator-ui

The operator workstation for Toast Report OPs — the single-human control room
over the wildfire ledger: OPS, AIR, RADIO, MESH, NEWSROOM, WATCH, ARCHIVE,
SOURCES, ANALYTICS.

## Running it

Normal path: double-click `ToastReport.cmd` in the repo root — it starts the
Docker backend and this UI on **port 3005** in an Edge app window. Dev path:
`npm run dev` in this directory.

The API lives at `http://127.0.0.1:8400` (Docker stack; the base URL comes from
`TOAST_API_URL`, read by `next.config.ts`).

## House style

- Single-file modes: each mode is one component under `components/`
  (`workstation.tsx` holds the shell + OPS).
- Inline styles, dark ops theme (palette as constants) — no CSS framework churn.
- Fonts: JetBrains Mono (data), Barlow / Barlow Condensed (body/display),
  self-hosted so the UI works offline during a PSPS outage.

## Boundary

Localhost only, never deployed. The public site is generated separately by the
backend (`toast/sitegen/`); this UI is for the operator's eyes only.
