"use client";

import { useEffect, useState } from "react";
import { Analytics, fetchAnalytics } from "@/lib/api";
import { C, TIER, font } from "@/lib/theme";
import { leadTime } from "@/lib/time";

/* ANALYTICS — what the ledger says about itself.

   Every figure is counted from stored rows. Rates carry their sample size,
   because "50% corroborated" over two facts is not the same claim as over two
   thousand, and a rate over nothing is unknown rather than zero. */

function Stat({
  label,
  value,
  sub,
  color = C.txt,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 7, padding: "10px 12px" }}>
      <div style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, letterSpacing: 1 }}>{label}</div>
      <div style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, color, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontFamily: font.mono, fontSize: 8.5, color: C.dim, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function Bars({
  rows,
  colorFor,
}: {
  rows: { label: string; n: number; note?: string }[];
  colorFor?: (label: string) => string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  return (
    <div>
      {rows.map((r) => (
        <div key={r.label} style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0" }}>
          <span style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, width: 118, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {r.label}
          </span>
          <div style={{ flex: 1, height: 9, background: C.line + "66", borderRadius: 3, overflow: "hidden" }}>
            <div
              style={{
                width: `${(r.n / max) * 100}%`,
                height: "100%",
                background: colorFor ? colorFor(r.label) : C.ember,
                borderRadius: 3,
              }}
            />
          </div>
          <span style={{ fontFamily: font.mono, fontSize: 9.5, color: C.txt, width: 44, textAlign: "right" }}>
            {r.n.toLocaleString()}
          </span>
          {r.note && (
            <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint, width: 60 }}>{r.note}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: "12px 14px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 9 }}>
        <span style={{ fontFamily: font.disp, fontSize: 12, letterSpacing: 2, color: C.dim }}>{title}</span>
        {hint && <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>{hint}</span>}
      </div>
      {children}
    </div>
  );
}

const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);

const fmtLead = (s: number) =>
  s >= 86_400 ? `${(s / 86_400).toFixed(1)}d` : s >= 3_600 ? `${(s / 3_600).toFixed(1)}h` : `${Math.round(s / 60)}m`;

/* Multi-fire growth overlay: one polyline per fire, each normalized to the
   shared time span and the biggest fire's scale — SVG, no library. */
const CURVE_COLORS = [C.emberHot, C.blue, C.ok, C.violet, C.warn];

function GrowthOverlay({ curves }: { curves: Analytics["growth_curves"] }) {
  const W = 560;
  const H = 130;
  const all = curves.flatMap((c) => c.points);
  if (!all.length) return null;
  const t0 = Math.min(...all.map((p) => +new Date(p.t)));
  const t1 = Math.max(...all.map((p) => +new Date(p.t)));
  const vMax = Math.max(...all.map((p) => p.acres), 1);
  const x = (t: string) => (t1 > t0 ? ((+new Date(t) - t0) / (t1 - t0)) * (W - 8) + 4 : W / 2);
  const y = (a: number) => H - 6 - (a / vMax) * (H - 16);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
        {curves.map((c, i) => (
          <polyline
            key={c.name}
            fill="none"
            stroke={CURVE_COLORS[i % CURVE_COLORS.length]}
            strokeWidth={1.8}
            points={c.points.map((p) => `${x(p.t).toFixed(1)},${y(p.acres).toFixed(1)}`).join(" ")}
          />
        ))}
      </svg>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 4 }}>
        {curves.map((c, i) => (
          <span key={c.name} style={{ fontFamily: font.mono, fontSize: 8.5, color: CURVE_COLORS[i % CURVE_COLORS.length] }}>
            ━ {c.name} ({Math.round(c.points[c.points.length - 1].acres).toLocaleString()} ac)
          </span>
        ))}
      </div>
    </div>
  );
}

