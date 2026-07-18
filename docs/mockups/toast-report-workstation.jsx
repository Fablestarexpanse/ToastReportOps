import React, { useState, useEffect, useRef } from "react";

/* ============================================================
   TOAST REPORT v3 — Fire Intelligence Workstation (mockup)
   Modes: OPS (live map + intake) · NEWSROOM (AI drafts →
   edit/approve/publish, point-at-article, attach media) ·
   WATCH (new-starts board) · SOURCES (manage + add sources,
   incl. trusted YouTubers)
   ============================================================ */

const C = {
  bg: "#14181d", panel: "#1b2128", panelUp: "#222a33", line: "#2e3843",
  txt: "#d7dee6", dim: "#7c8894", faint: "#4a555f",
  ember: "#ff9a3c", emberHot: "#ff5c33", evac: "#ff4d8f",
  ok: "#3ddbb4", warn: "#ffd23c", blue: "#5cb8ff", violet: "#a78bfa",
};
const font = {
  mono: "'JetBrains Mono','SF Mono',Consolas,monospace",
  disp: "'Barlow Condensed','Arial Narrow',sans-serif",
  body: "'Barlow','Segoe UI',sans-serif",
};
const TIER = { T1: C.ok, T2: C.blue, T3: C.warn, T4: C.ember, T5: C.evac };

/* ---------------- shared bits ---------------- */
const Chip = ({ children, color = C.dim, border, onClick, active }) => (
  <span onClick={onClick} style={{ fontFamily: font.mono, fontSize: 9, padding: "2px 7px", borderRadius: 3, border: `1px solid ${border || (active ? color : C.line)}`, color: active ? color : C.dim, cursor: onClick ? "pointer" : "default", display: "inline-block" }}>{children}</span>
);

const intakePool = [
  { kind: "SCANNER", tier: "T3", src: "Butte Fire Net", txt: "\u201CSpot fire across Flume Rd, wind driven, requesting two engines\u201D", verdict: "EXTRACTED", inc: "FLUME", conf: 74 },
  { kind: "AIRCRAFT", tier: "T2", src: "ADS-B: T-912", txt: "Tanker 912 departed CIC, heading 095° — 4th tanker on FLUME", verdict: "EXTRACTED · resource +1", inc: "FLUME", conf: 92 },
  { kind: "ARTICLE", tier: "T4", src: "KRCR News", txt: "\u201CEvacuations expand as Flume Fire pushes northeast\u201D — 3 facts, 1 dup dropped", verdict: "EXTRACTED", inc: "FLUME", conf: 82 },
  { kind: "VIDEO", tier: "T4", src: "YT: NorCal Fire Watch", txt: "Live stream 14 min — transcript: \u201Cspotting a half mile ahead of the head\u201D", verdict: "EXTRACTED · 2 facts", inc: "FLUME", conf: 78 },
  { kind: "SOCIAL", tier: "T5", src: "X @buttescanner", txt: "\u201CHuge column visible from Chico rn\u201D + photo", verdict: "MEDIA→TRIAGE", inc: "FLUME?", conf: 41 },
  { kind: "SAT", tier: "T1", src: "GOES-18", txt: "New hotspot cluster 39.78,-121.56 — persistence 3/3", verdict: "EXTRACTED", inc: "FLUME", conf: 95 },
  { kind: "AIRCRAFT", tier: "T2", src: "ADS-B: AA-230", txt: "Air Attack orbiting new location 39.61,-121.44 — 9 min, no incident matched", verdict: "→ WATCH BOARD", inc: "NEW?", conf: 66 },
  { kind: "OFFICIAL", tier: "T1", src: "Zonehaven", txt: "Zone PAR-E032 WARNING → ORDER", verdict: "EXTRACTED · supersedes #114", inc: "FLUME", conf: 99 },
  { kind: "SOCIAL", tier: "T5", src: "FB Paradise Group", txt: "\u201CAnyone know if Pentz is closed?\u201D", verdict: "DROPPED · no facts", inc: "—", conf: 0 },
  { kind: "SCANNER", tier: "T3", src: "CAL FIRE TAC 8", txt: "\u201CStructure defense assignments Pentz corridor\u201D", verdict: "EXTRACTED", inc: "FLUME", conf: 76 },
];

/* ================= OPS MODE ================= */
function Pulse({ x, y, size = 10, color = C.emberHot }) {
  return (<g><circle cx={x} cy={y} r={size} fill={color} opacity="0.9" /><circle cx={x} cy={y} r={size} fill="none" stroke={color} strokeWidth="2" className="tr-pulse" /></g>);
}

function Aircraft({ x, y, rot, label, color = C.blue }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <g transform={`rotate(${rot})`}>
        <path d="M0 -7 L2 -1 L7 2 L2 3 L1 7 L0 5 L-1 7 L-2 3 L-7 2 L-2 -1 Z" fill={color} />
      </g>
      <text x="9" y="3" fill={color} fontSize="8.5" fontFamily={font.mono}>{label}</text>
    </g>
  );
}

function IntakeRow({ f, fresh, starred, onStar, onDraft }) {
  const vc = f.verdict.startsWith("EXTRACTED") ? C.ok : f.verdict.startsWith("→") ? C.violet : f.verdict.startsWith("MEDIA") ? C.violet : f.verdict.startsWith("DEDUP") ? C.faint : f.verdict.startsWith("DROP") ? C.evac : C.blue;
  return (
    <div className={fresh ? "tr-in tr-row" : "tr-row"} style={{ padding: "8px 10px", borderBottom: `1px solid ${C.line}55`, display: "flex", gap: 8, background: starred ? C.warn + "0d" : undefined }}>
      <div style={{ width: 3, borderRadius: 2, background: TIER[f.tier], flexShrink: 0 }} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, display: "flex", gap: 6 }}>
          <span style={{ color: TIER[f.tier] }}>{f.kind}</span><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.src}</span>
          {f.conf > 0 && <span style={{ marginLeft: "auto", color: f.conf > 80 ? C.ok : f.conf > 60 ? C.warn : C.evac }}>{f.conf}%</span>}
        </div>
        <div style={{ fontSize: 11.5, marginTop: 2, color: f.verdict.startsWith("DROP") ? C.faint : C.txt }}>{f.txt}</div>
        <div style={{ fontFamily: font.mono, fontSize: 9, marginTop: 3, color: vc, display: "flex", gap: 8 }}>
          <span>▸ {f.verdict}{f.inc !== "—" ? ` → ${f.inc}` : ""}</span>
          <span onClick={onDraft} style={{ marginLeft: "auto", color: C.faint, cursor: "pointer" }} title="Send to Newsroom as draft material">✎ draft</span>
        </div>
      </div>
      <button onClick={onStar} title="Star — pin to archive" style={{ background: "none", border: "none", cursor: "pointer", fontSize: 14, color: starred ? C.warn : C.faint, padding: "0 2px", alignSelf: "flex-start" }}>{starred ? "★" : "☆"}</button>
    </div>
  );
}

