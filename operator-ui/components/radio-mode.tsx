"use client";

import { useEffect, useRef, useState } from "react";
import { RadioCall, RadioCalls, fetchRadioCalls } from "@/lib/api";
import { C, font } from "@/lib/theme";
import { age, fmtStamp } from "@/lib/time";

/* RADIO: the operator's own scanner traffic.

   Dispatch beats the CAL FIRE website by 20-60 minutes; this tab is that edge.
   Until the SDR arrives it shows the setup path honestly instead of an empty
   table — the ingest contract is already live, so the first capture dropped
   into the inbox appears here with no further work. Transcript states are
   shown as they are: a transcript is never fabricated, and a fire-worded one
   carries a WATCH badge showing it fed the board as a T5 signal. */

const POLL_MS = 10_000;

function fmtDur(s: number | null): string {
  if (s == null) return "—";
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m${Math.round(s % 60)}s`;
}

export default function RadioMode({ toast }: { toast: (m: string) => void }) {
  const [data, setData] = useState<RadioCalls | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    let live = true;
    const tick = () => fetchRadioCalls().then((d) => live && setData(d)).catch(() => undefined);
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  const play = (call: RadioCall) => {
    if (playing === call.id) {
      audioRef.current?.pause();
      setPlaying(null);
      return;
    }
    if (audioRef.current) audioRef.current.pause();
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
      failed: [C.emberHot, "transcription failed — audio kept, retry from OPTIONS later"],
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
    <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
        <div style={{ fontFamily: font.disp, fontSize: 14, letterSpacing: 2, color: C.txt }}>
          SCANNER TRAFFIC
        </div>
        {data && (
          <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>
            {data.count} calls kept
            {data.pending_transcripts ? ` · ${data.pending_transcripts} awaiting transcription` : ""}
          </span>
        )}
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginBottom: 12 }}>
        own receiver · T5 community-grade signal — fire-worded transcripts feed WATCH, never drafts
        or evacuation displays
      </div>

      {data && data.count === 0 && (
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 8, padding: "16px 18px", maxWidth: 640, fontFamily: font.mono, fontSize: 10.5, color: C.dim, lineHeight: 1.75 }}>
          <div style={{ color: C.txt, fontFamily: font.disp, letterSpacing: 1, marginBottom: 8 }}>
            NO CAPTURES YET — THE PIPELINE IS LIVE AND WAITING ON HARDWARE
          </div>
          1. Order the receiver: an RTL-SDR Blog V4 dongle + a VHF antenna (~$40–60).
          <br />
          2. Follow <span style={{ color: C.blue }}>docs/RADIO-SETUP.md</span> — rtl_airband
          config for CAL FIRE VHF around Butte, frequencies verified on RadioReference.
          <br />
          3. Captures drop into <span style={{ color: C.blue }}>data/radio/inbox/</span> (audio +
          JSON sidecar); the 30-second sweep does the rest — CAS archive, this table, Whisper
          transcription when a local endpoint is configured.
          <br />
          <span style={{ color: C.faint }}>
            PSPS note: local RF needs no internet — this keeps working when the fiber does not.
          </span>
        </div>
      )}

      {(data?.calls ?? []).map((c) => (
        <div
          key={c.id}
          style={{
            display: "flex",
            gap: 10,
            alignItems: "flex-start",
            padding: "7px 9px",
            borderBottom: `1px solid ${C.line}`,
            fontFamily: font.mono,
            fontSize: 10,
          }}
        >
          <span
            onClick={() => play(c)}
            title={playing === c.id ? "stop" : "play the recording"}
            style={{ cursor: "pointer", color: playing === c.id ? C.ember : C.blue, fontSize: 13, width: 18 }}
          >
            {playing === c.id ? "◼" : "▶"}
          </span>
          <div style={{ width: 130, flexShrink: 0 }}>
            <div style={{ color: C.txt }}>{fmtStamp(c.started_at)}</div>
            <div style={{ color: C.faint, fontSize: 8.5 }}>{age(c.started_at)} ago · {fmtDur(c.duration_s)}</div>
          </div>
          <div style={{ width: 150, flexShrink: 0, color: C.warn, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {c.talkgroup ?? c.system ?? "unlabelled"}
            {c.freq_hz && (
              <div style={{ color: C.faint, fontSize: 8.5 }}>{(c.freq_hz / 1e6).toFixed(4)} MHz</div>
            )}
          </div>
          <div style={{ flex: 1, color: c.transcript ? C.dim : C.faint }}>
            {c.transcript ?? stateBadge(c)}
            {c.watch_fact_id && (
              <span
                title="this transmission fed the WATCH board as a T5 possible-new-start signal"
                style={{ color: C.violet, marginLeft: 8, fontSize: 8, border: `1px solid ${C.violet}66`, borderRadius: 3, padding: "0 4px" }}
              >
                → WATCH
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
