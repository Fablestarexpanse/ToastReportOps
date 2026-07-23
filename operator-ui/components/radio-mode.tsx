"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createRadioChannel,
  deleteRadioChannel,
  fetchRadioBoard,
  fetchRadioCalls,
  RadioBoard,
  RadioBoardCounty,
  RadioCall,
  RadioCalls,
  RadioChannel,
  updateRadioChannel,
} from "@/lib/api";
import {
  fetchOmzCalls,
  fetchOmzCaliforniaSystems,
  fetchOmzTalkgroups,
  looksFire,
  OmzCall,
  OmzSystem,
  OmzTalkgroup,
} from "@/lib/openmhz";
import { C, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

/* RADIO — the fire scanner.
 *
 * Three panes. LEFT: the county board — active-fire counties first (evac, then
 * acreage), each with its fires and its saved channels; arm an OpenMHz channel
 * or start a stream with one click, Broadcastify stays a link-out (operator
 * decision — their terms prohibit embedding; paste a stream URL onto any
 * channel to upgrade it). CENTER: the scanner deck — armed channels merge into
 * one live call feed, and SCAN auto-plays new traffic in arrival order like a
 * real scanner. RIGHT: the operator's own SDR captures with transcripts (the
 * archival, PSPS-proof spine — fire-worded transcripts feed WATCH as T5).
 */

const BOARD_POLL_MS = 30_000;
const CALLS_POLL_MS = 7_000;
const SDR_POLL_MS = 10_000;

function fmtDur(s: number | null): string {
  if (s == null) return "—";
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m${Math.round(s % 60)}s`;
}

const KIND_META: Record<RadioChannel["kind"], { label: string; color: string; hint: string }> = {
  openmhz: { label: "OpenMHz", color: C.ok, hint: "plays in-app, ~30 s behind live (open API)" },
  stream: { label: "STREAM", color: C.blue, hint: "live audio stream, plays in-app" },
  link: { label: "LINK", color: C.dim, hint: "opens the listen page in your browser" },
};

const evacColor = (s: string | null) =>
  s === "order" ? C.evac : s === "warning" ? C.warn : s === "shelter_in_place" ? C.blue : C.faint;

/* ---------------- add-channel modal ---------------- */

function AddChannel({
  counties,
  presetCounty,
  onClose,
  onSaved,
  toast,
}: {
  counties: string[];
  presetCounty: string | null;
  onClose: () => void;
  onSaved: () => void;
  toast: (m: string) => void;
}) {
  const [kind, setKind] = useState<RadioChannel["kind"]>("openmhz");
  const [name, setName] = useState("");
  const [county, setCounty] = useState(presetCounty ?? "");
  const [url, setUrl] = useState("");
  const [systems, setSystems] = useState<OmzSystem[] | null>(null);
  const [system, setSystem] = useState("");
  const [tgs, setTgs] = useState<OmzTalkgroup[] | null>(null);
  const [tgFilter, setTgFilter] = useState("");
  const [fireOnly, setFireOnly] = useState(true);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (kind !== "openmhz" || systems) return;
    fetchOmzCaliforniaSystems()
      .then(setSystems)
      .catch(() => toast("could not reach OpenMHz — check your connection"));
  }, [kind, systems, toast]);

  useEffect(() => {
    if (!system) return;
    setTgs(null);
    setPicked(new Set());
    fetchOmzTalkgroups(system)
      .then(setTgs)
      .catch(() => toast("could not load talkgroups for that system"));
  }, [system, toast]);

  const shownTgs = useMemo(() => {
    if (!tgs) return [];
    const f = tgFilter.trim().toUpperCase();
    return tgs.filter(
      (t) =>
        (!fireOnly || looksFire(t)) &&
        (!f || `${t.alpha} ${t.description}`.toUpperCase().includes(f)),
    );
  }, [tgs, tgFilter, fireOnly]);

  const save = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await createRadioChannel({
        name:
          name.trim() ||
          (kind === "openmhz"
            ? `${systems?.find((s) => s.shortName === system)?.name ?? system}${picked.size ? " — fire" : ""}`
            : "channel"),
        kind,
        county: county.trim() || null,
        system: kind === "openmhz" ? system : null,
        talkgroups: kind === "openmhz" ? [...picked] : [],
        url: kind !== "openmhz" ? url.trim() : null,
      });
      toast("channel saved");
      onSaved();
      onClose();
    } catch (e) {
      toast(`save failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const inp: React.CSSProperties = {
    background: C.bg,
    color: C.txt,
    border: `1px solid ${C.line}`,
    borderRadius: 4,
    fontFamily: font.mono,
    fontSize: 10,
    padding: "4px 6px",
    width: "100%",
  };

  return (
    <div
      onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "#000a", zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center" }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: 520, maxHeight: "82vh", overflowY: "auto", background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 16 }}
      >
        <div style={{ fontFamily: font.disp, fontSize: 13, letterSpacing: 2, color: C.txt, marginBottom: 10 }}>
          ADD CHANNEL
        </div>
        <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
          {(Object.keys(KIND_META) as RadioChannel["kind"][]).map((k) => (
            <span
              key={k}
              onClick={() => setKind(k)}
              title={KIND_META[k].hint}
              style={{
                cursor: "pointer",
                fontFamily: font.mono,
                fontSize: 9,
                padding: "3px 8px",
                borderRadius: 4,
                border: `1px solid ${kind === k ? KIND_META[k].color : C.line}`,
                color: kind === k ? KIND_META[k].color : C.dim,
              }}
            >
              {KIND_META[k].label.toUpperCase()}
            </span>
          ))}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8, fontFamily: font.mono, fontSize: 9, color: C.dim }}>
          <label>
            NAME
            <input style={inp} value={name} onChange={(e) => setName(e.target.value)} placeholder="auto from system if blank" />
          </label>
          <label>
            COUNTY (blank = statewide)
            <input style={inp} list="tr-counties" value={county} onChange={(e) => setCounty(e.target.value)} />
            <datalist id="tr-counties">
              {counties.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </label>

          {kind !== "openmhz" && (
            <label>
              {kind === "stream" ? "STREAM URL (direct audio)" : "LISTEN PAGE URL"}
              <input style={inp} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
            </label>
          )}

          {kind === "openmhz" && (
            <>
              <label>
                SYSTEM (active CA systems on OpenMHz, busiest first)
                <select style={inp} value={system} onChange={(e) => setSystem(e.target.value)}>
                  <option value="">{systems ? "pick a system…" : "loading…"}</option>
                  {(systems ?? []).map((s) => (
                    <option key={s.shortName} value={s.shortName}>
                      {s.name}
                      {s.county ? ` (${s.county})` : ""}
                    </option>
                  ))}
                </select>
              </label>
              {system && (
                <div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                    <span>TALKGROUPS ({picked.size ? `${picked.size} picked` : "none = whole system"})</span>
                    <label style={{ marginLeft: "auto", cursor: "pointer" }}>
                      <input type="checkbox" checked={fireOnly} onChange={(e) => setFireOnly(e.target.checked)} /> fire-related only
                    </label>
                  </div>
                  <input style={inp} value={tgFilter} onChange={(e) => setTgFilter(e.target.value)} placeholder="filter (e.g. DISPATCH, TAC)…" />
                  <div style={{ maxHeight: 180, overflowY: "auto", border: `1px solid ${C.line}`, borderRadius: 4, marginTop: 4, padding: 4 }}>
                    {!tgs && <div style={{ color: C.faint }}>loading talkgroups…</div>}
                    {tgs && !shownTgs.length && <div style={{ color: C.faint }}>nothing matches</div>}
                    {shownTgs.slice(0, 200).map((t) => (
                      <label key={t.num} style={{ display: "flex", gap: 6, alignItems: "baseline", cursor: "pointer", padding: "1px 2px" }}>
                        <input
                          type="checkbox"
                          checked={picked.has(t.num)}
                          onChange={(e) => {
                            const next = new Set(picked);
                            if (e.target.checked) next.add(t.num);
                            else next.delete(t.num);
                            setPicked(next);
                          }}
                        />
                        <span style={{ color: C.txt }}>{t.alpha}</span>
                        <span style={{ color: C.faint, fontSize: 8 }}>{t.description}</span>
                      </label>
                    ))}
                  </div>
                  {shownTgs.length > 0 && (
                    <span
                      onClick={() => setPicked(new Set(shownTgs.map((t) => t.num)))}
                      style={{ cursor: "pointer", color: C.blue, fontSize: 8.5 }}
                    >
                      select all shown ({shownTgs.length})
                    </span>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 14, justifyContent: "flex-end" }}>
          <span onClick={onClose} style={{ cursor: "pointer", fontFamily: font.mono, fontSize: 10, color: C.dim, padding: "4px 10px" }}>
            cancel
          </span>
          <span
            onClick={save}
            style={{
              cursor: "pointer",
              fontFamily: font.mono,
              fontSize: 10,
              fontWeight: 700,
              color: C.ok,
              border: `1px solid ${C.ok}66`,
              borderRadius: 4,
              padding: "4px 12px",
              opacity: busy ? 0.5 : 1,
            }}
          >
            {busy ? "saving…" : "SAVE"}
          </span>
        </div>
      </div>
    </div>
  );
}

/* ---------------- the mode ---------------- */

export default function RadioMode({ toast }: { toast: (m: string) => void }) {
  const [board, setBoard] = useState<RadioBoard | null>(null);
  const [sdr, setSdr] = useState<RadioCalls | null>(null);
  const [addFor, setAddFor] = useState<string | null | false>(false); // false=closed, null=statewide, string=county
  const [refreshKey, setRefreshKey] = useState(0);

  // scanner state: armed openmhz channels + live streams + the call feed
  const [armed, setArmed] = useState<Record<string, RadioChannel>>({});
  const [streams, setStreams] = useState<Record<string, RadioChannel>>({});
  const [liveCalls, setLiveCalls] = useState<OmzCall[]>([]);
  const [tgNames, setTgNames] = useState<Record<string, Record<number, string>>>({});
  const [scan, setScan] = useState(true);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const playedRef = useRef<Set<string>>(new Set());
  const armedAtRef = useRef<number>(0);

  useEffect(() => {
    let live = true;
    const tick = () => fetchRadioBoard().then((b) => live && setBoard(b)).catch(() => {});
    tick();
    const t = setInterval(tick, BOARD_POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [refreshKey]);

  useEffect(() => {
    let live = true;
    const tick = () => fetchRadioCalls().then((d) => live && setSdr(d)).catch(() => {});
    tick();
    const t = setInterval(tick, SDR_POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  // merged live-call feed from every armed channel
  useEffect(() => {
    const chans = Object.values(armed);
    if (!chans.length) {
      setLiveCalls([]);
      return;
    }
    let live = true;
    const tick = async () => {
      const results = await Promise.allSettled(
        chans.map((c) => fetchOmzCalls(c.system!, c.talkgroups)),
      );
      if (!live) return;
      const merged = new Map<string, OmzCall>();
      for (const r of results) {
        if (r.status === "fulfilled") for (const call of r.value) merged.set(call.id, call);
      }
      setLiveCalls([...merged.values()].sort((a, b) => b.time.localeCompare(a.time)).slice(0, 120));
    };
    tick();
    const t = setInterval(tick, CALLS_POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [armed]);

  const playCall = useCallback(
    (call: OmzCall, fromScan = false) => {
      audioRef.current?.pause();
      const el = new Audio(call.url);
      el.onended = () => setPlayingId(null);
      el.onerror = () => {
        setPlayingId(null);
        if (!fromScan) toast("could not play that call");
      };
      audioRef.current = el;
      playedRef.current.add(call.id);
      el.play().catch(() => setPlayingId(null));
      setPlayingId(call.id);
    },
    [toast],
  );

  const stopAudio = useCallback(() => {
    audioRef.current?.pause();
    setPlayingId(null);
  }, []);

  // SCAN: when idle, play the oldest unheard call that arrived after arming —
  // a real scanner plays traffic in the order it happened
  const queue = useMemo(() => {
    if (!scan) return [];
    return liveCalls
      .filter(
        (c) =>
          !playedRef.current.has(c.id) &&
          new Date(c.time).getTime() > armedAtRef.current,
      )
      .sort((a, b) => a.time.localeCompare(b.time));
  }, [liveCalls, scan]);

  useEffect(() => {
    if (scan && !playingId && queue.length) playCall(queue[0], true);
  }, [scan, playingId, queue, playCall]);

  const toggleArm = (ch: RadioChannel) => {
    setArmed((prev) => {
      const next = { ...prev };
      if (next[ch.id]) delete next[ch.id];
      else {
        if (!Object.keys(prev).length) armedAtRef.current = Date.now();
        next[ch.id] = ch;
        // learn talkgroup names for labels, once per system
        if (ch.system && !tgNames[ch.system]) {
          fetchOmzTalkgroups(ch.system)
            .then((tgs) =>
              setTgNames((m) => ({
                ...m,
                [ch.system!]: Object.fromEntries(tgs.map((t) => [t.num, t.alpha])),
              })),
            )
            .catch(() => {});
        }
      }
      return next;
    });
  };

  const toggleStream = (ch: RadioChannel) => {
    setStreams((prev) => {
      const next = { ...prev };
      if (next[ch.id]) delete next[ch.id];
      else next[ch.id] = ch;
      return next;
    });
  };

  const upgradeToStream = async (ch: RadioChannel) => {
    const url = window.prompt(
      `Paste a direct audio stream URL for "${ch.name}" (e.g. from your own Broadcastify Premium account). It will play in-app from then on.`,
      ch.url ?? "",
    );
    if (!url) return;
    try {
      await updateRadioChannel(ch.id, { kind: "stream", url });
      setRefreshKey((k) => k + 1);
      toast("upgraded to in-app stream");
    } catch (e) {
      toast(`update failed: ${e instanceof Error ? e.message : e}`);
    }
  };

  const removeChannel = async (ch: RadioChannel) => {
    try {
      await deleteRadioChannel(ch.id);
      setArmed((p) => {
        const n = { ...p };
        delete n[ch.id];
        return n;
      });
      setStreams((p) => {
        const n = { ...p };
        delete n[ch.id];
        return n;
      });
      setRefreshKey((k) => k + 1);
      toast("channel removed");
    } catch (e) {
      toast(`delete failed: ${e instanceof Error ? e.message : e}`);
    }
  };

  const tgLabel = (c: OmzCall) => tgNames[c.system]?.[c.tg] ?? `TG ${c.tg}`;
  const armedList = Object.values(armed);
  const streamList = Object.values(streams);
  const countyNames = useMemo(
    () => (board ? board.counties.map((c) => c.county) : []),
    [board],
  );

  const channelRow = (ch: RadioChannel) => {
    const meta = KIND_META[ch.kind];
    const isArmed = !!armed[ch.id];
    const isOn = !!streams[ch.id];
    return (
      <div key={ch.id} style={{ display: "flex", gap: 6, alignItems: "center", padding: "3px 0", fontFamily: font.mono, fontSize: 9.5 }}>
        <span title={meta.hint} style={{ color: meta.color, fontSize: 7.5, border: `1px solid ${meta.color}55`, borderRadius: 3, padding: "0 3px", flexShrink: 0 }}>
          {meta.label}
        </span>
        <span style={{ color: C.txt, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }} title={ch.notes ?? ch.name}>
          {ch.name}
        </span>
        {ch.kind === "openmhz" && (
          <span
            onClick={() => toggleArm(ch)}
            title={isArmed ? "disarm — drop from the scanner" : "arm — merge this channel into the live scanner"}
            style={{ cursor: "pointer", fontWeight: 700, fontSize: 8.5, color: isArmed ? C.ember : C.ok, border: `1px solid ${(isArmed ? C.ember : C.ok) + "66"}`, borderRadius: 3, padding: "0 5px" }}
          >
            {isArmed ? "◼ DISARM" : "⏻ ARM"}
          </span>
        )}
        {ch.kind === "stream" && (
          <span
            onClick={() => toggleStream(ch)}
            title={isOn ? "stop this stream" : "listen live in-app"}
            style={{ cursor: "pointer", fontWeight: 700, fontSize: 8.5, color: isOn ? C.ember : C.blue, border: `1px solid ${(isOn ? C.ember : C.blue) + "66"}`, borderRadius: 3, padding: "0 5px" }}
          >
            {isOn ? "◼ STOP" : "▶ LISTEN"}
          </span>
        )}
        {ch.kind === "link" && ch.url && (
          <>
            <a href={ch.url} target="_blank" rel="noopener noreferrer" title="open the listen page" style={{ color: C.blue, fontSize: 8.5, textDecoration: "none", fontWeight: 700 }}>
              ↗ OPEN
            </a>
            <span onClick={() => upgradeToStream(ch)} title="paste a direct stream URL to make this play in-app" style={{ cursor: "pointer", color: C.faint, fontSize: 8.5 }}>
              ⤒
            </span>
          </>
        )}
        <span onClick={() => removeChannel(ch)} title="remove channel" style={{ cursor: "pointer", color: C.faint, fontSize: 9 }}>
          ✕
        </span>
      </div>
    );
  };

  const countyCard = (c: RadioBoardCounty) => (
    <div
      key={c.county}
      style={{
        border: `1px solid ${c.evac ? C.evac + "55" : C.line}`,
        borderLeft: `3px solid ${c.evac ? C.evac : c.fires.length ? C.ember : C.line}`,
        borderRadius: 6,
        padding: "7px 9px",
        marginBottom: 8,
        background: C.panel,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
        <span style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 1, color: C.txt }}>
          {c.county.toUpperCase()}
        </span>
        <span style={{ fontFamily: font.mono, fontSize: 8, color: C.faint }}>
          {c.fires.length ? `${c.fires.length} active fire${c.fires.length === 1 ? "" : "s"}` : "quiet"}
        </span>
        <a
          href={c.broadcastify_search}
          target="_blank"
          rel="noopener noreferrer"
          title="find this county's feeds on Broadcastify (opens in browser)"
          style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 8, color: C.dim, textDecoration: "none" }}
        >
          BCFY ↗
        </a>
        <span
          onClick={() => setAddFor(c.county)}
          title="add a channel for this county"
          style={{ cursor: "pointer", fontFamily: font.mono, fontSize: 9, color: C.ok }}
        >
          ＋
        </span>
      </div>
      {c.fires.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, margin: "4px 0 2px" }}>
          {c.fires.map((f) => (
            <span
              key={f.id}
              title={f.acreage != null ? `${Math.round(f.acreage).toLocaleString()} acres` : undefined}
              style={{
                fontFamily: font.mono,
                fontSize: 8,
                color: evacColor(f.evac_status),
                border: `1px solid ${evacColor(f.evac_status)}44`,
                borderRadius: 3,
                padding: "0 4px",
              }}
            >
              {f.name}
              {f.evac_status ? ` · ${f.evac_status.replace(/_/g, " ")}` : ""}
            </span>
          ))}
        </div>
      )}
      {c.channels.length ? (
        c.channels.map(channelRow)
      ) : (
        <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 3 }}>
          no saved channels — ＋ to add, BCFY to find feeds
        </div>
      )}
    </div>
  );

  return (
    <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "330px 1fr 350px", gap: 0 }}>
      {/* ---------- LEFT: county board ---------- */}
      <div style={{ overflowY: "auto", padding: "12px 12px", borderRight: `1px solid ${C.line}` }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
          <span style={{ fontFamily: font.disp, fontSize: 13, letterSpacing: 2, color: C.ember }}>
            CHANNELS
          </span>
          <span
            onClick={() => setAddFor(null)}
            title="add a statewide channel"
            style={{ marginLeft: "auto", cursor: "pointer", fontFamily: font.mono, fontSize: 9, color: C.ok }}
          >
            ＋ ADD
          </span>
        </div>
        {!board && (
          <div style={{ fontFamily: font.mono, fontSize: 10, color: C.faint }}>loading board…</div>
        )}
        {board && board.statewide.length > 0 && (
          <div style={{ border: `1px solid ${C.line}`, borderRadius: 6, padding: "7px 9px", marginBottom: 8, background: C.panel }}>
            <div style={{ fontFamily: font.disp, fontSize: 11, letterSpacing: 1, color: C.dim, marginBottom: 3 }}>
              STATEWIDE
            </div>
            {board.statewide.map(channelRow)}
          </div>
        )}
        {board?.counties.map(countyCard)}
        {board && (
          <div style={{ fontFamily: font.mono, fontSize: 7.5, color: C.faint, marginTop: 6, lineHeight: 1.6 }}>
            {board.note}
          </div>
        )}
      </div>

      {/* ---------- CENTER: scanner deck ---------- */}
      <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: "12px 14px 8px", borderBottom: `1px solid ${C.line}` }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontFamily: font.disp, fontSize: 13, letterSpacing: 2, color: C.txt }}>
              SCANNER
            </span>
            <span
              onClick={() => setScan((s) => !s)}
              title="SCAN plays each new call as it arrives, oldest first — like a real scanner"
              style={{
                cursor: "pointer",
                fontFamily: font.mono,
                fontSize: 9,
                fontWeight: 700,
                color: scan ? C.ok : C.dim,
                border: `1px solid ${scan ? C.ok : C.line}`,
                borderRadius: 4,
                padding: "2px 8px",
              }}
            >
              {scan ? "● SCAN ON" : "○ SCAN OFF"}
            </span>
            {playingId && (
              <>
                <span className="tr-blink" style={{ color: C.ember, fontSize: 10 }}>●</span>
                <span style={{ fontFamily: font.mono, fontSize: 9, color: C.ember }}>
                  {(() => {
                    const c = liveCalls.find((x) => x.id === playingId);
                    return c ? `${tgLabel(c)} · ${fmtDur(c.len)}` : "playing…";
                  })()}
                </span>
                <span onClick={stopAudio} style={{ cursor: "pointer", fontFamily: font.mono, fontSize: 9, color: C.dim }}>
                  ◼ stop
                </span>
              </>
            )}
            <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 8, color: C.faint }}>
              {armedList.length ? `${armedList.length} armed · ~30 s behind live` : "arm a channel to start"}
            </span>
          </div>
          {armedList.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
              {armedList.map((ch) => (
                <span
                  key={ch.id}
                  onClick={() => toggleArm(ch)}
                  title="click to disarm"
                  style={{ cursor: "pointer", fontFamily: font.mono, fontSize: 8.5, color: C.ok, border: `1px solid ${C.ok}55`, borderRadius: 3, padding: "1px 6px" }}
                >
                  ⏻ {ch.name}
                </span>
              ))}
            </div>
          )}
          {streamList.map((ch) => (
            <div key={ch.id} style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
              <span style={{ fontFamily: font.mono, fontSize: 9, color: C.blue, flexShrink: 0 }}>
                ▶ {ch.name}
              </span>
              <audio controls autoPlay src={ch.url ?? undefined} style={{ height: 26, flex: 1, maxWidth: 420 }} />
              <span onClick={() => toggleStream(ch)} style={{ cursor: "pointer", color: C.faint, fontSize: 9 }}>✕</span>
            </div>
          ))}
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "6px 14px" }}>
          {!armedList.length && (
            <div style={{ fontFamily: font.mono, fontSize: 10, color: C.faint, lineHeight: 1.8, marginTop: 12 }}>
              The scanner is idle. ARM an OpenMHz channel on the left — county systems for
              fires near cities — or ▶ LISTEN to a stream channel. Armed channels merge into
              one live feed here; SCAN plays each new transmission as it lands.
            </div>
          )}
          {liveCalls.map((c) => (
            <div
              key={c.id}
              style={{
                display: "flex",
                gap: 8,
                alignItems: "baseline",
                padding: "4px 2px",
                borderBottom: `1px solid ${C.line}55`,
                fontFamily: font.mono,
                fontSize: 9.5,
                background: playingId === c.id ? C.ember + "14" : undefined,
              }}
            >
              <span
                onClick={() => (playingId === c.id ? stopAudio() : playCall(c))}
                style={{ cursor: "pointer", color: playingId === c.id ? C.ember : C.blue, fontSize: 11, width: 14 }}
              >
                {playingId === c.id ? "◼" : "▶"}
              </span>
              <span style={{ color: C.txt, width: 56 }}>{fmtStamp(c.time).slice(-8)}</span>
              <span style={{ color: C.faint, width: 46 }}>{age(c.time)}</span>
              <span style={{ color: C.warn, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {tgLabel(c)}
              </span>
              <span style={{ color: C.faint, width: 38, textAlign: "right" }}>{fmtDur(c.len)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ---------- RIGHT: own SDR archive ---------- */}
      <div style={{ overflowY: "auto", padding: "12px 12px", borderLeft: `1px solid ${C.line}` }}>
        <SdrArchive sdr={sdr} toast={toast} />
      </div>

      {addFor !== false && (
        <AddChannel
          counties={countyNames}
          presetCounty={addFor}
          onClose={() => setAddFor(false)}
          onSaved={() => setRefreshKey((k) => k + 1)}
          toast={toast}
        />
      )}
    </div>
  );
}

/* ---------------- own-SDR archive (the original panel, condensed) ---------------- */

function SdrArchive({ sdr, toast }: { sdr: RadioCalls | null; toast: (m: string) => void }) {
  const [playing, setPlaying] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const play = (call: RadioCall) => {
    if (playing === call.id) {
      audioRef.current?.pause();
      setPlaying(null);
      return;
    }
    audioRef.current?.pause();
    const el = new Audio(`/api/radio/calls/${call.id}/audio`);
    el.onended = () => setPlaying(null);
    el.onerror = () => {
      setPlaying(null);
      toast("could not play that recording");
    };
    audioRef.current = el;
    el.play().catch(() => toast("could not play that recording"));
    setPlaying(call.id);
  };

  const stateBadge = (c: RadioCall) => {
    if (c.transcript_state === "done" && c.transcript) return null;
    const map: Record<string, [string, string]> = {
      pending: [C.warn, "transcript pending — Whisper endpoint not configured or queued"],
      failed: [C.emberHot, "transcription failed — audio kept"],
      none: [C.faint, "transcription off"],
      done: [C.faint, "no speech recognized"],
    };
    const [color, title] = map[c.transcript_state] ?? map.none;
    return (
      <span title={title} style={{ color, fontSize: 8, border: `1px solid ${color}66`, borderRadius: 3, padding: "0 4px" }}>
        {c.transcript_state.toUpperCase()}
      </span>
    );
  };

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
        <span style={{ fontFamily: font.disp, fontSize: 13, letterSpacing: 2, color: C.txt }}>
          MY SDR
        </span>
        {sdr && (
          <span style={{ fontFamily: font.mono, fontSize: 8, color: C.dim }}>
            {sdr.count} kept
            {sdr.pending_transcripts ? ` · ${sdr.pending_transcripts} pending` : ""}
          </span>
        )}
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 7.5, color: C.faint, marginBottom: 8, lineHeight: 1.5 }}>
        own receiver — archived to the CAS, transcripts feed WATCH as T5. Works through a PSPS;
        no internet required.
      </div>

      {sdr && sdr.count === 0 && (
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 6, padding: "10px 12px", fontFamily: font.mono, fontSize: 9, color: C.dim, lineHeight: 1.7 }}>
          <div style={{ color: C.txt, marginBottom: 4 }}>NO CAPTURES YET — pipeline live, waiting on hardware</div>
          RTL-SDR V4 + VHF antenna (~$40–60), then docs/RADIO-SETUP.md. Captures dropped into
          data/radio/inbox/ appear here within 30 s.
        </div>
      )}

      {(sdr?.calls ?? []).map((c) => (
        <div key={c.id} style={{ display: "flex", gap: 7, alignItems: "flex-start", padding: "5px 2px", borderBottom: `1px solid ${C.line}55`, fontFamily: font.mono, fontSize: 9 }}>
          <span
            onClick={() => play(c)}
            style={{ cursor: "pointer", color: playing === c.id ? C.ember : C.blue, fontSize: 11, width: 14 }}
          >
            {playing === c.id ? "◼" : "▶"}
          </span>
          <div style={{ width: 92, flexShrink: 0 }}>
            <div style={{ color: C.txt, fontSize: 8.5 }}>{fmtStamp(c.started_at)}</div>
            <div style={{ color: C.faint, fontSize: 7.5 }}>{age(c.started_at)} · {fmtDur(c.duration_s)}</div>
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: C.warn, fontSize: 8.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {c.talkgroup ?? c.system ?? "unlabelled"}
              {c.freq_hz ? ` · ${(c.freq_hz / 1e6).toFixed(3)} MHz` : ""}
            </div>
            <div style={{ color: c.transcript ? C.dim : C.faint, fontSize: 9 }}>
              {c.transcript ?? stateBadge(c)}
              {c.watch_fact_id && (
                <span title="fed WATCH as a T5 possible-new-start signal" style={{ color: C.violet, marginLeft: 6, fontSize: 7.5, border: `1px solid ${C.violet}66`, borderRadius: 3, padding: "0 3px" }}>
                  → WATCH
                </span>
              )}
            </div>
          </div>
        </div>
      ))}
    </>
  );
}
