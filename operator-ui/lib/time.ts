/* Time display — one implementation for the whole tool. Fire information is
   time-critical and bi-temporal: when a thing happened (observed/published) is a
   different fact from when we received it (ingested), and the operator needs the
   gap at a glance.

   Two independent choices, because they answer different questions:
     ZONE   utc | local  — which clock the reading is on
     FORMAT 24h | 12h    — how that reading is written
   24-hour stays the default: a fire log is read alongside dispatch and agency
   traffic, which is 24-hour. The zone marker (Z / L) is never dropped in either
   format — a time without its zone is how you act on the wrong hour. */

const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export type TzMode = "utc" | "local";
export type ClockMode = "24h" | "12h";

let tzMode: TzMode = "utc";
let clockMode: ClockMode = "24h";

export function setTzMode(mode: TzMode) {
  tzMode = mode;
}
export function getTzMode(): TzMode {
  return tzMode;
}
export function setClockMode(mode: ClockMode) {
  clockMode = mode;
}
export function getClockMode(): ClockMode {
  return clockMode;
}

function parts(iso: string, mode: TzMode = tzMode) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return mode === "utc"
    ? {
        day: d.getUTCDate(),
        mon: MON[d.getUTCMonth()],
        year: d.getUTCFullYear(),
        hh: d.getUTCHours(),
        mm: d.getUTCMinutes(),
        suffix: "Z",
      }
    : {
        day: d.getDate(),
        mon: MON[d.getMonth()],
        year: d.getFullYear(),
        hh: d.getHours(),
        mm: d.getMinutes(),
        suffix: "L",
      };
}

const p2 = (n: number) => String(n).padStart(2, "0");

/** The time itself, in the operator's chosen format, always with its zone.
 *  24h: "0137Z"      12h: "1:37AM Z" */
function clock(hh: number, mm: number, suffix: string): string {
  if (clockMode === "24h") return `${p2(hh)}${p2(mm)}${suffix}`;
  const ampm = hh < 12 ? "AM" : "PM";
  const h12 = hh % 12 === 0 ? 12 : hh % 12;
  return `${h12}:${p2(mm)}${ampm} ${suffix}`;
}

/** Date + time: "20JUL 0137Z" or "20JUL 1:37AM Z" (adds year if not this year). */
export function fmtStamp(iso: string | null | undefined): string {
  const p = iso ? parts(iso) : null;
  if (!p) return "—";
  const year = p.year !== new Date().getFullYear() ? String(p.year).slice(2) : "";
  return `${p2(p.day)}${p.mon}${year} ${clock(p.hh, p.mm, p.suffix)}`;
}

/** Full stamp for tooltips, always ending in the raw ISO the ledger stores —
 *  the formatted part is a convenience, the ISO is the record. */
export function fmtFull(iso: string | null | undefined): string {
  const p = iso ? parts(iso) : null;
  if (!p || !iso) return "unknown time";
  return `${p2(p.day)}${p.mon}${p.year} ${clock(p.hh, p.mm, p.suffix)}  (${iso})`;
}

/** Compact age: "4m" "3h" "2d". Empty string when unknown. */
export function age(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s}s`;
  // floor, not round: Math.round(59.5) is 60, which fell past the minute branch
  // and then floored to 0 hours — an hour-old fact rendered "0h old" and, via
  // ageColor, still looked fresh.
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(s / 3600);
  if (h < 48) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

/** Age color: fresh → ok, aging → warn, old → faint. */
export function ageColor(
  iso: string | null | undefined,
  C: { ok: string; warn: string; dim: string; faint: string },
  now: number = Date.now(),
): string {
  if (!iso) return C.faint;
  const h = (now - new Date(iso).getTime()) / 3_600_000;
  if (isNaN(h)) return C.faint;
  if (h < 3) return C.ok;
  if (h < 12) return C.warn;
  if (h < 48) return C.dim;
  return C.faint;
}

/** The bi-temporal gap: how long after the event we learned about it. */
export function lag(observed?: string | null, ingested?: string | null): string {
  if (!observed || !ingested) return "";
  const gap = new Date(ingested).getTime() - new Date(observed).getTime();
  if (isNaN(gap) || gap < 60_000) return "";
  return age(observed, new Date(ingested).getTime());
}

/** Median lead over the official record, as one phrasing everywhere.
 *
 * SOURCES bucketed to minutes under an hour while ANALYTICS always printed
 * hours to one decimal, so the same source read "+2m" on one screen and "+0.0h"
 * on another. A trust number that changes shape by screen is a trust number the
 * operator stops trusting. */
export function leadTime(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const sign = seconds > 0 ? "+" : "";
  if (Math.abs(seconds) >= 3600) return `${sign}${(seconds / 3600).toFixed(1)}h`;
  return `${sign}${Math.round(seconds / 60)}m`;
}
