# operator-ui

The operator workstation for Toast Report OPs — the single-human control room over the
wildfire and flood ledger. Twelve modes: OPS, EVAC, AIR, WATER, RADIO, MESH, TIMELINE,
NEWSROOM, WATCH, ARCHIVE, SOURCES, ANALYTICS (what each does: the root
[README](../README.md#whats-in-the-workstation)).

## Running it

Normal path: double-click `ToastReport.cmd` in the repo root — it starts the Docker
backend and this UI on **port 3005** in an Edge app window. Dev path: `npm run dev` in
this directory (or `npm --prefix operator-ui run dev -- -p 3005` from the root).

All requests go to `/api/*`, which `next.config.ts` rewrites to the operator API
(`TOAST_API_URL`, default `http://127.0.0.1:8400`). The API refuses state-changing
requests whose browser `Origin` isn't loopback, so open the UI as `localhost` or
`127.0.0.1`, not via a LAN name.

## Checks (same as CI)

```bash
npm ci
npm run lint
npx tsc --noEmit
npm run build
```

There are no UI unit tests yet; the build plus the backend's HTTP integration tests
are the safety net.

## House style

- One component per mode under `components/` (`workstation.tsx` holds the shell, OPS,
  ARCHIVE and SOURCES; `newsroom.tsx`, `water-mode.tsx`, `air-mode.tsx`, … the rest).
- Typed API client in `lib/api.ts`; design tokens in `lib/theme.ts`; time formatting in
  `lib/time.ts`; `lib/html.ts` escapes external strings before they enter any raw HTML
  (Leaflet tooltips).
- Inline styles, dark ops theme — no CSS framework churn.
- Fonts are self-hosted so the UI works offline during a PSPS outage; the map has an
  OFFLINE basemap for the same reason.
- External text is hostile: React escaping by default; the one `dangerouslySetInnerHTML`
  renders markdown the backend has already sanitised (nh3).

## Boundary

Localhost only, never deployed. The public site is generated separately by the backend
(`toast/sitegen/`); this UI is for the operator's eyes only.
