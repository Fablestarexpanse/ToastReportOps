"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  AirAircraft,
  AirBases,
  AirFocus,
  AirLog,
  AirMil,
  Incident,
  fetchAirBases,
  fetchAirFocus,
  fetchAirLog,
  fetchAirMil,
  fetchAircraftPhoto,
  fetchIncidents,
} from "@/lib/api";
import { DARK_BASEMAP } from "@/lib/basemap";
import { escHtml } from "@/lib/html";
import { C, Z, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

/* AIR mode: the aviation-first view.

   The OPS map shows aircraft as one layer among many; this screen inverts the
   priority. One fire's airspace, sampled every ~5 seconds (the backend
   throttles upstream to respect airplanes.live's shared 1 req/s budget, so a
   fast UI poll degrades to store reads, never API abuse), 15-minute trails so
   a tanker orbit and its drop runs read as a pattern, a sortable fleet board,
   follow mode, tanker-base watch, and the military picture over California. */

const ROLE_STYLE: Record<string, { glyph: string; color: string; label: string }> = {
  tanker: { glyph: "✈", color: C.emberHot, label: "TANKER" },
  air_attack: { glyph: "▲", color: C.warn, label: "AIR ATTACK" },
  helicopter: { glyph: "✜", color: C.blue, label: "HELO" },
  other: { glyph: "▲", color: C.faint, label: "OTHER" },
};

const FOCUS_POLL_MS = 5_000;
const BASES_POLL_MS = 15_000;
const MIL_POLL_MS = 30_000;
const LOG_POLL_MS = 60_000;

function fmtDur(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}`;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export default function AirMode({ toast }: { toast: (msg: string) => void }) {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [fireId, setFireId] = useState<string | null>(null);
  const [focus, setFocus] = useState<AirFocus | null>(null);
  const [bases, setBases] = useState<AirBases | null>(null);
  const [mil, setMil] = useState<AirMil | null>(null);
  const [log, setLog] = useState<AirLog | null>(null);
  const [followHex, setFollowHex] = useState<string | null>(null);
  const [roleFilter, setRoleFilter] = useState<Set<string>>(new Set());
  const [fireOpsOnly, setFireOpsOnly] = useState(false);
  const [showMil, setShowMil] = useState(false);
  const [photo, setPhoto] = useState<{ hex: string; url: string; credit: string | null } | null>(null);
  const [lastPollAt, setLastPollAt] = useState<string | null>(null);

  const holder = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);
  const acLayer = useRef<L.LayerGroup | null>(null);
  const fireLayer = useRef<L.LayerGroup | null>(null);
  const milLayer = useRef<L.LayerGroup | null>(null);
  const baseLayer = useRef<L.LayerGroup | null>(null);
  const followRef = useRef<string | null>(null);
  followRef.current = followHex;
  const [ready, setReady] = useState(false);

  const fire = useMemo(() => incidents.find((i) => i.id === fireId) ?? null, [incidents, fireId]);

  /* ---------- incidents: pick the default fire (most air activity wins) */
  useEffect(() => {
    let live = true;
    fetchIncidents()
      .then((r) => {
        if (!live) return;
        const withCoords = r.filter((i) => i.lat != null && i.status === "active");
        setIncidents(withCoords);
        setFireId((cur) => cur ?? withCoords[0]?.id ?? null);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  /* ---------- map init */
  useEffect(() => {
    if (map.current || !holder.current) return;
    const m = L.map(holder.current, {
      center: [37.4, -119.6],
      zoom: 6,
      zoomControl: true,
      attributionControl: true,
      preferCanvas: true,
    });
    L.tileLayer(DARK_BASEMAP.url, {
      attribution: DARK_BASEMAP.attribution,
      maxNativeZoom: DARK_BASEMAP.maxNativeZoom,
      maxZoom: 19,
    }).addTo(m);
    fireLayer.current = L.layerGroup().addTo(m);
    baseLayer.current = L.layerGroup().addTo(m);
    milLayer.current = L.layerGroup().addTo(m);
    acLayer.current = L.layerGroup().addTo(m);
    map.current = m;
    setReady(true);
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  /* ---------- fly to the chosen fire once per selection */
  useEffect(() => {
    if (!ready || !fire || fire.lat == null || fire.lon == null) return;
    map.current?.setView([fire.lat, fire.lon], 11);
    setFollowHex(null);
    setFocus(null);
    setLog(null);
  }, [ready, fireId]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---------- polls */
  useEffect(() => {
    if (!fireId) return;
    let live = true;
    const tick = () =>
      fetchAirFocus(fireId)
        .then((f) => {
          if (!live) return;
          setFocus(f);
          setLastPollAt(f.fetched_at);
        })
        .catch(() => undefined);
    tick();
    const t = setInterval(tick, FOCUS_POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [fireId]);

  useEffect(() => {
    let live = true;
    const tick = () => fetchAirBases().then((b) => live && setBases(b)).catch(() => undefined);
    tick();
    const t = setInterval(tick, BASES_POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    let live = true;
    const tick = () => fetchAirMil().then((m2) => live && setMil(m2)).catch(() => undefined);
    tick();
    const t = setInterval(tick, MIL_POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    if (!fireId) return;
    let live = true;
    const tick = () => fetchAirLog(fireId).then((l) => live && setLog(l)).catch(() => undefined);
    tick();
    const t = setInterval(tick, LOG_POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [fireId]);

  /* ---------- fire pin + base markers */
  useEffect(() => {
    const g = fireLayer.current;
    if (!g || !ready) return;
    g.clearLayers();
    if (fire && fire.lat != null && fire.lon != null) {
      L.circleMarker([fire.lat, fire.lon], {
        radius: 9,
        color: C.emberHot,
        weight: 2,
        fillColor: C.emberHot,
        fillOpacity: 0.35,
      })
        .bindTooltip(`<b>${escHtml(fire.canonical_name)}</b>`, { direction: "top" })
        .addTo(g);
    }
  }, [fire, ready]);

  useEffect(() => {
    const g = baseLayer.current;
    if (!g || !ready || !bases) return;
    g.clearLayers();
    for (const b of bases.bases) {
      const active = bases.sightings.some((s) => s.base === b.id && !s.departed_at);
      L.marker([b.lat, b.lon], {
        icon: L.divIcon({
          className: "",
          html: `<div style="color:${active ? C.warn : C.faint};font-size:11px;line-height:1;text-shadow:0 0 3px #000">⌂</div>`,
          iconSize: [14, 14],
          iconAnchor: [7, 7],
        }),
        interactive: true,
      })
        .bindTooltip(`${escHtml(b.name)}${active ? " — fire aircraft on field" : ""}`, { direction: "top" })
        .addTo(g);
    }
  }, [bases, ready]);

  /* ---------- aircraft + trails (the main event) */
  const visible = useMemo(() => {
    if (!focus) return [];
    return focus.aircraft.filter((a) => {
      if (fireOpsOnly && !a.fire_ops) return false;
      if (roleFilter.size && !roleFilter.has(a.role)) return false;
      return true;
    });
  }, [focus, roleFilter, fireOpsOnly]);

  useEffect(() => {
    const g = acLayer.current;
    const m = map.current;
    if (!g || !m || !ready) return;
    g.clearLayers();
    for (const a of visible) {
      const style = ROLE_STYLE[a.role] ?? ROLE_STYLE.other;
      if (a.trail.length > 1) {
        L.polyline(a.trail as [number, number][], {
          color: style.color,
          weight: 1.4,
          opacity: 0.65,
          dashArray: "3 4",
          interactive: false,
        }).addTo(g);
      }
      const isFollowed = a.hex === followRef.current;
      L.marker([a.lat, a.lon], {
        icon: L.divIcon({
          className: "",
          html:
            `<div style="transform:rotate(${(a.track ?? 0)}deg);color:${style.color};` +
            `font-size:${a.fire_ops ? 24 : 15}px;line-height:1;font-weight:700;` +
            `text-shadow:0 0 4px #000${isFollowed ? `,0 0 8px ${style.color}` : ""}">${style.glyph}</div>`,
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        }),
        interactive: true,
      })
        .bindTooltip(
          `<b>${escHtml(a.flight ?? a.hex.toUpperCase())}</b> · ${style.label}${a.military ? " · MIL" : ""}` +
            `<br/>${escHtml(a.descr ?? a.type ?? "unknown type")}` +
            `<br/>${a.alt_ft != null ? Math.round(a.alt_ft).toLocaleString() + " ft" : "alt ?"}` +
            ` · ${a.gs != null ? Math.round(a.gs) + " kt" : "spd ?"}`,
          { direction: "top", opacity: 0.95, offset: [0, -8] },
        )
        .on("click", () => selectAircraft(a))
        .addTo(g);
    }
    // follow: keep the chased aircraft centred without changing zoom
    const chased = visible.find((a) => a.hex === followRef.current);
    if (chased) m.panTo([chased.lat, chased.lon], { animate: true });
  }, [visible, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---------- military overlay */
  useEffect(() => {
    const g = milLayer.current;
    if (!g || !ready) return;
    g.clearLayers();
    if (!showMil || !mil) return;
    for (const a of mil.aircraft) {
      L.marker([a.lat, a.lon], {
        icon: L.divIcon({
          className: "",
          html: `<div style="transform:rotate(${(a.track ?? 0)}deg);color:${C.violet};font-size:13px;line-height:1;text-shadow:0 0 3px #000">▲</div>`,
          iconSize: [16, 16],
          iconAnchor: [8, 8],
        }),
        interactive: true,
      })
        .bindTooltip(
          `<b>${escHtml(a.flight ?? a.hex.toUpperCase())}</b> · MILITARY<br/>${escHtml(a.descr ?? a.type ?? "")}` +
            `<br/>${a.alt_ft != null ? Math.round(a.alt_ft).toLocaleString() + " ft" : ""}`,
          { direction: "top", opacity: 0.95 },
        )
        .addTo(g);
    }
  }, [mil, showMil, ready]);

  const selectAircraft = useCallback((a: AirAircraft) => {
    setFollowHex((cur) => (cur === a.hex ? null : a.hex));
    setPhoto(null);
    fetchAircraftPhoto(a.hex)
      .then((p) => {
        if (p.found)
          setPhoto({
            hex: a.hex,
            url: `/api/aircraft/${a.hex}/image`,
            credit: p.photographer ?? null,
          });
      })
      .catch(() => undefined);
  }, []);

  /* ---------- fleet board rows, ongoing session time merged in by hex */
  const onStation = useMemo(() => {
    const m2 = new Map<string, number>();
    for (const s of log?.sessions ?? []) if (s.ongoing) m2.set(s.hex, s.on_station_s);
    return m2;
  }, [log]);

  const chip = (label: string, on: boolean, toggle: () => void, color: string = C.blue) => (
    <span
      key={label}
      onClick={toggle}
      style={{
        cursor: "pointer",
        color: on ? color : C.dim,
        border: `1px solid ${on ? color + "66" : C.line}`,
        borderRadius: 3,
        padding: "1px 6px",
        fontFamily: font.mono,
        fontSize: 9,
        whiteSpace: "nowrap",
      }}
    >
      {on ? "◼" : "◻"} {label}
    </span>
  );

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      {/* ------------------------------------------------ left: boards */}
      <div
        style={{
          width: 390,
          minWidth: 390,
          borderRight: `1px solid ${C.line}`,
          display: "flex",
          flexDirection: "column",
          overflowY: "auto",
        }}
      >
        {/* fire selector + freshness */}
        <div style={{ padding: "10px 12px", borderBottom: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.faint, marginBottom: 6 }}>
            AIRSPACE
          </div>
          <select
            value={fireId ?? ""}
            onChange={(e) => setFireId(e.target.value || null)}
            style={{
              width: "100%",
              background: C.bg,
              color: C.txt,
              border: `1px solid ${C.line}`,
              borderRadius: 4,
              fontFamily: font.mono,
              fontSize: 11,
              padding: "6px 8px",
            }}
          >
            {incidents.map((i) => (
              <option key={i.id} value={i.id}>
                {i.canonical_name}
                {i.county ? ` — ${i.county} Co.` : ""}
              </option>
            ))}
          </select>
          <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}>
            {(["tanker", "air_attack", "helicopter", "other"] as const).map((r) =>
              chip(ROLE_STYLE[r].label, roleFilter.size === 0 || roleFilter.has(r), () => {
                setRoleFilter((cur) => {
                  const next = new Set(cur);
                  if (next.size === 0) {
                    // from "all" to "only this one off"
                    (["tanker", "air_attack", "helicopter", "other"] as const).forEach((x) => next.add(x));
                  }
                  if (next.has(r)) next.delete(r);
                  else next.add(r);
                  if (next.size === 4) next.clear(); // all on = no filter
                  return next;
                });
              }, ROLE_STYLE[r].color),
            )}
            {chip("FIRE OPS ONLY", fireOpsOnly, () => setFireOpsOnly((v) => !v), C.ember)}
            {chip(`MIL CA (${mil?.count ?? 0})`, showMil, () => setShowMil((v) => !v), C.violet)}
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginTop: 6 }}>
            {lastPollAt ? `airspace sampled ${age(lastPollAt)} ago` : "sampling…"} · ~5s refresh ·
            trails 15 min · airplanes.live (borrowed, never in the ledger)
          </div>
        </div>

        {/* fleet board */}
        <div style={{ padding: "8px 12px", borderBottom: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.faint, marginBottom: 4 }}>
            FLEET {focus ? `(${visible.length}${focus.fire_ops ? ` · ${focus.fire_ops} fire ops` : ""})` : ""}
          </div>
          {!visible.length && (
            <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, padding: "8px 0" }}>
              no aircraft in this airspace right now
            </div>
          )}
          {visible.map((a) => {
            const style = ROLE_STYLE[a.role] ?? ROLE_STYLE.other;
            const followed = a.hex === followHex;
            const stn = onStation.get(a.hex);
            return (
              <div
                key={a.hex}
                className="tr-row"
                onClick={() => selectAircraft(a)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "5px 6px",
                  borderRadius: 4,
                  cursor: "pointer",
                  background: followed ? C.panelUp : "transparent",
                  border: `1px solid ${followed ? style.color + "55" : "transparent"}`,
                }}
              >
                <span style={{ color: style.color, fontSize: 13, width: 16, textAlign: "center" }}>{style.glyph}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontFamily: font.mono, fontSize: 10.5, color: C.txt, fontWeight: 700 }}>
                    {a.flight ?? a.hex.toUpperCase()}
                    <span style={{ color: style.color, fontWeight: 400, fontSize: 8.5 }}> {style.label}</span>
                    {a.military && <span style={{ color: C.violet, fontSize: 8.5 }}> MIL</span>}
                  </div>
                  <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {a.descr ?? a.type ?? "type unknown"}
                  </div>
                </div>
                <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, textAlign: "right" }}>
                  <div>{a.alt_ft != null ? `${Math.round(a.alt_ft).toLocaleString()} ft` : "—"}</div>
                  <div>{a.gs != null ? `${Math.round(a.gs)} kt` : "—"}{stn != null ? ` · ${fmtDur(stn)} on stn` : ""}</div>
                </div>
                <span style={{ fontFamily: font.mono, fontSize: 8, color: followed ? style.color : C.faint }}>
                  {followed ? "FOLLOWING" : "follow"}
                </span>
              </div>
            );
          })}
          {photo && (
            <div style={{ marginTop: 6, border: `1px solid ${C.line}`, borderRadius: 6, overflow: "hidden" }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={photo.url} alt="aircraft" style={{ width: "100%", display: "block" }} />
              {photo.credit && (
                <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, padding: "3px 8px" }}>
                  photo © {photo.credit} · saved to unit
                </div>
              )}
            </div>
          )}
        </div>

        {/* tanker-base watch */}
        <div style={{ padding: "8px 12px", borderBottom: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.faint, marginBottom: 4 }}>
            BASE WATCH
            {bases?.last_scan && (
              <span style={{ fontSize: 8, letterSpacing: 0, color: C.faint }}>
                {" "}· scanned {bases.last_scan.base} {age(bases.last_scan.at)} ago
              </span>
            )}
          </div>
          {!bases?.sightings?.length && (
            <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>
              no fire aircraft seen at a base in the last hour ({bases?.note ?? "warming up"})
            </div>
          )}
          {(bases?.sightings ?? []).slice(0, 8).map((s) => (
            <div key={`${s.hex}-${s.base}`} style={{ display: "flex", gap: 7, alignItems: "center", padding: "3px 0", fontFamily: font.mono, fontSize: 9.5 }}>
              <span style={{ color: s.departed_at ? C.warn : C.ok }}>{s.departed_at ? "↗" : "⌂"}</span>
              <span style={{ color: C.txt, fontWeight: 700 }}>{s.flight ?? s.hex.toUpperCase()}</span>
              <span style={{ color: C.dim, flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {s.departed_at ? `left ${s.base}` : `at ${s.base}`} · {s.descr ?? s.type ?? ""}
              </span>
              <span style={{ color: C.faint, fontSize: 8 }}>{age(s.departed_at ?? s.last_seen)}</span>
            </div>
          ))}
        </div>

        {/* activity log */}
        <div style={{ padding: "8px 12px" }}>
          <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.faint, marginBottom: 4 }}>
            ACTIVITY LOG {log ? `(${log.count} sorties)` : ""}
          </div>
          {!log?.sessions?.length && (
            <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>
              no aviation recorded over this fire yet
            </div>
          )}
          {(log?.sessions ?? []).slice(0, 20).map((s, i) => {
            const style = ROLE_STYLE[s.role] ?? ROLE_STYLE.other;
            return (
              <div key={`${s.hex}-${s.arrived}-${i}`} style={{ display: "flex", gap: 7, alignItems: "center", padding: "3px 0", fontFamily: font.mono, fontSize: 9.5 }}>
                <span style={{ color: s.ongoing ? C.ok : C.faint, fontSize: 8 }}>{s.ongoing ? "●" : "○"}</span>
                <span style={{ color: style.color, width: 14, textAlign: "center" }}>{style.glyph}</span>
                <span style={{ color: C.txt, fontWeight: 700 }}>{s.flight ?? s.hex.toUpperCase()}</span>
                <span style={{ color: C.dim, flex: 1 }}>
                  {fmtStamp(s.arrived)} · {fmtDur(s.on_station_s)} on station{s.ongoing ? " — now" : ""}
                </span>
              </div>
            );
          })}
          {log && (
            <div style={{ fontFamily: font.mono, fontSize: 7.5, color: C.faint, marginTop: 4 }}>{log.note}</div>
          )}
        </div>
      </div>

      {/* ------------------------------------------------ right: the map */}
      <div style={{ flex: 1, position: "relative", isolation: "isolate" as const }}>
        <div ref={holder} style={{ position: "absolute", inset: 0, background: C.bg }} />
        {followHex && (
          <div
            onClick={() => setFollowHex(null)}
            style={{
              position: "absolute",
              top: 10,
              left: 54,
              zIndex: Z.mapControl,
              background: "#1b2128",
              border: `1px solid ${C.blue}66`,
              borderRadius: 4,
              color: C.blue,
              fontFamily: font.mono,
              fontSize: 9.5,
              padding: "4px 9px",
              cursor: "pointer",
              boxShadow: "0 1px 5px rgba(0,0,0,0.4)",
            }}
          >
            ◉ FOLLOWING {focus?.aircraft.find((a) => a.hex === followHex)?.flight ?? followHex.toUpperCase()} — click to release
          </div>
        )}
      </div>
    </div>
  );
}
