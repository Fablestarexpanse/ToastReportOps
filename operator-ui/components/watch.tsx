"use client";

import { useCallback, useEffect, useState } from "react";
import {
  WatchCandidate,
  WatchGroup,
  WatchState,
  classifyWatch,
  fetchWatch,
  promoteCandidate,
  startResearch,
  triageWatch,
} from "@/lib/api";
import { C, TIER, font } from "@/lib/theme";
import { fmtStamp } from "@/lib/time";

const fmtTime = fmtStamp;

const VERDICT_LABEL: Record<string, string> = {
  active_new: "BURNING NOW — likely a real new start",
  historical: "OLD FIRE — reporting refers to a past event",
  not_a_fire: "NOT A WILDFIRE INCIDENT",
  uncertain: "UNCERTAIN — not enough in the text to tell",
};

const VERDICT_COLOR: Record<string, string> = {
  active_new: C.emberHot,
  historical: C.dim,
  not_a_fire: C.faint,
  uncertain: C.warn,
};

/* Sections, in order. Only the first two ask for attention: the rest stay reachable
   because a wrong sort has to be something the operator can see and overrule. */
const SECTIONS: { group: WatchGroup; title: string; note: string; startsOpen: boolean }[] = [
  { group: "new", title: "NEW, UNCONFIRMED", note: "reported as a start, with no official record yet", startsOpen: true },
  { group: "official", title: "OFFICIALLY LISTED", note: "a CAL FIRE or NIFC record exists, but it is not tracked here", startsOpen: true },
  { group: "over", title: "ALREADY OVER", note: "CAL FIRE lists these as closed", startsOpen: false },
  { group: "prescribed", title: "PRESCRIBED", note: "planned burns, not wildfires", startsOpen: false },
  { group: "mentions", title: "MENTIONS, NOT STARTS", note: "a named fire in a story, with no report of it starting", startsOpen: false },
];

/** What the official lists say, in words. An unread list must never read as "no record". */
function officialLine(c: WatchCandidate): { text: string; color: string } {
  const o = c.official;
  const acres = o.acres != null ? `${o.acres.toLocaleString()} ac` : "size not listed";
  const contained = o.containment != null ? `, ${o.containment}% contained` : "";
  switch (o.status) {
    case "closed":
      return { text: `${o.source}: ${acres}${contained}${o.final ? ", final" : ""}`, color: C.dim };
    case "active":
      return { text: `${o.source}: listed active, ${acres}${contained}`, color: C.ok };
    case "reported":
      return { text: `${o.source}: ${acres}${contained}`, color: C.ok };
    case "none":
      return { text: "No official record", color: C.dim };
    default:
      return { text: "Official lists could not be checked", color: C.warn };
  }
}