function OpsMode({ toast }) {
  const [filter, setFilter] = useState("ALL");
  const [view, setView] = useState("stream");
  const [starred, setStarred] = useState({});
  const [timeWin, setTimeWin] = useState("LIVE");
  const [tiers, setTiers] = useState({ T1: true, T2: true, T3: true, T4: true, T5: true });
  const [minConf, setMinConf] = useState(0);
  const [kw, setKw] = useState("");
  const [rTab, setRTab] = useState("ledger");
  const [feed, setFeed] = useState(intakePool.slice(0, 6).map((f, i) => ({ ...f, key: "s" + i })));
  const idx = useRef(6);
  useEffect(() => {
    const id = setInterval(() => {
      setFeed((f) => { const n = intakePool[idx.current % intakePool.length]; idx.current += 1; return [{ ...n, key: Date.now() }, ...f].slice(0, 16); });
    }, 2800);
    return () => clearInterval(id);
  }, []);
  const kinds = ["ALL", "ARTICLE", "SOCIAL", "VIDEO", "SCANNER", "AIRCRAFT", "STARRED"];
  const shown = feed.filter((f) => {
    if (filter === "STARRED" && !starred[f.key]) return false;
    if (filter !== "ALL" && filter !== "STARRED" && f.kind !== filter) return false;
    if (!tiers[f.tier]) return false;
    if (f.conf < minConf && f.conf > 0) return false;
    if (kw && !(f.txt + f.src).toLowerCase().includes(kw.toLowerCase())) return false;
    return true;
  });
  const buckets = [
    { id: "FLUME", label: "FLUME FIRE", color: C.emberHot }, { id: "NEW?", label: "POSSIBLE NEW START", color: C.violet },
    { id: "FLUME?", label: "ADJUDICATING", color: C.violet }, { id: "—", label: "NO MATCH", color: C.faint },
  ];

  return (
    <div style={{ flex: 1, display: "grid", gridTemplateColumns: "330px 1fr 320px", minHeight: 0 }}>
      {/* intake */}
      <div style={{ borderRight: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {/* row 1: view + time window + kinds */}
        <div style={{ display: "flex", gap: 4, padding: "7px 9px 4px", flexWrap: "wrap", alignItems: "center", flexShrink: 0 }}>
          <div style={{ display: "flex", border: `1px solid ${C.line}`, borderRadius: 4, overflow: "hidden", marginRight: 2 }}>
            {[["stream", "STREAM"], ["byfire", "BY FIRE"]].map(([k, l]) => (
              <div key={k} onClick={() => setView(k)} style={{ fontFamily: font.mono, fontSize: 9, padding: "3px 8px", cursor: "pointer", background: view === k ? C.ember : "transparent", color: view === k ? C.bg : C.dim }}>{l}</div>
            ))}
          </div>
          <div style={{ display: "flex", border: `1px solid ${C.line}`, borderRadius: 4, overflow: "hidden" }}>
            {["LIVE", "15m", "1h", "3h", "24h"].map((t) => (
              <div key={t} onClick={() => { setTimeWin(t); toast(`Intake window: ${t === "LIVE" ? "live stream" : "last " + t}`); }} style={{ fontFamily: font.mono, fontSize: 9, padding: "3px 7px", cursor: "pointer", background: timeWin === t ? C.blue : "transparent", color: timeWin === t ? C.bg : C.dim }}>{t}</div>
            ))}
          </div>
          {kinds.map((k) => <Chip key={k} active={filter === k} color={k === "STARRED" ? C.warn : C.ember} onClick={() => setFilter(k)}>{k === "STARRED" ? `★ ${Object.values(starred).filter(Boolean).length}` : k}</Chip>)}
        </div>
        {/* row 2: tiers + confidence + keyword */}
        <div style={{ display: "flex", gap: 6, padding: "3px 9px 7px", alignItems: "center", borderBottom: `1px solid ${C.line}`, flexShrink: 0, flexWrap: "wrap" }}>
          <div style={{ display: "flex", gap: 3 }}>
            {["T1", "T2", "T3", "T4", "T5"].map((t) => (
              <span key={t} onClick={() => setTiers((s) => ({ ...s, [t]: !s[t] }))} title={{ T1: "Official", T2: "Semi-official / sensors", T3: "Scanner / trusted", T4: "Media", T5: "Community / social" }[t]}
                style={{ fontFamily: font.mono, fontSize: 8.5, padding: "2px 5px", borderRadius: 3, cursor: "pointer", background: tiers[t] ? TIER[t] + "22" : "transparent", border: `1px solid ${tiers[t] ? TIER[t] : C.line}`, color: tiers[t] ? TIER[t] : C.faint }}>{t}</span>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 5, flex: "0 0 auto" }}>
            <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>conf ≥{minConf}%</span>
            <input type="range" min="0" max="90" step="10" value={minConf} onChange={(e) => setMinConf(+e.target.value)} style={{ width: 64, accentColor: C.ember }} />
          </div>
          <input value={kw} onChange={(e) => setKw(e.target.value)} placeholder="⌕ keyword (Pentz, tanker…)"
            style={{ flex: 1, minWidth: 90, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.txt, fontFamily: font.mono, fontSize: 9.5, padding: "4px 7px", outline: "none" }} />
          {(kw || minConf > 0 || Object.values(tiers).some((v) => !v) || filter !== "ALL") && (
            <span onClick={() => { setKw(""); setMinConf(0); setTiers({ T1: true, T2: true, T3: true, T4: true, T5: true }); setFilter("ALL"); }}
              style={{ fontFamily: font.mono, fontSize: 8.5, color: C.evac, cursor: "pointer" }}>✕ clear</span>
          )}
          <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginLeft: "auto" }}>{shown.length}/{feed.length} shown</span>
        </div>
        <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {view === "stream" && shown.map((f, i) => (
            <IntakeRow key={f.key} f={f} fresh={i === 0} starred={!!starred[f.key]} onStar={() => setStarred((s) => ({ ...s, [f.key]: !s[f.key] }))} onDraft={() => toast("Sent to Newsroom draft materials")} />
          ))}
          {view === "byfire" && buckets.map((b) => {
            const items = shown.filter((f) => f.inc === b.id);
            if (!items.length) return null;
            return (
              <div key={b.id}>
                <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "7px 10px 4px", position: "sticky", top: 0, background: C.panel, zIndex: 1 }}>
                  <span style={{ width: 7, height: 7, borderRadius: 99, background: b.color }} />
                  <span style={{ fontFamily: font.disp, fontSize: 13, fontWeight: 700, letterSpacing: 1.5, color: b.color }}>{b.label}</span>
                  <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint }}>{items.length} · routing live</span>
                </div>
                {items.map((f, i) => <IntakeRow key={f.key} f={f} fresh={shown[0] === f} starred={!!starred[f.key]} onStar={() => setStarred((s) => ({ ...s, [f.key]: !s[f.key] }))} onDraft={() => toast("Sent to Newsroom draft materials")} />)}
              </div>
            );
          })}
        </div>
      </div>

      {/* map */}
      <div style={{ position: "relative", background: "#10141a", minWidth: 0 }}>
        <svg viewBox="0 0 800 520" style={{ width: "100%", height: "100%", display: "block" }} preserveAspectRatio="xMidYMid slice">
          {[...Array(8)].map((_, i) => (<path key={i} d={`M-50 ${80 + i * 56} C ${180 + i * 12} ${34 + i * 50}, ${420 - i * 10} ${140 + i * 48}, 850 ${92 + i * 52}`} fill="none" stroke="#232c36" strokeWidth="1" />))}
          <path d="M120 520 C 260 390, 340 280, 470 105" stroke="#39434e" strokeWidth="3" fill="none" />
          <text x="292" y="330" fill={C.faint} fontSize="10" fontFamily={font.mono} transform="rotate(-50 292 330)">SKYWAY</text>
          <path d="M430 115 L580 88 L640 190 L560 270 L450 234 Z" fill={C.evac} opacity="0.13" stroke={C.evac} strokeWidth="1.5" strokeDasharray="6 4" />
          <text x="494" y="170" fill={C.evac} fontSize="11" fontFamily={font.mono} fontWeight="700">E032 · ORDER</text>
          <path d="M470 146 C 500 128, 545 136, 556 168 C 566 202, 530 228, 494 220 C 462 212, 450 168, 470 146 Z" fill={C.emberHot} opacity="0.22" stroke={C.emberHot} strokeWidth="2" />
          <Pulse x={548} y={162} /><Pulse x={512} y={144} size={7} color={C.ember} />
          {/* aircraft layer */}
          <ellipse cx="516" cy="182" rx="64" ry="40" fill="none" stroke={C.blue} strokeWidth="1" strokeDasharray="2 4" opacity="0.5" />
          <Aircraft x={578} y={158} rot={35} label="AA-230 · 6.2k" />
          <Aircraft x={452} y={214} rot={210} label="T-912" color={C.ember} />
          <Aircraft x={340} y={300} rot={60} label="CPTR H-406" color={C.violet} />
          {/* possible new start — watch board tie-in */}
          <g transform="translate(640 380)">
            <circle r="14" fill="none" stroke={C.violet} strokeWidth="1.5" strokeDasharray="4 3" className="tr-pulse" />
            <circle r="3" fill={C.violet} />
            <text x="18" y="-4" fill={C.violet} fontSize="9" fontFamily={font.mono}>NEW START? 2/3 signals</text>
            <text x="18" y="8" fill={C.faint} fontSize="8.5" fontFamily={font.mono}>AA orbit + smoke check traffic</text>
          </g>
          <g transform="translate(706 60)">
            <line x1="0" y1="-22" x2="0" y2="22" stroke={C.blue} strokeWidth="2" transform="rotate(200)" />
            <polygon points="0,-28 -6,-16 6,-16" fill={C.blue} transform="rotate(200)" />
            <text x="-30" y="42" fill={C.blue} fontSize="10" fontFamily={font.mono}>N 25 G40</text>
          </g>
        </svg>
        <div style={{ position: "absolute", top: 8, left: 10, display: "flex", gap: 5, flexWrap: "wrap" }}>
          {["Hotspots", "Perimeter", "Evac", "Aircraft", "Cams", "Media", "History"].map((l, i) => (
            <Chip key={l} active={i < 6} color={C.ember}>{l}</Chip>
          ))}
        </div>
        <div style={{ position: "absolute", bottom: 10, left: "50%", transform: "translateX(-50%)", width: "70%", maxWidth: 520, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: "8px 13px", display: "flex", gap: 9, alignItems: "center", boxShadow: "0 6px 24px #000a" }}>
          <span style={{ color: C.ember, fontFamily: font.mono }}>⌕</span>
          <span style={{ color: C.dim, fontFamily: font.mono, fontSize: 11.5 }}>which fires have VLATs assigned right now?</span>
        </div>
      </div>

      {/* right panel: ledger / deployed / weather */}
      <div style={{ borderLeft: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: "10px 13px", borderBottom: `1px solid ${C.line}`, flexShrink: 0 }}>
          <div style={{ fontFamily: font.disp, fontSize: 20, fontWeight: 700 }}>FLUME FIRE</div>
          <div style={{ fontFamily: font.mono, fontSize: 10, color: C.dim, marginTop: 2 }}>1,240 ac · +310/hr · 0% · <span style={{ color: C.ok }}>⚑ 27 min lead</span></div>
        </div>
        <div style={{ display: "flex", borderBottom: `1px solid ${C.line}`, flexShrink: 0 }}>
          {[["ledger", "LEDGER"], ["deployed", "DEPLOYED"], ["wx", "WEATHER"]].map(([k, l]) => (
            <div key={k} onClick={() => setRTab(k)} style={{ flex: 1, textAlign: "center", padding: "7px 0", cursor: "pointer", fontFamily: font.disp, fontSize: 12.5, letterSpacing: 1.5, fontWeight: 600, color: rTab === k ? C.ember : C.faint, borderBottom: `2px solid ${rTab === k ? C.ember : "transparent"}` }}>{l}</div>
          ))}
        </div>
        <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {rTab === "ledger" && (
            <>
              {/* stale-news catch example */}
              <div style={{ margin: 9, padding: 7, borderRadius: 5, border: `1px solid ${C.warn}44`, background: C.warn + "0d", fontFamily: font.mono, fontSize: 9.5, color: C.dim }}>
                <span style={{ color: C.warn }}>⏱ STALE CAUGHT</span> — article ingested 15:41 reports "400 acres"; LLM dated claim to ~13:00 (observed_at ≠ ingested_at). Excluded from updates — ledger already at 1,240 [#128].
              </div>
              {[
                { t: "15:44", tier: "T2", txt: "Tanker 912 en route — 4th tanker assigned", conf: 92 },
                { t: "15:42", tier: "T3", txt: "Spot fire across Flume Rd — geocoded", conf: 74 },
                { t: "15:40", tier: "T2", txt: "Cam B-114 column confirmed 042°", conf: 87 },
                { t: "15:37", tier: "T1", txt: "PAR-E032 WARNING → ORDER", conf: 99 },
                { t: "15:29", tier: "T1", txt: "FIRMS 3 detections NE flank", conf: 95 },
              ].map((f, i) => (
                <div key={i} className="tr-row" style={{ padding: "7px 11px", borderBottom: `1px solid ${C.line}55`, display: "flex", gap: 8 }}>
                  <div style={{ width: 3, borderRadius: 2, background: TIER[f.tier] }} />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, display: "flex" }}><span>{f.t}</span><span style={{ marginLeft: "auto", color: f.conf > 80 ? C.ok : C.warn }}>{f.conf}%</span></div>
                    <div style={{ fontSize: 11.5, marginTop: 1 }}>{f.txt}</div>
                  </div>
                </div>
              ))}
            </>
          )}

          {rTab === "deployed" && (
            <div style={{ padding: "10px 12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontFamily: font.disp, fontSize: 15, fontWeight: 700, color: C.blue }}>≈ 312 PERSONNEL</span>
                <span style={{ fontFamily: font.mono, fontSize: 9, color: C.ok }}>+180 since 12:00</span>
              </div>
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginBottom: 8 }}>assembled from dispatch traffic + ADS-B + official updates · per-item source & confidence</div>
              {[
                { cat: "AIR", items: [["Air Attack 230", "on scene · orbiting", "ADS-B", 98], ["Tankers 910, 911, 914", "cycling CIC ↔ fire", "ADS-B", 96], ["Tanker 912", "en route", "ADS-B", 92], ["Copters H-406, H-521", "bucket work, west flank", "scanner", 81]] },
                { cat: "GROUND", items: [["24 engines", "12 CAL FIRE · 8 local gov · 4 OES", "official + scanner", 88], ["6 hand crews", "incl. 2 inmate crews per Tac 8", "scanner", 72], ["4 dozers", "line construction NE flank", "scanner", 76], ["2 water tenders", "Pentz corridor", "scanner", 68]] },
                { cat: "OVERHEAD", items: [["Type 3 IC assigned", "transition to Type 2 team ordered", "scanner", 64], ["Strike team 9142C", "structure defense", "dispatch", 84]] },
              ].map((g) => (
                <div key={g.cat} style={{ marginBottom: 9 }}>
                  <div style={{ fontFamily: font.disp, fontSize: 11.5, letterSpacing: 2, color: C.dim, borderBottom: `1px solid ${C.line}`, paddingBottom: 2, marginBottom: 4 }}>{g.cat}</div>
                  {g.items.map(([n, s, src, cf]) => (
                    <div key={n} style={{ padding: "3px 0" }}>
                      <div style={{ display: "flex", fontSize: 11.5 }}>
                        <span style={{ fontWeight: 600 }}>{n}</span>
                        <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 9, color: cf > 85 ? C.ok : cf > 70 ? C.warn : C.evac }}>{cf}%</span>
                      </div>
                      <div style={{ fontFamily: font.mono, fontSize: 9, color: C.dim }}>{s} <span style={{ color: C.faint }}>· via {src}</span></div>
                    </div>
                  ))}
                </div>
              ))}
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.violet, borderTop: `1px dashed ${C.line}`, paddingTop: 6 }}>
                ▲ ESCALATION SIGNAL: Type 2 team ordered + 4th tanker = severity rising — alert rule matched, flagged for next update
              </div>
            </div>
          )}

          {rTab === "wx" && (
            <div style={{ padding: "10px 12px" }}>
              <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim }}>JARBO GAP RAWS · 2.1 MI FROM FIRE · 15:40</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 7 }}>
                {[["TEMP", "96°F", C.emberHot], ["RH", "9%", C.emberHot], ["WIND", "N 25 G40", C.warn]].map(([l, v, col]) => (
                  <div key={l} style={{ background: C.panelUp, borderRadius: 6, padding: "8px 10px", textAlign: "center" }}>
                    <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>{l}</div>
                    <div style={{ fontFamily: font.disp, fontSize: 19, fontWeight: 700, color: col }}>{v}</div>
                  </div>
                ))}
              </div>
              {/* hourly forecast strip */}
              <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, marginTop: 12 }}>NEXT 8 HOURS · HRRR</div>
              <div style={{ display: "flex", gap: 3, marginTop: 6 }}>
                {[["16", 97, 8, "G42"], ["17", 96, 8, "G44"], ["18", 93, 10, "G38"], ["19", 88, 13, "G30"], ["20", 82, 17, "G24"], ["21", 76, 22, "G18"], ["22", 71, 26, "14"], ["23", 67, 30, "10"]].map(([h, t, rh, w]) => (
                  <div key={h} style={{ flex: 1, background: C.panelUp, borderRadius: 4, padding: "5px 2px", textAlign: "center" }}>
                    <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint }}>{h}h</div>
                    <div style={{ fontFamily: font.mono, fontSize: 9.5, color: t > 90 ? C.emberHot : C.txt }}>{t}°</div>
                    <div style={{ fontFamily: font.mono, fontSize: 8.5, color: rh < 15 ? C.emberHot : rh < 25 ? C.warn : C.ok }}>{rh}%</div>
                    <div style={{ fontFamily: font.mono, fontSize: 8, color: String(w).startsWith("G4") ? C.warn : C.dim }}>{w}</div>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 10, padding: 8, background: C.panelUp, borderRadius: 6, fontSize: 11, lineHeight: 1.5, borderLeft: `2px solid ${C.warn}` }}>
                <span style={{ fontFamily: font.mono, fontSize: 9, color: C.warn }}>AI FIRE-WX READ</span><br />
                Peak alignment 16:00–18:00: gusts to 44 with single-digit RH and wind/slope alignment on the Skyway corridor. Moderation begins ~19:00 as RH recovers; best containment window overnight. Red Flag expires 20:00.
              </div>
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 6 }}>sources: RAWS (free) · HRRR + HRRR-Smoke (free) · NWS CAP · fuel moisture from NFMD</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ================= NEWSROOM MODE ================= */
function NewsroomMode({ toast }) {
  const [selDraft, setSelDraft] = useState(0);
  const [url, setUrl] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [drafts, setDrafts] = useState([
    { title: "FLUME FIRE UPDATE #6 — Evac order expanded, spot fire across Flume Rd", status: "NEEDS REVIEW", when: "auto-drafted 15:43", basis: "12 new ledger facts since Update #5", body: "The Flume Fire has grown to approximately 1,240 acres with 0% containment as of 3:40 PM [#128]. Evacuation ORDERS are now in effect for zones PAR-E031 and E032 [#137] — check your zone at community.zonehaven.com.\n\nFire crews confirmed a spot fire across Flume Road [#139,#141], and structure defense resources are assigned to the Pentz Road corridor [#142]. A fourth air tanker was assigned within the last hour [#144].\n\nConditions: Red Flag Warning through 8 PM with north winds gusting to 40 mph [#122]. Smoke is expected to reach Chico around 6 PM [#124].\n\nUNCONFIRMED: A community photo shows possible ember cast on Pentz Rd; we are seeking a second source before treating this as confirmed [#140]." },
    { title: "ANALYSIS — Why the NE flank matters tonight", status: "DRAFT", when: "auto-drafted 15:20", basis: "weather + terrain + ledger", body: "…" },
    { title: "FLUME FIRE UPDATE #5", status: "PUBLISHED 14:55", when: "approved by you", basis: "site + X + FB · 2,340 views", body: "…" },
    { title: "CORRECTION — recirculated 2018 photo", status: "PUBLISHED 15:12", when: "approved by you", basis: "media triage reverse-search", body: "…" },
  ]);
  const d = drafts[selDraft];
  const statusColor = (s) => s.startsWith("PUBLISHED") ? C.ok : s === "NEEDS REVIEW" ? C.warn : C.dim;

  const analyze = () => {
    if (!url.trim()) return;
    setAnalyzing(true);
    setTimeout(() => {
      setAnalyzing(false);
      setDrafts((ds) => [{ title: "SOURCED — Key claims from: " + url.replace(/^https?:\/\//, "").slice(0, 38) + "…", status: "NEEDS REVIEW", when: "analyzed just now", basis: "5 claims extracted · 2 new · 2 corroborate ledger · 1 CONFLICTS with #131", body: "AI analysis of the linked article:\n\nNEW CLAIMS (not yet in ledger):\n• County reports 3 outbuildings damaged near Flume Rd — single-source, holding at 60% confidence\n• Shelter at Neighborhood Church nearing capacity per county PIO quote\n\nCORROBORATES: acreage figure [#128], evac zones [#137]\n\n⚠ CONFLICT: article states 5% containment; CAL FIRE 15:30 update says 0% [#131]. Recommend citing official figure.\n\nDraft paragraph ready to merge into Update #7 — review before publish." }, ...ds]);
      setSelDraft(0);
      setUrl("");
      toast("Article analyzed — draft created");
    }, 1400);
  };

  return (
    <div style={{ flex: 1, display: "grid", gridTemplateColumns: "300px 1fr 290px", minHeight: 0 }}>
      {/* draft queue */}
      <div style={{ borderRight: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {/* POINT-AT-ARTICLE */}
        <div style={{ padding: 10, borderBottom: `1px solid ${C.line}`, flexShrink: 0 }}>
          <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, marginBottom: 5 }}>POINT AI AT ANYTHING</div>
          <div style={{ display: "flex", gap: 6 }}>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="paste article / video / post URL…"
              style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.txt, fontFamily: font.mono, fontSize: 10.5, padding: "6px 8px", outline: "none" }} />
            <button onClick={analyze} style={{ background: analyzing ? C.faint : C.ember, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 12, letterSpacing: 1, padding: "0 11px", cursor: "pointer" }}>
              {analyzing ? "…" : "ANALYZE"}
            </button>
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 4 }}>extracts claims · checks vs ledger · flags conflicts · drafts report</div>
        </div>
        <div style={{ padding: "8px 12px 4px", fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, flexShrink: 0 }}>DRAFT QUEUE</div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {drafts.map((dr, i) => (
            <div key={i} onClick={() => setSelDraft(i)} className="tr-row" style={{ padding: "9px 12px", borderLeft: `3px solid ${i === selDraft ? C.ember : "transparent"}`, background: i === selDraft ? C.panelUp : undefined, cursor: "pointer", borderBottom: `1px solid ${C.line}55` }}>
              <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.3 }}>{dr.title}</div>
              <div style={{ fontFamily: font.mono, fontSize: 9, marginTop: 3, display: "flex", gap: 8 }}>
                <span style={{ color: statusColor(dr.status) }}>{dr.status}</span><span style={{ color: C.faint }}>{dr.when}</span>
              </div>
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 2 }}>{dr.basis}</div>
            </div>
          ))}
        </div>
      </div>

      {/* editor */}
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0, background: C.bg }}>
        <div style={{ padding: "10px 16px", borderBottom: `1px solid ${C.line}`, display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
          <span style={{ fontFamily: font.disp, fontSize: 16, fontWeight: 700, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.title}</span>
          <Chip color={statusColor(d.status)} active>{d.status}</Chip>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "14px 18px" }}>
          {/* TIME WINDOW — what facts feed this draft */}
          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 7, padding: "10px 12px", marginBottom: 14 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim }}>TIME WINDOW</span>
              {[["Since Update #5 (14:55)", true], ["Last hour", false], ["Last 3h", false], ["Full incident", false], ["Custom…", false]].map(([p, on]) => (
                <span key={p} onClick={() => toast(`Window set: ${p} — draft will only use facts in range`)} style={{ fontFamily: font.mono, fontSize: 9, padding: "3px 8px", borderRadius: 3, cursor: "pointer", border: `1px solid ${on ? C.ember : C.line}`, color: on ? C.ember : C.dim, background: on ? C.ember + "12" : "transparent" }}>{p}</span>
              ))}
              <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 9.5 }}>
                <span style={{ color: C.ok }}>12 facts IN</span><span style={{ color: C.faint }}> · 135 older excluded</span>
              </span>
            </div>
            {/* visual density strip: excluded dim, included bright, drag handle */}
            <div style={{ position: "relative", marginTop: 9, height: 30 }}>
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "flex-end", gap: 2 }}>
                {[3, 2, 4, 3, 5, 4, 6, 3, 5, 7, 6, 4, 5, 8, 6, 9, 7, 11, 9, 14].map((h, i) => (
                  <div key={i} style={{ flex: 1, height: h * 2, borderRadius: 1, background: i >= 15 ? C.ember : C.faint, opacity: i >= 15 ? 0.95 : 0.35 }} />
                ))}
              </div>
              {/* window start handle */}
              <div style={{ position: "absolute", left: "75%", top: -4, bottom: -4, width: 2, background: C.ember, cursor: "ew-resize" }}>
                <div style={{ position: "absolute", top: -1, left: -5, width: 12, height: 12, borderRadius: 3, background: C.ember, cursor: "ew-resize" }} />
                <div style={{ position: "absolute", top: -1, left: 10, fontFamily: font.mono, fontSize: 8, color: C.ember, whiteSpace: "nowrap" }}>14:55 ▸ drag</div>
              </div>
              {/* markers */}
              <div style={{ position: "absolute", left: "75%", right: 0, top: 0, bottom: 0, background: C.ember + "0a", borderRadius: 3, pointerEvents: "none" }} />
              <div style={{ position: "absolute", left: "38%", bottom: -2, fontFamily: font.mono, fontSize: 7.5, color: C.faint }}>Update #4</div>
              <div style={{ position: "absolute", right: 0, top: -2, fontFamily: font.mono, fontSize: 7.5, color: C.ok }}>NOW</div>
            </div>
            <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 8 }}>
              drag the handle or pick a preset — REGENERATE uses only facts inside the window · older facts still available as background context, never re-reported as news
            </div>
          </div>

          <textarea defaultValue={d.body} key={selDraft} spellCheck={false}
            style={{ width: "100%", minHeight: 320, background: "transparent", border: "none", outline: "none", resize: "vertical", color: C.txt, fontFamily: font.body, fontSize: 13.5, lineHeight: 1.65 }} />
          <div style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, marginTop: 6 }}>
            [#nnn] = ledger citation · hover shows source + confidence · citations locked to facts, edit text freely
          </div>
          {/* attached media */}
          <div style={{ marginTop: 14, borderTop: `1px dashed ${C.line}`, paddingTop: 10 }}>
            <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, marginBottom: 7 }}>ATTACHED MEDIA · drag from triage or paste link</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {[["🗺 MAP SNAPSHOT", "auto · perimeter + evac zones + timestamp", C.ember], ["📷 CAM B-114 FRAME", "confirmed · ALERTCalifornia credit", C.ok], ["🔊 SCANNER CLIP 0:14", "\u201Cspot fire across Flume Rd\u201D + transcript", C.warn], ["▶ YT: NorCal Fire Watch", "linked with timestamp 12:41", C.blue]].map(([t, s, col]) => (
                <div key={t} style={{ background: C.panelUp, border: `1px solid ${col}44`, borderRadius: 6, padding: "7px 10px", fontSize: 10.5 }}>
                  <div style={{ color: col, fontFamily: font.mono, fontSize: 10 }}>{t} <span style={{ color: C.faint, cursor: "pointer" }}> ✕</span></div>
                  <div style={{ color: C.dim, fontSize: 9.5, marginTop: 2 }}>{s}</div>
                </div>
              ))}
              <div onClick={() => toast("Media picker: triage grid + starred items + paste URL")} style={{ border: `1px dashed ${C.line}`, borderRadius: 6, padding: "7px 12px", color: C.faint, fontFamily: font.mono, fontSize: 10, cursor: "pointer", alignSelf: "stretch", display: "flex", alignItems: "center" }}>+ attach</div>
            </div>
          </div>
        </div>
        <div style={{ padding: "10px 16px", borderTop: `1px solid ${C.line}`, display: "flex", gap: 8, flexShrink: 0, alignItems: "center" }}>
          <button onClick={() => toast("Saved to drafts")} style={{ background: C.panelUp, color: C.txt, border: `1px solid ${C.line}`, borderRadius: 5, fontFamily: font.disp, fontWeight: 600, fontSize: 13, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}>SAVE</button>
          <button onClick={() => toast("Regenerating from latest ledger facts…")} style={{ background: C.panelUp, color: C.violet, border: `1px solid ${C.violet}44`, borderRadius: 5, fontFamily: font.disp, fontWeight: 600, fontSize: 13, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}>⟳ REGENERATE</button>
          <span style={{ fontFamily: font.mono, fontSize: 9, color: C.faint }}>AI drafts · you approve · nothing publishes itself</span>
          <button onClick={() => toast("Approved → distribution panel armed")} style={{ marginLeft: "auto", background: C.ember, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 13, letterSpacing: 1, padding: "7px 18px", cursor: "pointer" }}>APPROVE ✓</button>
        </div>
      </div>

      {/* distribution */}
      <div style={{ borderLeft: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: "10px 13px", fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, borderBottom: `1px solid ${C.line}` }}>DISTRIBUTION</div>
        <div style={{ padding: 12, overflowY: "auto" }}>
          {[["Site page + RSS", true, "full report"], ["X / Twitter", true, "thread · 3 posts + card"], ["Facebook", true, "post + card"], ["Bluesky", false, "post + card"], ["Email digest", true, "1,204 subscribers"], ["Webhook / Discord", false, "community server"]].map(([p, on, note]) => (
            <div key={p} style={{ display: "flex", alignItems: "center", gap: 9, padding: "6px 0", borderBottom: `1px solid ${C.line}44` }}>
              <span style={{ width: 26, height: 14, borderRadius: 8, background: on ? C.ok : C.faint, position: "relative", flexShrink: 0 }}>
                <span style={{ position: "absolute", top: 2, left: on ? 14 : 2, width: 10, height: 10, borderRadius: 99, background: C.bg }} />
              </span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 11.5 }}>{p}</div>
                <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>{note}</div>
              </div>
            </div>
          ))}
          {/* share card preview */}
          <div style={{ marginTop: 12, fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim }}>SHARE CARD · auto-generated</div>
          <div style={{ marginTop: 6, borderRadius: 8, overflow: "hidden", border: `1px solid ${C.line}` }}>
            <svg viewBox="0 0 280 140" style={{ display: "block", width: "100%" }}>
              <rect width="280" height="140" fill="#10141a" />
              {[...Array(4)].map((_, i) => (<path key={i} d={`M0 ${40 + i * 26} C 90 ${28 + i * 24}, 190 ${52 + i * 24}, 280 ${36 + i * 26}`} stroke="#232c36" fill="none" />))}
              <path d="M150 44 C 172 34, 200 40, 206 60 C 212 80, 188 96, 166 90 C 146 84, 138 56, 150 44 Z" fill={C.emberHot} opacity="0.3" stroke={C.emberHot} strokeWidth="1.5" />
              <path d="M130 30 L216 18 L246 70 L200 104 L140 88 Z" fill="none" stroke={C.evac} strokeWidth="1" strokeDasharray="4 3" />
              <text x="12" y="22" fill={C.ember} fontSize="13" fontFamily={font.disp} fontWeight="700">FLUME FIRE — UPDATE #6</text>
              <text x="12" y="120" fill={C.txt} fontSize="9" fontFamily={font.mono}>1,240 ac · 0% · EVAC ORDER E031-032</text>
              <text x="12" y="132" fill={C.faint} fontSize="7.5" fontFamily={font.mono}>thetoastreport.com · 15:43 PDT JUL 17 · verify with officials</text>
            </svg>
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 5 }}>timestamp burned in — screenshots stay honest</div>
          <button onClick={() => toast("Published to 4 channels · site rebuilt")} style={{ width: "100%", marginTop: 12, background: C.ok, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 14, letterSpacing: 1, padding: "9px 0", cursor: "pointer" }}>PUBLISH TO 4 CHANNELS →</button>
        </div>
      </div>
    </div>
  );
}