export default function AnalyticsMode() {
  const [data, setData] = useState<Analytics | null>(null);
  const [days, setDays] = useState(14);

  useEffect(() => {
    let alive = true;
    fetchAnalytics(days)
      .then((d) => alive && setData(d))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [days]);

  if (!data)
    return (
      <div style={{ flex: 1, padding: 18, fontFamily: font.mono, fontSize: 10, color: C.faint }}>
        loading analytics…
      </div>
    );

  const t = data.totals;
  const q = data.quality;
  const peak = Math.max(1, ...data.daily.map((d) => d.facts));

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
        <span style={{ fontFamily: font.disp, fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>ANALYTICS</span>
        <span style={{ fontFamily: font.mono, fontSize: 10, color: C.dim }}>
          counted from the ledger — no estimates
        </span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 5 }}>
          {[7, 14, 30, 90].map((d) => (
            <span
              key={d}
              onClick={() => setDays(d)}
              style={{
                fontFamily: font.mono,
                fontSize: 9,
                padding: "3px 9px",
                borderRadius: 4,
                cursor: "pointer",
                border: `1px solid ${days === d ? C.ember : C.line}`,
                color: days === d ? C.ember : C.dim,
              }}
            >
              {d}d
            </span>
          ))}
        </span>
      </div>

      {/* headline counters */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(150px,1fr))", gap: 9, marginBottom: 14 }}>
        <Stat label="FACTS IN LEDGER" value={t.facts.toLocaleString()} sub={`${q.superseded} superseded`} />
        <Stat
          label="ACTIVE FIRES"
          value={String(t.active_incidents)}
          sub={`${t.incidents} tracked all-time`}
          color={C.emberHot}
        />
        <Stat
          label="ACRES (ACTIVE)"
          value={Math.round(t.acres_tracked).toLocaleString()}
          sub="latest non-superseded values"
        />
        <Stat label="SOURCES ON" value={`${t.sources_enabled}/${t.sources_total}`} />
        <Stat label="REPORTS WRITTEN" value={String(t.drafts)} sub={`${t.released} released`} />
        <Stat
          label="AVG CONFIDENCE"
          value={pct(q.avg_confidence)}
          sub={`${q.assessed} assessed`}
          color={C.ok}
        />
        <Stat
          label={`FIRES ${data.season.year}`}
          value={String(data.season.fires_ytd)}
          sub="year to date"
          color={C.ember}
        />
        <Stat
          label={`ACRES ${data.season.year}`}
          value={Math.round(data.season.acres_ytd).toLocaleString()}
          sub="peak acreage summed, YTD"
          color={C.emberHot}
        />
      </div>

      {/* -------- the record board + the race we exist to win -------- */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))", gap: 12, marginBottom: 12 }}>
        <Section title="🏆 RECORD BOARD" hint="real rows, never estimates">
          <div style={{ display: "grid", gap: 6 }}>
            {([
              ...(data.records.biggest_fire
                ? [{ label: "BIGGEST FIRE", value: `${data.records.biggest_fire.name} — ${Math.round(data.records.biggest_fire.acres).toLocaleString()} ac`, color: C.emberHot }]
                : []),
              ...(data.records.fastest_growth
                ? [{ label: "FASTEST RUN", value: `${data.records.fastest_growth.name} — ${Math.round(data.records.fastest_growth.acres_per_hour).toLocaleString()} ac/hr`, color: C.evac }]
                : []),
              ...(data.records.longest_burning
                ? [{ label: "LONGEST BURNING", value: `${data.records.longest_burning.name} — day ${data.records.longest_burning.days + 1}`, color: C.warn }]
                : []),
              ...(data.records.busiest_day
                ? [{ label: "BUSIEST LEDGER DAY", value: `${data.records.busiest_day.day} — ${data.records.busiest_day.facts.toLocaleString()} facts`, color: C.blue }]
                : []),
              ...(data.records.most_corroborated
                ? [{ label: "MOST-CONFIRMED FACT", value: `${data.records.most_corroborated.n} sources agree${data.records.most_corroborated.name ? ` (${data.records.most_corroborated.name})` : ""}`, color: C.ok }]
                : []),
            ] as { label: string; value: string; color: string }[])
              .map((r) => (
                <div key={r.label} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                  <span style={{ fontFamily: font.mono, fontSize: 8, color: C.faint, letterSpacing: 1, width: 130, flexShrink: 0 }}>
                    {r.label}
                  </span>
                  <span style={{ fontFamily: font.disp, fontSize: 13, fontWeight: 700, color: r.color }}>
                    {r.value}
                  </span>
                </div>
              ))}
          </div>
        </Section>

        <Section
          title="⚡ WE KNEW FIRST"
          hint={`ledger vs first official record · ${data.detection.beat_count} of ${data.detection.measured} fires beaten`}
        >
          {data.detection.beats.length ? (
            data.detection.beats.map((b) => (
              <div key={b.name} style={{ display: "flex", gap: 8, alignItems: "baseline", padding: "3px 0" }}>
                <span style={{ fontFamily: font.disp, fontSize: 13, fontWeight: 700, color: C.ok }}>
                  +{fmtLead(b.lead_s)}
                </span>
                <span style={{ fontSize: 11 }}>{b.name}</span>
                <span style={{ fontFamily: font.mono, fontSize: 8, color: C.faint }}>
                  ahead of the first official record
                </span>
              </div>
            ))
          ) : (
            <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.faint }}>
              no beats measured yet — watch/radio signals are how this number grows
            </div>
          )}
          <div style={{ fontFamily: font.mono, fontSize: 7.5, color: C.faint, marginTop: 6 }}>
            honest caveat: fires first learned FROM an official listing count as ties, not beats
          </div>
        </Section>

        <Section title="🏁 CONTAINMENT RACE" hint="active fires, race to 100%">
          {data.containment_race.map((f) => (
            <div key={f.name} style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0" }}>
              <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, width: 110, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {f.name}
              </span>
              <div style={{ flex: 1, height: 8, background: C.line + "66", borderRadius: 3, overflow: "hidden" }}>
                <div
                  style={{
                    width: `${f.pct}%`,
                    height: "100%",
                    background: f.pct >= 90 ? C.ok : f.pct >= 50 ? C.warn : C.emberHot,
                    borderRadius: 3,
                  }}
                />
              </div>
              <span style={{ fontFamily: font.mono, fontSize: 9, color: C.txt, width: 34, textAlign: "right" }}>
                {Math.round(f.pct)}%
              </span>
            </div>
          ))}
        </Section>

        {data.growth_curves.length > 0 && (
          <Section title="📈 GROWTH CURVES" hint="largest active fires — supersession IS the history">
            <GrowthOverlay curves={data.growth_curves} />
          </Section>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))", gap: 12 }}>
        <Section title="INGEST VOLUME" hint={`last ${data.window_days} days`}>
          {data.daily.length ? (
            <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 96 }}>
              {data.daily.map((d) => (
                <div
                  key={d.day}
                  title={`${d.day}: ${d.facts} facts from ${d.sources} sources`}
                  style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", height: "100%" }}
                >
                  <div
                    style={{
                      height: `${(d.facts / peak) * 100}%`,
                      background: C.ember,
                      borderRadius: "2px 2px 0 0",
                      minHeight: 2,
                    }}
                  />
                  <div style={{ fontFamily: font.mono, fontSize: 7, color: C.faint, textAlign: "center", marginTop: 3 }}>
                    {d.day.slice(8)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ fontFamily: font.mono, fontSize: 9.5, color: C.faint }}>no ingest in this window</div>
          )}
        </Section>

        <Section title="LEDGER QUALITY" hint="rates carry their sample size">
          <div style={{ display: "grid", gap: 7 }}>
            {[
              {
                label: "ATTRIBUTED TO A FIRE",
                value: pct(q.attribution_rate),
                sub: `${q.attributed} of ${q.attributed + q.unattributed_open} · ${q.triaged} triaged out`,
                color: C.ok,
              },
              {
                label: "CORROBORATED",
                value: pct(q.corroboration_rate),
                sub: `${q.corroborated} of ${q.assessed} assessed`,
                color: C.blue,
              },
              {
                label: "AWAITING ATTRIBUTION",
                value: String(q.unattributed_open),
                sub: "open in the adjudication queue",
                color: q.unattributed_open > 50 ? C.warn : C.dim,
              },
            ].map((r) => (
              <div key={r.label} style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, width: 150 }}>{r.label}</span>
                <span style={{ fontFamily: font.disp, fontSize: 17, fontWeight: 700, color: r.color }}>
                  {r.value}
                </span>
                <span style={{ fontFamily: font.mono, fontSize: 8.5, color: C.faint }}>{r.sub}</span>
              </div>
            ))}
          </div>
        </Section>

        <Section title="WHAT WE LEARN" hint="facts by kind">
          <Bars rows={data.by_kind.map((k) => ({ label: k.kind, n: k.n }))} />
        </Section>

        <Section title="WHO TELLS US" hint="facts by source trust tier">
          <Bars
            rows={data.by_tier.map((t2) => ({ label: `T${t2.tier}`, n: t2.n }))}
            colorFor={(l) => TIER[Number(l.slice(1))] ?? C.ember}
          />
        </Section>

        <Section title="COVERAGE BY COUNTY" hint="incidents tracked">
          <Bars
            rows={data.by_county.map((c) => ({
              label: c.county,
              n: c.incidents,
              note: c.active ? `${c.active} active` : "",
            }))}
          />
        </Section>

        <Section title="TOP SOURCES" hint="volume, with measured performance">
          {data.top_sources.map((s) => (
            <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", borderBottom: `1px solid ${C.line}44` }}>
              <span style={{ fontFamily: font.mono, fontSize: 9.5, color: TIER[s.tier] }}>T{s.tier}</span>
              <span style={{ fontSize: 10.5, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {s.name}
              </span>
              {s.lead_time_median_s != null && (
                <span
                  title="median lead over the official record"
                  style={{ fontFamily: font.mono, fontSize: 8.5, color: s.lead_time_median_s > 0 ? C.ok : C.dim }}
                >
                  {leadTime(s.lead_time_median_s)}
                </span>
              )}
              {s.superseded_rate != null && s.superseded_rate > 0 && (
                <span title="corrected by a higher tier" style={{ fontFamily: font.mono, fontSize: 8.5, color: C.warn }}>
                  {Math.round(s.superseded_rate * 100)}% corr
                </span>
              )}
              <span style={{ fontFamily: font.mono, fontSize: 9.5, color: C.dim, width: 40, textAlign: "right" }}>
                {s.facts}
              </span>
            </div>
          ))}
        </Section>

        <Section title="🗑 NOISE EATEN" hint={`last ${data.window_days} days — dedup earning its keep`}>
          <div style={{ display: "grid", gap: 7 }}>
            {[
              { label: "RAW ITEMS INGESTED", value: data.dedup.envelopes.toLocaleString(), color: C.dim },
              { label: "BECAME FACTS", value: data.dedup.facts.toLocaleString(), color: C.txt },
              {
                label: "NEAR-DUPLICATES FLAGGED",
                value: data.dedup.near_duplicates_flagged.toLocaleString(),
                color: C.ok,
              },
            ].map((r) => (
              <div key={r.label} style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, width: 175 }}>{r.label}</span>
                <span style={{ fontFamily: font.disp, fontSize: 17, fontWeight: 700, color: r.color }}>{r.value}</span>
              </div>
            ))}
          </div>
        </Section>

        <Section title="📰 NEWSROOM PACE" hint={`last ${data.window_days} days`}>
          <div style={{ display: "grid", gap: 7 }}>
            {[
              { label: "DRAFTS WRITTEN", value: String(data.newsroom.drafted), color: C.txt },
              { label: "PUBLISHED", value: String(data.newsroom.published), color: C.ok },
              { label: "CHANNEL DELIVERIES", value: String(data.newsroom.deliveries), color: C.blue },
              {
                label: "MEDIAN DRAFT → APPROVE",
                value: data.newsroom.median_hours_to_approve != null ? `${data.newsroom.median_hours_to_approve}h` : "—",
                color: C.dim,
              },
            ].map((r) => (
              <div key={r.label} style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, width: 175 }}>{r.label}</span>
                <span style={{ fontFamily: font.disp, fontSize: 17, fontWeight: 700, color: r.color }}>{r.value}</span>
              </div>
            ))}
          </div>
        </Section>

        <Section title="✈ SKY TRAFFIC" hint={`aircraft near fires, last ${data.window_days} days`}>
          <div style={{ display: "grid", gap: 7 }}>
            {([
              { label: "POSITION SIGHTINGS", value: data.aircraft.sightings.toLocaleString(), color: C.txt },
              { label: "DISTINCT AIRCRAFT", value: data.aircraft.hulls.toLocaleString(), color: C.blue },
              ...(data.aircraft.busiest
                ? [
                    {
                      label: "HARDEST-WORKING HULL",
                      value: `${data.aircraft.busiest.ident} (${data.aircraft.busiest.n} fixes)`,
                      color: C.ok,
                    },
                  ]
                : []),
            ] as { label: string; value: string; color: string }[])
              .map((r) => (
                <div key={r.label} style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                  <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, width: 175 }}>{r.label}</span>
                  <span style={{ fontFamily: font.disp, fontSize: 16, fontWeight: 700, color: r.color }}>{r.value}</span>
                </div>
              ))}
          </div>
        </Section>

        <Section title="📻 SCANNER" hint={`own SDR, last ${data.window_days} days`}>
          <div style={{ display: "grid", gap: 7 }}>
            {[
              { label: "CALLS CAPTURED", value: String(data.radio.calls), color: C.txt },
              {
                label: "AIRTIME",
                value: data.radio.airtime_s >= 60 ? `${Math.round(data.radio.airtime_s / 60)}m` : `${Math.round(data.radio.airtime_s)}s`,
                color: C.dim,
              },
              { label: "TRANSCRIBED", value: String(data.radio.transcribed), color: C.blue },
              { label: "FED WATCH", value: String(data.radio.watch_hits), color: C.violet },
            ].map((r) => (
              <div key={r.label} style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                <span style={{ fontFamily: font.mono, fontSize: 9, color: C.dim, width: 175 }}>{r.label}</span>
                <span style={{ fontFamily: font.disp, fontSize: 16, fontWeight: 700, color: r.color }}>{r.value}</span>
              </div>
            ))}
          </div>
        </Section>

        {data.season.weekly_starts.length > 1 && (
          <Section title="🔥 FIRE STARTS BY WEEK" hint="last 12 weeks">
            <Bars rows={data.season.weekly_starts.map((w) => ({ label: w.week, n: w.fires }))} />
          </Section>
        )}
      </div>
    </div>
  );
}
