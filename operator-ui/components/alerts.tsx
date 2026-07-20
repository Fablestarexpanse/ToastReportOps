"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchIncidents, fetchWatch } from "@/lib/api";
import { C, font, Z } from "@/lib/theme";
import { fmtStamp } from "@/lib/time";

/* New-fire alerts: watches /incidents and /watch; a newly-appearing active
   incident or a newly-corroborating watch candidate raises an alert — browser
   notification (works in the Edge app window) + the bell dropdown. Seen-state
   persists in localStorage so restarts don't re-alert history. */

type Alert = { id: string; kind: "fire" | "watch"; title: string; detail: string; at: string };

const SEEN_KEY = "toast-alert-seen-v1";
const POLL_MS = 30_000;
const MAX_LOG = 30;

function loadSeen(): { incidents: string[]; watch: string[] } {
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  return { incidents: [], watch: [] };
}

export default function AlertBell() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [notifState, setNotifState] = useState<string>("default");
  const seen = useRef<{ incidents: Set<string>; watch: Set<string> } | null>(null);
  const primed = useRef(false);

  useEffect(() => {
    if ("Notification" in window) setNotifState(Notification.permission);
  }, []);

  const notify = useCallback((title: string, body: string) => {
    if ("Notification" in window && Notification.permission === "granted") {
      try {
        new Notification(title, { body, silent: false });
      } catch {}
    }
  }, []);

  const tick = useCallback(async () => {
    try {
      const [incs, watch] = await Promise.all([fetchIncidents(), fetchWatch()]);
      if (!seen.current) {
        const stored = loadSeen();
        seen.current = { incidents: new Set(stored.incidents), watch: new Set(stored.watch) };
      }
      const s = seen.current;
      const fresh: Alert[] = [];
      const now = new Date().toISOString();

      for (const i of incs) {
        if (!s.incidents.has(i.id)) {
          s.incidents.add(i.id);
          if (primed.current && i.status === "active") {
            fresh.push({
              id: `inc-${i.id}`,
              kind: "fire",
              title: `NEW FIRE: ${i.canonical_name}`,
              detail: `${i.county ? i.county + " County · " : ""}${i.acreage != null ? i.acreage.toLocaleString() + " ac" : "size unknown"}`,
              at: now,
            });
          }
        }
      }
      for (const c of watch) {
        if (c.state === "corroborating" && !s.watch.has(c.key)) {
          s.watch.add(c.key);
          if (primed.current) {
            fresh.push({
              id: `watch-${c.key}`,
              kind: "watch",
              title: `POSSIBLE NEW START: ${c.name}`,
              detail: `${c.source_count} independent sources — check the WATCH board`,
              at: now,
            });
          }
        }
      }
      // first poll only records the baseline — never alert on history
      primed.current = true;
      localStorage.setItem(
        SEEN_KEY,
        JSON.stringify({ incidents: [...s.incidents], watch: [...s.watch] }),
      );
      if (fresh.length) {
        setAlerts((prev) => [...fresh, ...prev].slice(0, MAX_LOG));
        setUnread((u) => u + fresh.length);
        for (const a of fresh) notify(a.title, a.detail);
      }
    } catch {}
  }, [notify]);

  useEffect(() => {
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => clearInterval(id);
  }, [tick]);

  const requestPermission = async () => {
    if ("Notification" in window && Notification.permission === "default") {
      setNotifState(await Notification.requestPermission());
    }
  };

  const bellColor = unread > 0 ? C.emberHot : notifState === "granted" ? C.ok : C.dim;

  return (
    <div style={{ position: "relative" }}>
      <span
        onClick={() => {
          setOpen(!open);
          setUnread(0);
          requestPermission();
        }}
        title={
          notifState === "granted"
            ? "alerts armed — desktop notifications on new fires"
            : "click to enable desktop notifications for new fires"
        }
        style={{ cursor: "pointer", fontSize: 14, color: bellColor, padding: "0 2px", userSelect: "none" }}
      >
        🔔
        {unread > 0 && (
          <span style={{ fontFamily: font.mono, fontSize: 9, color: C.emberHot, marginLeft: 2 }}>
            {unread}
          </span>
        )}
      </span>
      {open && (
        <div
          style={{
            position: "absolute",
            right: 0,
            top: 24,
            width: 300,
            maxHeight: 320,
            overflowY: "auto",
            background: C.panelUp,
            border: `1px solid ${C.line}`,
            borderRadius: 7,
            boxShadow: "0 8px 30px #000c",
            zIndex: Z.dropdown, // must clear the map, whose panes reach 800
          }}
        >
          <div style={{ padding: "8px 12px", fontFamily: font.disp, fontSize: 11, letterSpacing: 2, color: C.dim, borderBottom: `1px solid ${C.line}` }}>
            ALERTS {notifState !== "granted" && "· notifications off — click bell to enable"}
          </div>
          {alerts.map((a) => (
            <div key={a.id} style={{ padding: "8px 12px", borderBottom: `1px solid ${C.line}44` }}>
              <div style={{ fontSize: 11.5, fontWeight: 700, color: a.kind === "fire" ? C.emberHot : C.violet }}>
                {a.title}
              </div>
              <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, marginTop: 2 }}>
                {a.detail} · {fmtStamp(a.at)}
              </div>
            </div>
          ))}
          {!alerts.length && (
            <div style={{ padding: 14, fontFamily: font.mono, fontSize: 9.5, color: C.faint }}>
              no alerts this session — new active fires and corroborating watch candidates land here
            </div>
          )}
        </div>
      )}
    </div>
  );
}
