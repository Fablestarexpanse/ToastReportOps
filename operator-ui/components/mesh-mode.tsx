"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  Incident,
  MeshMessages,
  MeshNode,
  MeshNodes,
  fetchIncidents,
  fetchMeshMessages,
  fetchMeshNodes,
  hasActiveEvac,
} from "@/lib/api";
import { escHtml } from "@/lib/html";
import { C, Z, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

/* MESH: the statewide California Meshtastic picture, heard via the public MQTT
   broker.

   Why it's here at all: LoRa mesh keeps moving traffic when PSPS takes the
   internet and cell towers with it — Paradise knows exactly why that matters.
   Honesty cuts both ways, and the banner says so: the MESH ITSELF needs no
   internet, but this VIEW of it (public broker) does. The operator's own nodes appear
   like any other the moment they join; a direct serial hookup later only adds
   packets the broker can't hear.

   Coverage is the whole state now — a node in San Diego is as worth hearing as
   one on Paradise ridge. The map overlays the live fire picture so the operator
   can see which nodes sit near an active fire and monitor those specifically as
   fires come and go.

   Mesh text is community input: rendered inert, never treated as fact, never
   near the ledger. */

const POLL_MS = 15_000;
const NEAR_CHOICES = [10, 25, 50] as const;

function batteryColor(b: number | null): string {
  if (b == null) return C.faint;
  if (b > 100) return C.blue; // 101 = plugged in
  if (b > 50) return C.ok;
  if (b > 20) return C.warn;
  return C.emberHot;
}

// same order-of-priority the OPS map uses: people-at-risk first, then behavior
function fireColor(i: Incident): string {
  if (hasActiveEvac(i)) return C.evac;
  if (i.status === "contained" || i.status === "frozen") return C.dim;
  return (i.containment_pct ?? 0) >= 60 ? C.warn : C.emberHot;
}

const R_KM = 6371;
const toRad = (d: number) => (d * Math.PI) / 180;
function distKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const dLat = toRad(bLat - aLat);
  const dLon = toRad(bLon - aLon);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(aLat)) * Math.cos(toRad(bLat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R_KM * Math.asin(Math.sqrt(s));
}

const evacTag = (i: Incident): string | null =>
  i.evac_status === "order"
    ? "EVAC ORDER"
    : i.evac_status === "warning"
      ? "EVAC WARNING"
      : i.evac_status === "shelter_in_place"
        ? "SHELTER"
        : null;

export default function MeshMode({ toast }: { toast: (m: string) => void }) {
  const [nodes, setNodes] = useState<MeshNodes | null>(null);
  const [messages, setMessages] = useState<MeshMessages | null>(null);
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [selNode, setSelNode] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [fireOnly, setFireOnly] = useState(false);
  const [showFires, setShowFires] = useState(true);
  const [nearKm, setNearKm] = useState<number>(25);
  const [focusFire, setFocusFire] = useState<string | null>(null);

  const holder = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const fireLayer = useRef<L.LayerGroup | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let live = true;
    const tick = () => {
      fetchMeshNodes().then((n) => live && setNodes(n)).catch(() => undefined);
      fetchMeshMessages({ search, fireOnly }).then((m) => live && setMessages(m)).catch(() => undefined);
      fetchIncidents().then((i) => live && setIncidents(i)).catch(() => undefined);
    };
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [search, fireOnly]);

  // active fires we can place on a map — the only ones a node can be "near"
  const activeFires = useMemo(
    () =>
      (incidents ?? [])
        .filter((i) => i.lat != null && i.lon != null && i.status !== "contained" && i.status !== "frozen")
        .sort((a, b) => {
          const ea = hasActiveEvac(a) ? 1 : 0;
          const eb = hasActiveEvac(b) ? 1 : 0;
          if (ea !== eb) return eb - ea;
          return (b.acreage ?? 0) - (a.acreage ?? 0);
        }),
    [incidents],
  );

  // for each active fire, the mesh nodes sitting inside the chosen radius
  const fireGroups = useMemo(() => {
    const nn = (nodes?.nodes ?? []).filter((n) => n.lat != null && n.lon != null);
    return activeFires.map((fire) => {
      const near = nn
        .map((n) => ({ n, km: distKm(fire.lat!, fire.lon!, n.lat!, n.lon!) }))
        .filter((x) => x.km <= nearKm)
        .sort((a, b) => a.km - b.km);
      return { fire, near };
    });
  }, [activeFires, nodes, nearKm]);

  // nearest active fire per node, so the all-nodes list can tag the ones on watch
  const nearInfo = useMemo(() => {
    const m = new Map<string, { fire: Incident; km: number }>();
    for (const g of fireGroups)
      for (const { n, km } of g.near) {
        const cur = m.get(n.node_id);
        if (!cur || km < cur.km) m.set(n.node_id, { fire: g.fire, km });
      }
    return m;
  }, [fireGroups]);

  const withNodes = fireGroups.filter((g) => g.near.length > 0);
  const coveredCount = fireGroups.length - withNodes.length;

  // monitor as fires come and go: when a node newly falls inside a fire's
  // radius, say so once — that is the actionable moment, not the steady state
  const prevPairs = useRef<Set<string> | null>(null);
  useEffect(() => {
    const keys = new Set<string>();
    for (const g of fireGroups) for (const { n } of g.near) keys.add(`${g.fire.id}|${n.node_id}`);
    if (prevPairs.current) {
      const fresh = [...keys].filter((k) => !prevPairs.current!.has(k));
      if (fresh.length === 1) {
        const [fid, nid] = fresh[0].split("|");
        const f = activeFires.find((x) => x.id === fid);
        const n = nodes?.nodes.find((x) => x.node_id === nid);
        if (f && n) toast(`mesh node ${n.long_name ?? n.short_name ?? nid} is now within ${nearKm}km of ${f.canonical_name}`);
      } else if (fresh.length > 1) {
        toast(`${fresh.length} mesh nodes are now near active fires`);
      }
    }
    prevPairs.current = keys;
  }, [fireGroups, activeFires, nodes, nearKm, toast]);

  useEffect(() => {
    if (map.current || !holder.current) return;
    const m = L.map(holder.current, {
      center: [37.3, -119.3], // statewide California
      zoom: 6,
      zoomControl: true,
      attributionControl: true,
      preferCanvas: true,
    });
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> · © CARTO',
    }).addTo(m);
    fireLayer.current = L.layerGroup().addTo(m); // fires under nodes
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    setReady(true);
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  // fire pins + the focused fire's radius ring
  useEffect(() => {
    const g = fireLayer.current;
    const m = map.current;
    if (!g || !m || !ready) return;
    g.clearLayers();
    if (!showFires) return;
    for (const f of activeFires) {
      const col = fireColor(f);
      const focused = f.id === focusFire;
      if (focused) {
        L.circle([f.lat!, f.lon!], {
          radius: nearKm * 1000,
          color: col,
          weight: 1,
          opacity: 0.5,
          fillColor: col,
          fillOpacity: 0.05,
          interactive: false,
        }).addTo(g);
      }
      const nearN = fireGroups.find((x) => x.fire.id === f.id)?.near.length ?? 0;
      const icon = L.divIcon({
        className: "",
        html: `<div style="color:${col};font-size:${focused ? 18 : 14}px;line-height:1;text-shadow:0 0 3px #000;font-weight:700">▲</div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      L.marker([f.lat!, f.lon!], { icon, interactive: true })
        .bindTooltip(
          `<b>${escHtml(f.canonical_name)}</b>` +
            `${evacTag(f) ? `<br/><span style="color:${C.evac}">${evacTag(f)}</span>` : ""}` +
            `${f.acreage != null ? `<br/>${Math.round(f.acreage).toLocaleString()} ac` : ""}` +
            `${f.containment_pct != null ? ` · ${f.containment_pct}% cont` : ""}` +
            `<br/>${nearN} mesh node${nearN === 1 ? "" : "s"} within ${nearKm}km`,
          { direction: "top", opacity: 0.95, offset: [0, -6] },
        )
        .on("click", () => focusOnFire(f))
        .addTo(g);
    }
  }, [activeFires, fireGroups, showFires, focusFire, nearKm, ready]);

  // mesh node markers
  useEffect(() => {
    const g = layer.current;
    const m = map.current;
    if (!g || !m || !ready) return;
    g.clearLayers();
    for (const n of nodes?.nodes ?? []) {
      if (n.lat == null || n.lon == null) continue;
      const sel = n.node_id === selNode;
      const near = nearInfo.get(n.node_id);
      const color = sel ? C.txt : near ? C.evac : "#7bd88f";
      const icon = L.divIcon({
        className: "",
        html: `<div style="color:${color};font-size:${sel ? 16 : 12}px;line-height:1;text-shadow:0 0 3px #000;font-weight:700">◈</div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      L.marker([n.lat, n.lon], { icon, interactive: true })
        .bindTooltip(
          `<b>${escHtml(n.long_name ?? n.node_id)}</b>${n.short_name ? ` (${escHtml(n.short_name)})` : ""}` +
            `${n.hw_model ? `<br/>${escHtml(n.hw_model)}` : ""}` +
            `${n.battery != null ? `<br/>battery ${n.battery > 100 ? "powered" : n.battery + "%"}` : ""}` +
            `${near ? `<br/><span style="color:${C.evac}">${near.km.toFixed(1)}km from ${escHtml(near.fire.canonical_name)}</span>` : ""}` +
            `<br/>heard ${age(n.last_heard)} ago`,
          { direction: "top", opacity: 0.95, offset: [0, -6] },
        )
        .on("click", () => setSelNode(n.node_id))
        .addTo(g);
    }
  }, [nodes, selNode, nearInfo, ready]);

  const flyTo = (nodeId: string) => {
    setSelNode(nodeId);
    const n = nodes?.nodes.find((x) => x.node_id === nodeId);
    if (n && n.lat != null && n.lon != null) map.current?.setView([n.lat, n.lon], 12);
  };

  const focusOnFire = (f: Incident) => {
    setFocusFire((cur) => (cur === f.id ? null : f.id));
    if (f.lat != null && f.lon != null) map.current?.setView([f.lat, f.lon], 11);
  };

  // a fire message pins to the sender's node — click it to fly there
  const flyToMessage = (m: { node_id: string; lat: number | null; lon: number | null }) => {
    if (m.lat != null && m.lon != null) {
      setSelNode(m.node_id);
      map.current?.setView([m.lat, m.lon], 12);
    } else {
      toast("this sender hasn't reported a position yet — no pin to fly to");
    }
  };

  const nodeName = (n: MeshNode) => n.long_name ?? n.short_name ?? n.node_id;

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      <div style={{ width: 400, minWidth: 400, borderRight: `1px solid ${C.line}`, display: "flex", flexDirection: "column", overflowY: "auto" }}>
        <div style={{ padding: "10px 12px", borderBottom: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.faint }}>
            CALIFORNIA MESH {nodes ? `· ${nodes.count} nodes / 24h` : ""}
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginTop: 5, lineHeight: 1.6 }}>
            statewide, heard via the public Meshtastic broker — the mesh itself needs no
            internet; this view of it does. Your nodes appear here the moment they join
            the mesh. Community traffic: signal, never fact.
          </div>
        </div>

        {/* ACTIVE FIRES + the nodes sitting near them — the monitor-nearby view */}
        <div style={{ padding: "8px 12px", borderBottom: `1px solid ${C.line}` }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
            <span style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.faint }}>
              ACTIVE FIRES {activeFires.length ? `· ${activeFires.length}` : ""}
            </span>
            <span
              onClick={() => setShowFires((v) => !v)}
              title="show or hide the fire pins on the mesh map"
              style={{
                marginLeft: "auto", fontFamily: font.mono, fontSize: 8, cursor: "pointer",
                padding: "1px 6px", borderRadius: 3,
                border: `1px solid ${showFires ? C.emberHot : C.line}`, color: showFires ? C.emberHot : C.dim,
              }}
            >
              ▲ PINS
            </span>
            {NEAR_CHOICES.map((km) => (
              <span
                key={km}
                onClick={() => setNearKm(km)}
                title="radius that counts a node as 'near' a fire"
                style={{
                  fontFamily: font.mono, fontSize: 8, cursor: "pointer",
                  padding: "1px 5px", borderRadius: 3,
                  border: `1px solid ${nearKm === km ? C.evac : C.line}`, color: nearKm === km ? C.evac : C.dim,
                }}
              >
                {km}km
              </span>
            ))}
          </div>

          {activeFires.length === 0 && (
            <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, lineHeight: 1.6 }}>
              no mapped active fires right now — the whole state is quiet on the ledger.
            </div>
          )}

          {withNodes.length === 0 && activeFires.length > 0 && (
            <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, lineHeight: 1.6 }}>
              no mesh nodes within {nearKm}km of any active fire yet. {activeFires.length} fire
              {activeFires.length === 1 ? "" : "s"} on the map — tap a pin or a fire below to look.
            </div>
          )}

          {withNodes.map(({ fire, near }) => {
            const focused = fire.id === focusFire;
            return (
              <div key={fire.id} style={{ marginBottom: 6 }}>
                <div
                  onClick={() => focusOnFire(fire)}
                  className="tr-row"
                  style={{
                    display: "flex", gap: 6, alignItems: "center", padding: "3px 6px",
                    borderRadius: 4, cursor: "pointer",
                    background: focused ? C.panelUp : "transparent",
                    fontFamily: font.mono, fontSize: 10,
                  }}
                >
                  <span style={{ color: fireColor(fire), fontSize: 11 }}>▲</span>
                  <span style={{ color: C.txt, flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {fire.canonical_name}
                  </span>
                  {evacTag(fire) && (
                    <span style={{ color: C.evac, fontSize: 7.5, border: `1px solid ${C.evac}66`, borderRadius: 3, padding: "0 4px" }}>
                      {evacTag(fire)}
                    </span>
                  )}
                  <span style={{ color: C.evac, fontSize: 8.5 }}>◈ {near.length}</span>
                </div>
                {near.map(({ n, km }) => (
                  <div
                    key={n.node_id}
                    onClick={() => flyTo(n.node_id)}
                    className="tr-row"
                    style={{
                      display: "flex", gap: 8, alignItems: "center", padding: "3px 6px 3px 20px",
                      borderRadius: 4, cursor: "pointer",
                      background: selNode === n.node_id ? C.panelUp : "transparent",
                      fontFamily: font.mono, fontSize: 9,
                    }}
                  >
                    <span style={{ color: C.evac, fontSize: 10 }}>◈</span>
                    <span style={{ color: C.txt, flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {nodeName(n)}
                    </span>
                    <span style={{ color: C.evac, fontSize: 8.5 }}>{km.toFixed(1)}km</span>
                    <span style={{ color: batteryColor(n.battery), fontSize: 8 }}>
                      {n.battery == null ? "—" : n.battery > 100 ? "⚡" : `${n.battery}%`}
                    </span>
                    <span style={{ color: C.faint, fontSize: 8 }}>{age(n.last_heard)}</span>
                  </div>
                ))}
              </div>
            );
          })}

          {coveredCount > 0 && withNodes.length > 0 && (
            <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginTop: 2 }}>
              + {coveredCount} more active fire{coveredCount === 1 ? "" : "s"} with no mesh node within {nearKm}km
            </div>
          )}
        </div>

        {nodes && nodes.count === 0 && (
          <div style={{ padding: "14px 12px", fontFamily: font.mono, fontSize: 9.5, color: C.dim, lineHeight: 1.7 }}>
            listening… nodes appear as the broker relays California traffic (a quiet mesh
            can take a while to show itself — position beacons are minutes apart).
          </div>
        )}

        <div style={{ padding: "6px 12px", borderBottom: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: font.disp, fontSize: 10, letterSpacing: 2, color: C.faint, marginBottom: 4 }}>
            ALL NODES
          </div>
          {(nodes?.nodes ?? []).slice(0, 80).map((n) => {
            const near = nearInfo.get(n.node_id);
            return (
              <div
                key={n.node_id}
                className="tr-row"
                onClick={() => flyTo(n.node_id)}
                style={{
                  display: "flex", gap: 8, alignItems: "center", padding: "4px 6px",
                  borderRadius: 4, cursor: "pointer",
                  background: selNode === n.node_id ? C.panelUp : "transparent",
                  fontFamily: font.mono, fontSize: 9.5,
                }}
              >
                <span style={{ color: near ? C.evac : "#7bd88f", fontSize: 11 }}>◈</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: C.txt, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {n.long_name ?? n.node_id}
                    {n.short_name && <span style={{ color: C.faint }}> {n.short_name}</span>}
                    {near && (
                      <span style={{ color: C.evac, fontSize: 8 }}> · 🔥 {near.km.toFixed(0)}km {near.fire.canonical_name}</span>
                    )}
                  </div>
                  <div style={{ color: C.faint, fontSize: 8 }}>
                    {n.hw_model ?? "unknown hw"}
                    {n.hops != null ? ` · ${n.hops} hop${n.hops === 1 ? "" : "s"}` : ""}
                    {n.snr != null ? ` · SNR ${n.snr}` : ""}
                  </div>
                </div>
                <div style={{ textAlign: "right", fontSize: 8.5 }}>
                  <div style={{ color: batteryColor(n.battery) }}>
                    {n.battery == null ? "—" : n.battery > 100 ? "⚡ pwr" : `${n.battery}%`}
                  </div>
                  <div style={{ color: C.faint, fontSize: 8 }}>{age(n.last_heard)}</div>
                </div>
              </div>
            );
          })}
        </div>

        <div style={{ padding: "8px 12px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 5 }}>
            <span style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.faint }}>
              MESH TRAFFIC {messages ? `(${messages.count})` : ""}
            </span>
            {messages && messages.fire_count > 0 && (
              <span
                onClick={() => setFireOnly((v) => !v)}
                title="the always-on scan escalated these to WATCH"
                style={{ fontFamily: font.mono, fontSize: 8, color: C.evac, cursor: "pointer" }}
              >
                🔥 {messages.fire_count} fire
              </span>
            )}
            <span
              onClick={() => setFireOnly((v) => !v)}
              style={{
                marginLeft: "auto", fontFamily: font.mono, fontSize: 8, cursor: "pointer",
                padding: "1px 6px", borderRadius: 3,
                border: `1px solid ${fireOnly ? C.evac : C.line}`, color: fireOnly ? C.evac : C.dim,
              }}
            >
              🔥 FIRE ONLY
            </span>
          </div>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="⌕ search mesh text…"
            style={{ width: "100%", background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.txt, fontFamily: font.mono, fontSize: 9, padding: "3px 7px", marginBottom: 6 }}
          />
          <div style={{ fontFamily: font.mono, fontSize: 7.5, color: C.faint, marginBottom: 5, lineHeight: 1.5 }}>
            every text is scanned for fire words — and a 🔥 emoji — on arrival; a hit
            becomes a T5 WATCH signal (the AI judges it there), pinned to the sender node.
          </div>
          {messages && messages.count === 0 && (
            <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>
              {fireOnly || search
                ? "nothing matches — clear the filter"
                : "no text heard yet; most mesh traffic is position beacons"}
            </div>
          )}
          {(messages?.messages ?? []).map((msg) => {
            const fire = msg.is_fire === 1;
            return (
              <div
                key={msg.id}
                onClick={() => fire && flyToMessage(msg)}
                style={{
                  padding: "4px 6px",
                  margin: "0 -4px",
                  fontFamily: font.mono,
                  fontSize: 9.5,
                  borderBottom: `1px solid ${C.line}33`,
                  borderLeft: fire ? `2px solid ${C.evac}` : "2px solid transparent",
                  background: fire ? C.evac + "10" : undefined,
                  cursor: fire ? "pointer" : "default",
                }}
                title={fire ? "fire mention — click to fly to the sender's node" : undefined}
              >
                <span style={{ color: fire ? C.evac : "#7bd88f" }}>
                  {msg.long_name ?? msg.short_name ?? msg.node_id}
                </span>
                <span style={{ color: C.faint, fontSize: 8 }}> · {fmtStamp(msg.rx_at)}</span>
                {fire && (
                  <span
                    title="fed the WATCH board as a T5 possible-new-start signal"
                    style={{ color: C.violet, marginLeft: 6, fontSize: 7.5, border: `1px solid ${C.violet}66`, borderRadius: 3, padding: "0 4px" }}
                  >
                    → WATCH
                  </span>
                )}
                <div style={{ color: fire ? C.txt : C.dim, marginTop: 1 }}>{msg.text}</div>
              </div>
            );
          })}
        </div>
      </div>

      <div style={{ flex: 1, position: "relative", isolation: "isolate" as const }}>
        <div ref={holder} style={{ position: "absolute", inset: 0, background: C.bg }} />
        <div
          style={{
            position: "absolute", top: 10, right: 10, zIndex: Z.mapControl,
            background: "#1b2128", border: `1px solid ${C.line}`, borderRadius: 4,
            color: C.faint, fontFamily: font.mono, fontSize: 8.5, padding: "5px 9px", lineHeight: 1.7,
          }}
        >
          <div>◈ mesh node · <span style={{ color: C.evac }}>◈ near a fire</span></div>
          <div><span style={{ color: C.emberHot }}>▲</span> active fire · <span style={{ color: C.evac }}>▲</span> under evacuation</div>
          <div style={{ color: C.faint }}>ring = {nearKm}km around the focused fire</div>
        </div>
      </div>
    </div>
  );
}