export default function WatchMode({ toast }: { toast: (m: string) => void }) {
  const [candidates, setCandidates] = useState<WatchCandidate[] | null>(null);
  const [promoting, setPromoting] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [openOverride, setOpenOverride] = useState<Partial<Record<WatchGroup, boolean>>>({});

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

  const ageCheck = async (c: WatchCandidate) => {
    setBusy((s) => ({ ...s, [c.key]: "check" }));
    try {
      const v = await classifyWatch(c.key);
      toast(`${c.name}: ${VERDICT_LABEL[v.verdict]}`);
      refresh();
    } catch (e) {
      toast(`age check failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy((s) => ({ ...s, [c.key]: "" }));
    }
  };

  const triage = async (c: WatchCandidate, state: WatchState) => {
    // optimistic: the card disappears immediately, the board stays responsive
    setCandidates((prev) => (prev ?? []).filter((x) => x.key !== c.key));
    try {
      await triageWatch(c.key, state);
      toast(state === "archived" ? `${c.name} filed to archive` : `${c.name} ignored`);
    } catch (e) {
      toast(`failed: ${e instanceof Error ? e.message : e}`);
      refresh();
    }
  };

  const list = candidates ?? [];
  const inGroup = (g: WatchGroup) => list.filter((c) => c.group === g);

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
        <span style={{ fontFamily: font.mono, fontSize: 12, color: C.dim }}>
          fires named by sources, checked against CAL FIRE and NIFC · {inGroup("new").length} new ·{" "}
          {inGroup("official").length} officially listed
        </span>
      </div>

      {SECTIONS.flatMap(({ group, title, note, startsOpen }) => {
        const members = inGroup(group);
        if (!members.length) return [];
        const open = openOverride[group] ?? startsOpen;
        const header = (
          <button
            key={`section-${group}`}
            onClick={() => setOpenOverride((s) => ({ ...s, [group]: !open }))}
            aria-expanded={open}
            style={{ gridColumn: "1 / -1", display: "flex", alignItems: "baseline", gap: 10, background: "transparent", border: "none", borderTop: `1px solid ${C.line}`, padding: "10px 0 0", color: C.txt, cursor: "pointer", textAlign: "left" }}
          >
            <span style={{ fontFamily: font.mono, fontSize: 12, color: C.dim, width: 12 }}>{open ? "▾" : "▸"}</span>
            <span style={{ fontFamily: font.disp, fontSize: 14, fontWeight: 700, letterSpacing: 1.5 }}>
              {title} · {members.length}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 11, color: C.dim }}>{note}</span>
          </button>
        );
        if (!open) return [header];
        return [header, ...members.map((c) => {
        const color = c.state === "corroborating" ? C.warn : C.violet;
        const official = officialLine(c);
        const canPromote = c.group === "new" || c.group === "official";
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
            <div style={{ fontFamily: font.disp, fontSize: 15, fontWeight: 700 }}>{c.name}</div>
            <div style={{ fontFamily: font.mono, fontSize: 11, color: official.color, marginTop: 4 }}>
              {official.text}
            </div>
            <div style={{ fontFamily: font.mono, fontSize: 11, color: C.dim, marginTop: 3 }}>
              {c.county ? `${c.county} · ` : ""}
              {c.source_count} source{c.source_count === 1 ? "" : "s"} · {c.fact_count} signals · last{" "}
              {fmtTime(c.latest_observed)}
            </div>
            {c.geo_source === "named_feature" && (
              <div
                title="no source gave coordinates — this position is inferred from the fire's name and is approximate"
                style={{ fontFamily: font.mono, fontSize: 11, color: C.warn, marginTop: 2 }}
              >
                ≈ location inferred from “{c.geo_place}” — approximate
              </div>
            )}

            {/* sample signals */}
            <div style={{ marginTop: 9 }}>
              {c.samples.map((s, i) => (
                <div key={i} style={{ display: "flex", gap: 7, padding: "3px 0", alignItems: "flex-start" }}>
                  <div style={{ width: 3, borderRadius: 2, background: TIER[s.tier], flexShrink: 0, alignSelf: "stretch" }} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 12, lineHeight: 1.35 }}>{s.content}</div>
                    <div style={{ fontFamily: font.mono, fontSize: 11, color: C.dim }}>
                      T{s.tier} · {fmtTime(s.observed_at)}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {/* age check verdict */}
            {c.verdict && (
              <div
                style={{
                  marginTop: 9,
                  border: `1px solid ${VERDICT_COLOR[c.verdict.verdict]}55`,
                  background: VERDICT_COLOR[c.verdict.verdict] + "12",
                  borderRadius: 5,
                  padding: "6px 8px",
                }}
              >
                <div style={{ fontFamily: font.mono, fontSize: 11, color: VERDICT_COLOR[c.verdict.verdict], letterSpacing: 1 }}>
                  {VERDICT_LABEL[c.verdict.verdict]} · {Math.round(c.verdict.confidence * 100)}% confident
                  {c.verdict.in_california === false && " · OUTSIDE CA"}
                </div>
                <div style={{ fontSize: 12, color: C.dim, marginTop: 3, lineHeight: 1.35 }}>
                  {c.verdict.reasoning}
                </div>
                {c.verdict.evidence_quote && (
                  <div style={{ fontFamily: font.mono, fontSize: 11, color: C.dim, marginTop: 3, fontStyle: "italic" }}>
                    “{c.verdict.evidence_quote}”
                  </div>
                )}
                {c.verdict.suggested_action !== "keep" && (
                  <div
                    onClick={() => triage(c, c.verdict!.suggested_action as WatchState)}
                    style={{ marginTop: 5, fontFamily: font.mono, fontSize: 11, cursor: "pointer", color: C.ok, border: `1px solid ${C.ok}66`, borderRadius: 3, padding: "3px 7px", display: "inline-block" }}
                  >
                    ✓ ACCEPT — {c.verdict.suggested_action === "archived" ? "file to archive" : "ignore this"}
                  </div>
                )}
              </div>
            )}

            <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
              <button
                onClick={() => ageCheck(c)}
                disabled={busy[c.key] === "check"}
                title="ask the local model whether this is burning now or an old fire being referenced"
                style={{
                  flex: 1,
                  background: "transparent",
                  color: C.warn,
                  border: `1px solid ${C.warn}66`,
                  borderRadius: 5,
                  fontFamily: font.disp,
                  fontWeight: 700,
                  fontSize: 11,
                  letterSpacing: 1,
                  padding: "7px 0",
                  cursor: "pointer",
                }}
              >
                {busy[c.key] === "check" ? "CHECKING…" : "⏱ NEW OR OLD?"}
              </button>
              <button
                onClick={() => triage(c, "archived")}
                title="a real fire, but old — file it away"
                style={{
                  background: "transparent",
                  color: C.dim,
                  border: `1px solid ${C.line}`,
                  borderRadius: 5,
                  fontFamily: font.disp,
                  fontSize: 11,
                  letterSpacing: 1,
                  padding: "7px 10px",
                  cursor: "pointer",
                }}
              >
                🗄 ARCHIVE
              </button>
              <button
                onClick={() => triage(c, "ignored")}
                title="not a real new start — remove from the board"
                style={{
                  background: "transparent",
                  color: C.dim,
                  border: `1px solid ${C.line}`,
                  borderRadius: 5,
                  fontFamily: font.disp,
                  fontSize: 11,
                  padding: "7px 10px",
                  cursor: "pointer",
                }}
              >
                ✕
              </button>
            </div>

            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
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
              {canPromote && (
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
              )}
            </div>
          </div>
        );
        })];
      })}

      {candidates && !list.length && (
        <div style={{ gridColumn: "1 / -1", padding: 16, fontFamily: font.mono, fontSize: 12, color: C.dim }}>
          no new-start candidates right now — the board fills when sources name a fire that isn&apos;t
          yet on the active list. As scanner/ADS-B/hotspot sources come online, their signals cluster
          here too.
        </div>
      )}
    </div>
  );
}