/* ================= WATCH MODE (new starts) ================= */
function WatchMode() {
  const candidates = [
    { name: "CANDIDATE · Bloomer Hill area", signals: [["AA-230 orbit 11 min", true], ["Scanner: smoke check dispatched", true], ["Hotspot persistence", false]], score: 66, note: "Graduates to incident at 3/3 or any T1 confirm", color: C.violet },
    { name: "CANDIDATE · Hwy 70 corridor", signals: [["Lightning strike 03:12 (holdover watch 41h remaining)", true], ["FIRMS single detect — awaiting 2nd frame", true], ["No scanner traffic", false]], score: 52, note: "Holdover fires can smolder 24-72h after strikes", color: C.violet },
    { name: "SUPPRESSED · Oroville ag burn", signals: [["FIRMS detect", true], ["Matched CARB burn authorization #B-2241", true]], score: 0, note: "Auto-suppressed — will alert if it exceeds permitted footprint", color: C.faint },
  ];
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 18, display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))", gap: 14, alignContent: "start" }}>
      <div style={{ gridColumn: "1 / -1", display: "flex", alignItems: "baseline", gap: 12 }}>
        <span style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>NEW STARTS BOARD</span>
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>pre-incident signals · corroboration in progress · 2 watching · 1 suppressed</span>
      </div>
      {candidates.map((c) => (
        <div key={c.name} style={{ background: C.panel, border: `1px solid ${c.color}44`, borderRadius: 8, padding: 14, opacity: c.score === 0 ? 0.6 : 1 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <span style={{ fontFamily: font.disp, fontSize: 15, fontWeight: 700, color: c.color === C.faint ? C.dim : C.txt }}>{c.name}</span>
            {c.score > 0 && <span style={{ fontFamily: font.mono, fontSize: 11, color: c.score > 60 ? C.warn : C.dim }}>{c.score}%</span>}
          </div>
          <div style={{ marginTop: 10 }}>
            {c.signals.map(([s, on]) => (
              <div key={s} style={{ display: "flex", gap: 8, alignItems: "center", padding: "3px 0", fontFamily: font.mono, fontSize: 10.5 }}>
                <span style={{ color: on ? C.ok : C.faint }}>{on ? "✓" : "○"}</span>
                <span style={{ color: on ? C.txt : C.faint }}>{s}</span>
              </div>
            ))}
          </div>
          {/* corroboration bar */}
          <div style={{ marginTop: 10, height: 5, borderRadius: 3, background: C.line, overflow: "hidden" }}>
            <div style={{ width: `${c.score}%`, height: "100%", background: c.score > 60 ? C.warn : C.dim, transition: "width .4s" }} />
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, marginTop: 7 }}>{c.note}</div>
        </div>
      ))}
      <div style={{ gridColumn: "1 / -1", background: C.panel, border: `1px dashed ${C.line}`, borderRadius: 8, padding: 12, fontFamily: font.mono, fontSize: 10, color: C.dim }}>
        WATCHING STATEWIDE: 3 lightning holdover zones · 1 PSPS de-energization event · aircraft launches with no matched incident · unexplained FIRMS clusters. Candidates graduate automatically and inherit their full pre-detection ledger — your first-detection proof.
      </div>
    </div>
  );
}

