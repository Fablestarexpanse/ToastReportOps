"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchIncidents, fetchWatch } from "@/lib/api";
import { C, font, Z } from "@/lib/theme";
import { fmtStamp } from "@/lib/time";

/* Alerts: new fires, watch candidates, and — highest priority of all —
   evacuation changes. A new evac order on a tracked fire is the single most
   important event this system can notice; for a long time the bell fired on new
   fires but stayed silent on evacuations, which was exactly backwards.

   Evac state uses a PERSISTED per-incident baseline rather than the session
   "primed" flag the other alerts use, deliberately: an order issued while the
   app was closed must still alert on the next launch. Missing a fire that
   started overnight is a gap; missing an evacuation order is a failure. */

type Alert = {
  id: string;
  kind: "fire" | "watch" | "evac";
  title: string;
  detail: string;
  at: string;
  color?: string;       // dropdown accent; defaults by kind
  incidentId?: string; // fire/evac alerts: the incident to open
  watchKey?: string;    // watch alerts: the candidate on the WATCH board
};

const SEEN_KEY = "toast-alert-seen-v1";
const EVAC_KEY = "toast-alert-evac-v1"; // incidentId -> last known evac status
const POLL_MS = 30_000;
const MAX_LOG = 30;

const ACTIVE_EVAC = new Set(["order", "warning", "shelter_in_place"]);
const EVAC_TITLES: Record<string, string> = {
  order: "EVACUATION ORDER",
  warning: "EVACUATION WARNING",
  shelter_in_place: "SHELTER IN PLACE",
};

function loadEvacBaseline(): Record<string, string> | null {
  try {
    const raw = localStorage.getItem(EVAC_KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  return null; // first-ever run: baseline silently, never alert on history
}

function loadSeen(): { incidents: string[]; watch: string[] } {
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  return { incidents: [], watch: [] };
}

export default function AlertBell({
  onOpen,
}: {
  /** route a clicked alert: a fire opens its incident in OPS, a watch
   *  candidate opens the WATCH board */
  onOpen?: (target: { incidentId?: string; watchKey?: string }) => void;
}) {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [notifState, setNotifState] = useState<string>("default");
  const seen = useRef<{ incidents: Set<string>; watch: Set<string> } | null>(null);
  const evacSeen = useRef<Record<string, string> | null | undefined>(undefined);
  const primed = useRef(false);

  useEffect(() => {
    if ("Notification" in window) setNotifState(Notification.permission);
  }, []);

  const notify = useCallback((title: string, body: string, urgent = false) => {
    if ("Notification" in window && Notification.permission === "granted") {
      try {
        // requireInteraction keeps an evacuation notice on screen until the
        // operator dismisses it — an order must not fade away unseen
        new Notification(title, { body, silent: false, requireInteraction: urgent });
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
              incidentId: i.id,
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
              watchKey: c.key,
              title: `POSSIBLE NEW START: ${c.name}`,
              detail: `${c.source_count} independent sources — check the WATCH board`,
              at: now,
            });
          }
        }
      }
      // Evacuation transitions, against a PERSISTED baseline (see header note).
      if (evacSeen.current === undefined) evacSeen.current = loadEvacBaseline();
      const firstEvacRun = evacSeen.current === null;
      const evacMap: Record<string, string> = evacSeen.current ?? {};
      for (const i of incs) {
        const raw = i.evac_status;
        const cur = raw && (ACTIVE_EVAC.has(raw) || raw === "lifted") ? raw : "none";
        const prev = evacMap[i.id];
        evacMap[i.id] = cur;
        if (firstEvacRun) continue; // recording history, not announcing it
        const becameActive = ACTIVE_EVAC.has(cur) && cur !== (prev ?? "none");
        const wasActive = prev != null && ACTIVE_EVAC.has(prev);
        if (becameActive) {
          fresh.push({
            id: `evac-${i.id}-${cur}-${now}`,
            kind: "evac",
            incidentId: i.id,
            title: `${EVAC_TITLES[cur]}: ${i.canonical_name}`,
            detail:
              `${i.county ? i.county + " County · " : ""}` +
              (wasActive && prev !== cur
                ? `changed from ${prev.replace(/_/g, " ")} — `
                : "") +
              "official text in the incident panel — verify before repeating",
            at: now,
          });
        } else if (cur === "lifted" && wasActive) {
          fresh.push({
            id: `evac-${i.id}-lifted-${now}`,
            kind: "evac",
            color: C.ok,
            incidentId: i.id,
            title: `EVACUATION LIFTED: ${i.canonical_name}`,
            detail: `${i.county ? i.county + " County · " : ""}was under ${prev.replace(/_/g, " ")}`,
            at: now,
          });
        }
      }
      evacSeen.current = evacMap;
      localStorage.setItem(EVAC_KEY, JSON.stringify(evacMap));

      // first poll only records the baseline — never alert on history
      primed.current = true;
      localStorage.setItem(
        SEEN_KEY,
        JSON.stringify({ incidents: [...s.incidents], watch: [...s.watch] }),
      );
      if (fresh.length) {
        setAlerts((prev) => [...fresh, ...prev].slice(0, MAX_LOG));
        setUnread((u) => u + fresh.length);
        for (const a of fresh) notify(a.title, a.detail, a.kind === "evac" && a.color !== C.ok);
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
          {alerts.map((a) => {
            const clickable = !!(a.incidentId || a.watchKey);
            return (
              <div
                key={a.id}
                onClick={
                  clickable
                    ? () => {
                        onOpen?.({ incidentId: a.incidentId, watchKey: a.watchKey });
                        setOpen(false);
                      }
                    : undefined
                }
                title={
                  clickable
                    ? a.kind === "fire"
                      ? "open this fire in OPS"
                      : "open the WATCH board"
                    : undefined
                }
                className={clickable ? "tr-row" : undefined}
                style={{ padding: "8px 12px", borderBottom: `1px solid ${C.line}44`, cursor: clickable ? "pointer" : "default" }}
              >
                <div style={{ fontSize: 11.5, fontWeight: 700, color: a.color ?? (a.kind === "evac" ? C.evac : a.kind === "fire" ? C.emberHot : C.violet) }}>
                  {a.title}
                  {clickable && <span style={{ float: "right", color: C.dim, fontSize: 10 }}>→</span>}
                </div>
                <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, marginTop: 2 }}>
                  {a.detail} · {fmtStamp(a.at)}
                </div>
              </div>
            );
          })}
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
