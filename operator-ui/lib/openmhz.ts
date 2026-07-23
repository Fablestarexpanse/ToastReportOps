/** OpenMHz client — fetched DIRECTLY from the browser, not proxied.
 *
 * OpenMHz publishes an open API (`Access-Control-Allow-Origin: *`) and its
 * media files play cross-origin in an <audio> element. The browser is exactly
 * the client they serve, so the honest architecture is to let the operator's
 * own browser talk to them — no backend proxy, no user-agent games. The
 * backend owns only OUR data (the channel directory, the county board).
 *
 * Calls arrive ~30 s behind live as finished per-transmission clips — near-live
 * scanning, played in arrival order by the SCAN engine in radio-mode.
 */

const API = "https://api.openmhz.com";

export type OmzSystem = {
  shortName: string;
  name: string;
  county: string | null;
  state: string | null;
  active: boolean;
  lastActive: string | null;
  callAvg: number | null;
};

export type OmzTalkgroup = { num: number; alpha: string; description: string };

export type OmzCall = {
  id: string;
  system: string;
  tg: number;
  time: string; // ISO
  len: number; // seconds
  url: string; // m4a on their media host — playable in <audio>
};

export async function fetchOmzSystems(): Promise<OmzSystem[]> {
  const res = await fetch(`${API}/systems`);
  if (!res.ok) throw new Error(`openmhz systems: ${res.status}`);
  const d = await res.json();
  const systems: Record<string, unknown>[] = d.systems ?? [];
  return systems
    .map((s) => ({
      shortName: String(s.shortName ?? ""),
      name: String(s.name ?? s.shortName ?? ""),
      county: (s.county as string) || null,
      state: (s.state as string) || null,
      active: Boolean(s.active),
      lastActive: (s.lastActive as string) || null,
      callAvg: typeof s.callAvg === "number" ? s.callAvg : null,
    }))
    .filter((s) => s.shortName);
}

/** Active California systems, busiest first. */
export async function fetchOmzCaliforniaSystems(): Promise<OmzSystem[]> {
  const all = await fetchOmzSystems();
  return all
    .filter((s) => s.active && s.state === "CA")
    .sort((a, b) => (b.callAvg ?? 0) - (a.callAvg ?? 0));
}

export async function fetchOmzTalkgroups(system: string): Promise<OmzTalkgroup[]> {
  const res = await fetch(`${API}/${encodeURIComponent(system)}/talkgroups`);
  if (!res.ok) throw new Error(`openmhz talkgroups: ${res.status}`);
  const d = await res.json();
  // their payload is a dict keyed by talkgroup number, not a list
  const tg = d.talkgroups ?? {};
  const list: OmzTalkgroup[] = Object.values(tg as Record<string, Record<string, unknown>>).map(
    (t) => ({
      num: Number(t.num),
      alpha: String(t.alpha ?? t.num),
      description: String(t.description ?? ""),
    }),
  );
  return list.sort((a, b) => a.alpha.localeCompare(b.alpha));
}

/** A talkgroup that reads fire-related — used to prefilter the picker. */
export function looksFire(t: OmzTalkgroup): boolean {
  const s = `${t.alpha} ${t.description}`.toUpperCase();
  return /FIRE|\bFD\b|WILDLAND|BRUSH|ENGINE|BATTALION|FIREGROUND/.test(s);
}

export async function fetchOmzCalls(
  system: string,
  talkgroups: number[] = [],
): Promise<OmzCall[]> {
  const filter = talkgroups.length
    ? `?filter-type=talkgroup&filter-code=${talkgroups.join(",")}`
    : "";
  const res = await fetch(`${API}/${encodeURIComponent(system)}/calls${filter}`);
  if (!res.ok) throw new Error(`openmhz calls: ${res.status}`);
  const d = await res.json();
  const calls: Record<string, unknown>[] = d.calls ?? [];
  return calls
    .map((c) => ({
      id: String(c._id ?? ""),
      system,
      tg: Number(c.talkgroupNum ?? 0),
      time: String(c.time ?? ""),
      len: typeof c.len === "number" ? c.len : 0,
      url: String(c.url ?? ""),
    }))
    .filter((c) => c.id && c.url);
}
