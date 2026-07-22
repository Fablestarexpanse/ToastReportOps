"use client";

import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MeshMessages, MeshNodes, fetchMeshMessages, fetchMeshNodes } from "@/lib/api";
import { escHtml } from "@/lib/html";
import { C, Z, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

/* MESH: the NorCal Meshtastic picture, heard via the public MQTT broker.

   Why it's here at all: LoRa mesh keeps moving traffic when PSPS takes the
   internet and cell towers with it — Paradise knows exactly why that matters.
   Honesty cuts both ways, and the banner says so: the MESH ITSELF needs no
   internet, but this VIEW of it (public broker) does. Brian's own nodes appear
   like any other the moment they join; a direct serial hookup later only adds
   packets the broker can't hear.

   Mesh text is community input: rendered inert, never treated as fact, never
   near the ledger. */

const POLL_MS = 15_000;

function batteryColor(b: number | null): string {
  if (b == null) return C.faint;
  if (b > 100) return C.blue; // 101 = plugged in
  if (b > 50) return C.ok;
  if (b > 20) return C.warn;
  return C.emberHot;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export default function MeshMode({ toast }: { toast: (m: string) => void }) {
  const [nodes, setNodes] = useState<MeshNodes | null>(null);
  const [messages, setMessages] = useState<MeshMessages | null>(null);
  const [selNode, setSelNode] = useState<string | null>(null);

  const holder = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let live = true;
    const tick = () => {
      fetchMeshNodes().then((n) => live && setNodes(n)).catch(() => undefined);
      fetchMeshMessages().then((m) => live && setMessages(m)).catch(() => undefined);
    };
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    if (map.current || !holder.current) return;
    const m = L.map(holder.current, {
      center: [39.9, -121.6],
      zoom: 8,
      zoomControl: true,
      attributionControl: true,
      preferCanvas: true,
    });
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> · © CARTO',
    }).addTo(m);
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    setReady(true);
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const g = layer.current;
    const m = map.current;
    if (!g || !m || !ready) return;
    g.clearLayers();
    for (const n of nodes?.nodes ?? []) {
      if (n.lat == null || n.lon == null) continue;
      const sel = n.node_id === selNode;
      const icon = L.divIcon({
        className: "",
        html: `<div style="color:${sel ? C.txt : "#7bd88f"};font-size:${sel ? 16 : 12}px;line-height:1;text-shadow:0 0 3px #000;font-weight:700">◈</div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      L.marker([n.lat, n.lon], { icon, interactive: true })
        .bindTooltip(
          `<b>${escHtml(n.long_name ?? n.node_id)}</b>${n.short_name ? ` (${escHtml(n.short_name)})` : ""}` +
            `${n.hw_model ? `<br/>${escHtml(n.hw_model)}` : ""}` +
            `${n.battery != null ? `<br/>battery ${n.battery > 100 ? "powered" : n.battery + "%"}` : ""}` +
            `<br/>heard ${age(n.last_heard)} ago`,
          { direction: "top", opacity: 0.95, offset: [0, -6] },
        )
        .on("click", () => setSelNode(n.node_id))
        .addTo(g);
    }
  }, [nodes, selNode, ready]);

  const flyTo = (nodeId: string) => {
    setSelNode(nodeId);
    const n = nodes?.nodes.find((x) => x.node_id === nodeId);
    if (n && n.lat != null && n.lon != null) map.current?.setView([n.lat, n.lon], 12);
  };

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      <div style={{ width: 400, minWidth: 400, borderRight: `1px solid ${C.line}`, display: "flex", flexDirection: "column", overflowY: "auto" }}>
        <div style={{ padding: "10px 12px", borderBottom: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.faint }}>
            NORCAL MESH {nodes ? `· ${nodes.count} nodes / 24h` : ""}
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, marginTop: 5, lineHeight: 1.6 }}>
            heard via the public Meshtastic broker — the mesh itself needs no internet;
            this view of it does. Your nodes appear here the moment they join the mesh.
            Community traffic: signal, never fact.
          </div>
        </div>

        {nodes && nodes.count === 0 && (
          <div style={{ padding: "14px 12px", fontFamily: font.mono, fontSize: 9.5, color: C.dim, lineHeight: 1.7 }}>
            listening… nodes appear as the broker relays NorCal traffic (a quiet mesh
            can take a while to show itself — position beacons are minutes apart).
          </div>
        )}

        <div style={{ padding: "6px 12px", borderBottom: `1px solid ${C.line}` }}>
          {(nodes?.nodes ?? []).slice(0, 60).map((n) => (
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
              <span style={{ color: "#7bd88f", fontSize: 11 }}>◈</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ color: C.txt, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {n.long_name ?? n.node_id}
                  {n.short_name && <span style={{ color: C.faint }}> {n.short_name}</span>}
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
          ))}
        </div>

        <div style={{ padding: "8px 12px" }}>
          <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.faint, marginBottom: 4 }}>
            MESH TRAFFIC {messages ? `(${messages.count})` : ""}
          </div>
          {messages && messages.count === 0 && (
            <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>
              no text heard yet — most mesh traffic is position beacons
            </div>
          )}
          {(messages?.messages ?? []).map((msg) => (
            <div key={msg.id} style={{ padding: "4px 0", fontFamily: font.mono, fontSize: 9.5, borderBottom: `1px solid ${C.line}33` }}>
              <span style={{ color: "#7bd88f" }}>{msg.long_name ?? msg.short_name ?? msg.node_id}</span>
              <span style={{ color: C.faint, fontSize: 8 }}> · {fmtStamp(msg.rx_at)}</span>
              <div style={{ color: C.dim, marginTop: 1 }}>{msg.text}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ flex: 1, position: "relative", isolation: "isolate" as const }}>
        <div ref={holder} style={{ position: "absolute", inset: 0, background: C.bg }} />
        <div
          style={{
            position: "absolute", top: 10, right: 10, zIndex: Z.mapControl,
            background: "#1b2128", border: `1px solid ${C.line}`, borderRadius: 4,
            color: C.faint, fontFamily: font.mono, fontSize: 8.5, padding: "4px 9px",
          }}
        >
          ◈ mesh node (position self-reported)
        </div>
      </div>
    </div>
  );
}