/* ================= SOURCES MODE ================= */
function SourcesMode({ toast }) {
  const [adding, setAdding] = useState(false);
  const sources = [
    { name: "CAL FIRE / WFIGS", type: "API", tier: "T1", up: "99.9%", yield: "38/day", lead: "—", acc: "100%", note: "" },
    { name: "GOES-18 + FIRMS", type: "SAT", tier: "T1", up: "99%", yield: "112/day", lead: "+22 min avg", acc: "94%", note: "false positives auto-filtered vs PFIRS" },
    { name: "Butte Fire Net (scanner)", type: "AUDIO", tier: "T3", up: "97%", yield: "64/day", lead: "+18 min avg", acc: "91%", note: "" },
    { name: "YT: NorCal Fire Watch", type: "VIDEO", tier: "T3↑", up: "live events", yield: "12/event", lead: "+9 min avg", acc: "96%", note: "promoted from T4 after 14 verified firsts · your override 6/12" },
    { name: "X @buttescanner", type: "SOCIAL", tier: "T4↑", up: "—", yield: "22/day", lead: "+6 min avg", acc: "89%", note: "promoted after 30-day review" },
    { name: "KRCR / Chico ER scrape", type: "NEWS", tier: "T4", up: "98%", yield: "17/day", lead: "-11 min", acc: "93%", note: "" },
    { name: "FB Paradise Alert Group", type: "SOCIAL", tier: "T5", up: "—", yield: "41/day", lead: "+3 min", acc: "62%", note: "high noise — extraction threshold raised" },
  ];
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
        <span style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>SOURCE MANAGER</span>
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>trust is earned — lead time & accuracy tracked per source, promotions need your approval</span>
        <button onClick={() => setAdding(!adding)} style={{ marginLeft: "auto", background: C.ember, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 13, letterSpacing: 1, padding: "7px 14px", cursor: "pointer" }}>+ ADD SOURCE</button>
      </div>

      {adding && (
        <div className="tr-in" style={{ background: C.panel, border: `1px solid ${C.ember}44`, borderRadius: 8, padding: 14, marginBottom: 14 }}>
          <div style={{ fontFamily: font.disp, fontSize: 14, fontWeight: 700, letterSpacing: 1, marginBottom: 9 }}>ADD SOURCE — AI will figure out how to ingest it</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input placeholder="URL / YouTube channel / RSS / scanner stream / X handle…" style={{ flex: "1 1 300px", background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.txt, fontFamily: font.mono, fontSize: 11, padding: "8px 10px", outline: "none" }} />
            <select style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.txt, fontFamily: font.mono, fontSize: 11, padding: "8px 10px" }}>
              <option>Start at tier: T5 (unproven)</option><option>T4 — I trust this source</option><option>T3 — highly trusted (e.g. known YouTuber)</option>
            </select>
            <button onClick={() => { setAdding(false); toast("Source probed: YouTube channel detected → live-stream transcript watcher configured"); }} style={{ background: C.ok, color: C.bg, border: "none", borderRadius: 5, fontFamily: font.disp, fontWeight: 700, fontSize: 13, padding: "0 16px", cursor: "pointer" }}>PROBE & ADD</button>
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, marginTop: 7 }}>
            AI probes the target: YouTube → monitors for live streams + transcribes · article site → scraper + change detection · X handle → crawl · audio URL → Whisper pipeline. You set starting trust; performance adjusts it from there.
          </div>
        </div>
      )}

      <div style={{ background: C.panel, borderRadius: 8, border: `1px solid ${C.line}`, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: "2fr .7fr .6fr .7fr .8fr .9fr .7fr", padding: "8px 14px", fontFamily: font.mono, fontSize: 9, color: C.faint, letterSpacing: 1, borderBottom: `1px solid ${C.line}` }}>
          <span>SOURCE</span><span>TYPE</span><span>TIER</span><span>UPTIME</span><span>YIELD</span><span>LEAD vs OFFICIAL</span><span>ACCURACY</span>
        </div>
        {sources.map((s) => (
          <div key={s.name} className="tr-row" style={{ display: "grid", gridTemplateColumns: "2fr .7fr .6fr .7fr .8fr .9fr .7fr", padding: "9px 14px", borderBottom: `1px solid ${C.line}44`, fontSize: 11.5, alignItems: "center" }}>
            <span>
              {s.name}
              {s.note && <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.violet, marginTop: 1 }}>{s.note}</div>}
            </span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>{s.type}</span>
            <span style={{ fontFamily: font.mono, fontSize: 10.5, color: TIER[s.tier.slice(0, 2)] }}>{s.tier}</span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>{s.up}</span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>{s.yield}</span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: s.lead.startsWith("+") ? C.ok : C.dim }}>{s.lead}</span>
            <span style={{ fontFamily: font.mono, fontSize: 10, color: parseInt(s.acc) > 90 ? C.ok : parseInt(s.acc) > 75 ? C.warn : C.evac }}>{s.acc}</span>
          </div>
        ))}
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.faint, marginTop: 10 }}>
        ↑ = pending or recent promotion · LEAD = average minutes ahead of first official report on breaking facts · ACCURACY = facts never superseded by higher-tier contradiction
      </div>
    </div>
  );
}

