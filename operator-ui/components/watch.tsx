"use client";

import { useCallback, useEffect, useState } from "react";
import { WatchCandidate, fetchWatch, promoteCandidate, startResearch } from "@/lib/api";
import { C, TIER, font } from "@/lib/theme";

const fmtTime = (iso: string) => (iso ? iso.slice(11, 16) + "Z" : "—");

export default function WatchMode({ toast }: { toast: (m: string) => void }) {
  const [candidates, setCandidates] = useState<WatchCandidate[] | null>(null);
  const [promoting, setPromoting] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    try {
      setCandidates(await fetchWatch());
    } catch {
      setCandidates([]);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 8000);
    return () => clearInterval(id);
  }, [refresh]);

  const promote = async (c: WatchCandidate) => {
    setPromoting((s) => ({ ...s, [c.key]: true }));
    try {
      const r = await promoteCandidate(c.key);
      toast(`promoted ${r.name} → incident (${r.facts_attributed} facts attributed)`);
      refresh();
    } catch (e) {
      toast(`promote failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setPromoting((s) => ({ ...s, [c.key]: false }));
    }
  };

  const list = candidates ?? [];
  const corroborating = list.filter((c) => c.state === "corroborating").length;

  return (
    <div
      style={{
        flex: 1,
        overflowY: "auto",
        padding: 18,
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill,minmax(320px,1fr))",
        gap: 14,
        alignContent: "start",
      }}
    >
      <div style={{ gridColumn: "1 / -1", display: "flex", alignItems: "baseline", gap: 12 }}>
        <span style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>
          NEW STARTS BOARD
        </span>
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>
          fires named by independent sources but not yet on CAL FIRE&apos;s active list ·{" "}
          {list.length} watching · {corroborating} corroborating
        </span>
      </div>

      {list.map((c) => {
        const color = c.state === "corroborating" ? C.warn : C.violet;
        return (
          <div
            key={c.key}
            style={{
              background: C.panel,
              border: `1px solid ${color}44`,
              borderRadius: 8,
              padding: 14,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <span style={{ fontFamily: font.disp, fontSize: 15, fontWeight: 700 }}>{c.name}</span>
              <span style={{ fontFamily: font.mono, fontSize: 11, color }}>
                {Math.round(c.score * 100)}%
              </span>
            </div>
            <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, marginTop: 3 }}>
              {c.county ? `${c.county} · ` : ""}
              {c.source_count} source{c.source_count === 1 ? "" : "s"} · {c.fact_count} signals · last{" "}
              {fmtTime(c.latest_observed)}
            </div>

            {/* corroboration bar */}
            <div style={{ marginTop: 10, height: 5, borderRadius: 3, background: C.line, overflow: "hidden" }}>
              <div style={{ width: `${c.score * 100}%`, height: "100%", background: color }} />
            </div>
            <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 4 }}>
              {c.state === "corroborating"
                ? "multiple independent sources — worth a look"
                : "single source so far — watching for corroboration"}
            </div>

            {/* sample signals */}
            <div style={{ marginTop: 9 }}>
              {c.samples.map((s, i) => (
                <div key={i} style={{ display: "flex", gap: 7, padding: "3px 0", alignItems: "flex-start" }}>
                  <div style={{ width: 3, borderRadius: 2, background: TIER[s.tier], flexShrink: 0, alignSelf: "stretch" }} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 10.5, lineHeight: 1.35 }}>{s.content}</div>
                    <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint }}>
                      T{s.tier} · {fmtTime(s.observed_at)}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
              <button
                onClick={async () => {
                  try {
                    const q = `California wildfire "${c.name}"${c.county ? ` ${c.county}` : ""} — current status, location, size, containment, evacuations. Only report on this specific fire.`;
                    await startResearch(q);
                    toast(`deep research queued for ${c.name} — result lands in NEWSROOM`);
                  } catch (e) {
                    toast(`research failed: ${e instanceof Error ? e.message : e}`);
                  }
                }}
                style={{
                  flex: 1,
                  background: "transparent",
                  color: C.blue,
                  border: `1px solid ${C.blue}66`,
                  borderRadius: 5,
                  fontFamily: font.disp,
                  fontWeight: 700,
                  fontSize: 11,
                  letterSpacing: 1,
                  padding: "7px 0",
                  cursor: "pointer",
                }}
                title="agentic web research via Local Deep Research — cited dossier lands as an analysis draft"
              >
                ⌕ DEEP RESEARCH
              </button>
              <button
                onClick={() => promote(c)}
                disabled={promoting[c.key]}
                style={{
                  flex: 1,
                  background: color,
                  color: C.bg,
                  border: "none",
                  borderRadius: 5,
                  fontFamily: font.disp,
                  fontWeight: 700,
                  fontSize: 11,
                  letterSpacing: 1,
                  padding: "7px 0",
                  cursor: "pointer",
                }}
              >
                {promoting[c.key] ? "…" : "▲ PROMOTE"}
              </button>
            </div>
          </div>
        );
      })}

      {candidates && !list.length && (
        <div style={{ gridColumn: "1 / -1", padding: 16, fontFamily: font.mono, fontSize: 10, color: C.faint }}>
          no new-start candidates right now — the board fills when sources name a fire that isn&apos;t
          yet on the active list. As scanner/ADS-B/hotspot sources come online, their signals cluster
          here too.
        </div>
      )}
    </div>
  );
}