/* ================= ARCHIVE MODE (database) ================= */
function ArchiveMode({ toast }) {
  const [selFire, setSelFire] = useState(0);
  const fires = [
    { name: "FLUME FIRE", yr: "2026", status: "ACTIVE — ledger open", acres: "1,240+", facts: 147, media: 23, audio: "41 clips", firstBy: "⚑ 27 min (GOES)", color: C.emberHot },
    { name: "CRESTA FIRE", yr: "2026", status: "ACTIVE — 35% cont", acres: "88", facts: 63, media: 6, audio: "12 clips", firstBy: "⚑ 11 min (scanner)", color: C.warn },
    { name: "MESILLA FIRE", yr: "2026", status: "FROZEN · Jun 30", acres: "3,804 final", facts: 611, media: 88, audio: "204 clips", firstBy: "⚑ 34 min (cam AI)", color: C.dim },
    { name: "TWAIN FIRE", yr: "2026", status: "FROZEN · Jun 12", acres: "412 final", facts: 188, media: 31, audio: "77 clips", firstBy: "official first", color: C.dim },
    { name: "ARCHER COMPLEX", yr: "2025", status: "FROZEN · merged 3 fires", acres: "22,116 final", facts: 2140, media: 302, audio: "1,120 clips", firstBy: "⚑ 19 min (scanner)", color: C.dim },
  ];
  const f = fires[selFire];
  return (
    <div style={{ flex: 1, display: "grid", gridTemplateColumns: "320px 1fr", minHeight: 0 }}>
      {/* left: storage + fire list */}
      <div style={{ borderRight: `1px solid ${C.line}`, background: C.panel, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: 12, borderBottom: `1px solid ${C.line}`, flexShrink: 0 }}>
          <div style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim, marginBottom: 6 }}>STORAGE · LOCAL-FIRST</div>
          {[["facts.db (SQLite + vec)", "1.9 GB", C.ok], ["media/ (originals + EXIF)", "48.2 GB", C.ok], ["audio/ (clips + transcripts)", "112.6 GB", C.ok], ["snapshots/ (source pages)", "9.1 GB", C.ok], ["mirror → B2 cold storage", "synced 15:31", C.blue]].map(([k, v, col]) => (
            <div key={k} style={{ display: "flex", justifyContent: "space-between", fontFamily: font.mono, fontSize: 10, padding: "2px 0" }}>
              <span style={{ color: C.dim }}>{k}</span><span style={{ color: col }}>{v}</span>
            </div>
          ))}
          <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, marginTop: 5 }}>append-only ledger · starred items never pruned · frozen fires immutable</div>
        </div>
        {/* semantic search */}
        <div style={{ padding: "9px 12px", borderBottom: `1px solid ${C.line}`, flexShrink: 0 }}>
          <div style={{ display: "flex", gap: 6 }}>
            <input placeholder="search all fires, all years… ('spot fires near Skyway')" style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.txt, fontFamily: font.mono, fontSize: 10, padding: "6px 8px", outline: "none" }} />
            <button onClick={() => toast("Semantic search across 3,149 facts, 450 media, 1,454 audio clips")} style={{ background: C.panelUp, color: C.ember, border: `1px solid ${C.ember}44`, borderRadius: 5, fontFamily: font.mono, fontSize: 11, padding: "0 10px", cursor: "pointer" }}>⌕</button>
          </div>
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {fires.map((fr, i) => (
            <div key={fr.name} onClick={() => setSelFire(i)} className="tr-row" style={{ padding: "10px 12px", borderLeft: `3px solid ${i === selFire ? C.ember : "transparent"}`, background: i === selFire ? C.panelUp : undefined, cursor: "pointer", borderBottom: `1px solid ${C.line}55` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontFamily: font.disp, fontSize: 15, fontWeight: 700 }}>{fr.name} <span style={{ color: C.faint, fontSize: 11, fontWeight: 500 }}>{fr.yr}</span></span>
                <span style={{ width: 7, height: 7, borderRadius: 99, background: fr.color, display: "inline-block" }} />
              </div>
              <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, marginTop: 2 }}>{fr.status} · {fr.acres}</div>
              <div style={{ fontFamily: font.mono, fontSize: 9, color: C.faint, marginTop: 2 }}>{fr.facts} facts · {fr.media} media · {fr.audio} · <span style={{ color: fr.firstBy.startsWith("⚑") ? C.ok : C.faint }}>{fr.firstBy}</span></div>
            </div>
          ))}
        </div>
      </div>

      {/* right: selected fire detail + geolocation pipeline */}
      <div style={{ overflowY: "auto", padding: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <span style={{ fontFamily: font.disp, fontSize: 24, fontWeight: 700, letterSpacing: 0.5 }}>{f.name}</span>
          <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>{f.status}</span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            <Chip color={C.blue} active onClick={() => toast("Exported: full timeline as shareable retrospective article")}>EXPORT RETROSPECTIVE</Chip>
            <Chip color={C.ok} active onClick={() => toast("Exported: ledger.jsonl + media manifest + GeoJSON perimeters")}>EXPORT DATA</Chip>
          </span>
        </div>

        {/* timeline stats row */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginTop: 12 }}>
          {[["FACT LEDGER", `${f.facts}`, "append-only · full supersession history"], ["MEDIA VAULT", `${f.media}`, "originals + EXIF + VLM analysis + geo"], ["AUDIO", f.audio, "scanner clips + transcripts + entities"], ["PERIMETERS", "14 snapshots", "GeoJSON history · replayable"]].map(([t, v, s]) => (
            <div key={t} style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 7, padding: "10px 12px" }}>
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, letterSpacing: 1 }}>{t}</div>
              <div style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, color: C.ember, marginTop: 2 }}>{v}</div>
              <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, marginTop: 2 }}>{s}</div>
            </div>
          ))}
        </div>

        {/* IMAGE GEOLOCATION PIPELINE */}
        <div style={{ marginTop: 16, fontFamily: font.disp, fontSize: 14, fontWeight: 700, letterSpacing: 1.5, color: C.txt }}>IMAGE GEOLOCATION PIPELINE</div>
        <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.faint, marginTop: 2, marginBottom: 8 }}>every incoming image: EXIF GPS → visual placement → bearing triangulation · placed images become map pins + ledger facts</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
          {[
            { m: "1 · EXIF GPS", ex: "Pentz Rd ember photo", how: "GPS 39.7612, -121.6034 in metadata · timestamp valid · device consistent with claimed source", out: "PLACED · 40m accuracy → pinned", col: C.ok },
            { m: "2 · VISUAL MATCH", ex: "Chico column photo (EXIF stripped by platform)", how: "VLM landmark match: Hwy 99 overpass + Sutter Buttes on horizon · sun azimuth vs timestamp checks out", out: "PLACED · ~400m + bearing 118° → sightline drawn", col: C.blue },
            { m: "3 · TRIANGULATION", ex: "Two smoke photos, different users", how: "Bearing lines from photo A (118°) and photo B (042° from cam grid) intersect within 300m of FIRMS cluster", out: "SMOKE SOURCE FIXED · corroborates FLUME origin point", col: C.violet },
          ].map((g) => (
            <div key={g.m} style={{ background: C.panel, border: `1px solid ${g.col}44`, borderRadius: 7, padding: 12 }}>
              <div style={{ fontFamily: font.mono, fontSize: 10, color: g.col, letterSpacing: 1 }}>{g.m}</div>
              <div style={{ fontSize: 11.5, fontWeight: 600, marginTop: 4 }}>{g.ex}</div>
              <div style={{ fontSize: 10.5, color: C.dim, marginTop: 4, lineHeight: 1.45 }}>{g.how}</div>
              <div style={{ fontFamily: font.mono, fontSize: 9.5, color: g.col, marginTop: 6 }}>→ {g.out}</div>
            </div>
          ))}
        </div>

        {/* fact ledger sample */}
        <div style={{ marginTop: 16, fontFamily: font.disp, fontSize: 14, fontWeight: 700, letterSpacing: 1.5 }}>LEDGER — QUERYABLE, EXPORTABLE, IMMUTABLE ONCE FROZEN</div>
        <div style={{ marginTop: 6, background: "#10141a", border: `1px solid ${C.line}`, borderRadius: 7, padding: 12, fontFamily: font.mono, fontSize: 10, lineHeight: 1.7, color: C.dim, overflowX: "auto" }}>
          <div>{"{"}"id":147,"ts":"15:44:02","tier":"T2","src":"adsb:T-912","fact":"4th tanker assigned","conf":0.92,"geo":null,"supersedes":null,"starred":false{"}"}</div>
          <div>{"{"}"id":140,"ts":"15:39:11","tier":"T5","src":"community:4412","fact":"ember cast Pentz Rd","conf":0.38,"geo":[39.7612,-121.6034],"geo_method":"exif","media":"m-2291",<span style={{ color: C.warn }}>"starred":true</span>{"}"}</div>
          <div>{"{"}"id":131,"ts":"15:30:00","tier":"T1","src":"calfire","fact":"containment 0%","conf":0.99,<span style={{ color: C.blue }}>"supersedes":114</span>{"}"}</div>
        </div>
      </div>
    </div>
  );
}

/* ================= SHELL ================= */
export default function ToastReportV3() {
  const [mode, setMode] = useState("ops");
  const [toastMsg, setToastMsg] = useState(null);
  const toast = (m) => { setToastMsg(m); setTimeout(() => setToastMsg(null), 2600); };

  return (
    <div style={{ background: C.bg, color: C.txt, fontFamily: font.body, height: "100vh", display: "flex", flexDirection: "column", overflow: "hidden", fontSize: 13 }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap');
        .tr-pulse { animation: trpulse 2s ease-out infinite }
        @keyframes trpulse { 0% { r: 8; opacity: .8 } 100% { r: 26; opacity: 0 } }
        .tr-in { animation: slidein .4s ease-out }
        @keyframes slidein { from { opacity: 0; transform: translateY(-8px) } to { opacity: 1; transform: none } }
        @media (prefers-reduced-motion: reduce) { .tr-pulse,.tr-in { animation: none } }
        ::-webkit-scrollbar { width: 8px } ::-webkit-scrollbar-thumb { background: ${C.line}; border-radius: 4px }
        .tr-row:hover { background: ${C.panelUp} }
        input::placeholder, textarea::placeholder { color: ${C.faint} }
        select { outline: none }
      `}</style>

      {/* top bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "7px 14px", borderBottom: `1px solid ${C.line}`, background: C.panel, flexShrink: 0 }}>
        <div style={{ fontFamily: font.disp, fontWeight: 700, fontSize: 19, letterSpacing: 1, color: C.ember }}>TOAST REPORT</div>
        {/* mode nav */}
        <div style={{ display: "flex", gap: 2, background: C.bg, borderRadius: 6, padding: 2 }}>
          {[["ops", "OPS"], ["news", "NEWSROOM 2"], ["watch", "WATCH 2"], ["archive", "ARCHIVE"], ["sources", "SOURCES"]].map(([k, l]) => (
            <div key={k} onClick={() => setMode(k)} style={{ fontFamily: font.disp, fontSize: 13, fontWeight: 700, letterSpacing: 1.5, padding: "5px 13px", borderRadius: 5, cursor: "pointer", background: mode === k ? C.panelUp : "transparent", color: mode === k ? C.ember : C.dim }}>{l}</div>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>FRI JUL 17 · 15:44 PDT</div>
        {[["GPU", C.ok], ["FEEDS", C.ok], ["SOCIAL", C.warn], ["BUILD", C.ok]].map(([k, col]) => (
          <div key={k} style={{ display: "flex", alignItems: "center", gap: 4, fontFamily: font.mono, fontSize: 9.5, color: C.dim }}>
            <span style={{ width: 6, height: 6, borderRadius: 99, background: col }} />{k}
          </div>
        ))}
        <div style={{ fontFamily: font.mono, fontSize: 9.5, padding: "3px 7px", border: `1px solid ${C.warn}`, color: C.warn, borderRadius: 3 }}>RED FLAG →20:00</div>
      </div>

      {mode === "ops" && <OpsMode toast={toast} />}
      {mode === "news" && <NewsroomMode toast={toast} />}
      {mode === "watch" && <WatchMode />}
      {mode === "archive" && <ArchiveMode toast={toast} />}
      {mode === "sources" && <SourcesMode toast={toast} />}

      {/* toast */}
      {toastMsg && (
        <div className="tr-in" style={{ position: "fixed", bottom: 18, left: "50%", transform: "translateX(-50%)", background: C.panelUp, border: `1px solid ${C.ok}66`, color: C.txt, fontFamily: font.mono, fontSize: 11, padding: "9px 16px", borderRadius: 7, boxShadow: "0 8px 30px #000c", zIndex: 50 }}>
          ✓ {toastMsg}
        </div>
      )}
    </div>
  );
}
