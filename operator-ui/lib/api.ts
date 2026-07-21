/** API client — everything proxied through /api → FastAPI on 127.0.0.1:8400. */

export type Incident = {
  id: string;
  canonical_name: string;
  status: "candidate" | "active" | "contained" | "frozen";
  lat: number | null;
  lon: number | null;
  county: string | null;
  irwin_id: string | null;
  calfire_id: string | null;
  created_at: string;
  updated_at: string;
  fact_count: number;
  last_observed: string | null;
  acreage: number | null;
  containment_pct: number | null;
  followed: number;
  started_at: string | null;
  evac_status: string | null;
};

/** Fires under an active evacuation order/warning — the map's alarm color. */
export const hasActiveEvac = (i: { evac_status: string | null }) =>
  i.evac_status === "order" || i.evac_status === "warning" || i.evac_status === "shelter_in_place";

export type Fact = {
  id: string;
  incident_id: string | null;
  incident_name?: string | null;
  incident_county?: string | null;
  source_id: string;
  source_name: string;
  tier: number;
  content: string;
  kind: string;
  acreage: number | null;
  containment_pct: number | null;
  evac_status: string | null;
  structured: Record<string, unknown>;
  confidence: number;
  observed_at: string;
  ingested_at: string;
  lat: number | null;
  lon: number | null;
  geo_method: string | null;
  supersedes: string | null;
  superseded_by: string | null;
  starred: number;
  stale: boolean;
  stale_s: number | null;
  effective_confidence: number | null;
  corroborating_sources: number | null;
  source_url: string | null;
  /** envelope kind behind this fact: report | article | video | image … */
  media_kind: string | null;
};

export type SourceStats = {
  lead_time_median_s: number | null;
  superseded_rate: number | null;
  yield_facts_per_day: number | null;
  suggested_tier: number | null;
  stats_computed_at: string | null;
};

export type Source = {
  id: string;
  name: string;
  type: string;
  adapter: string;
  tier: number;
  schedule: string | null;
  enabled: number;
  tos_notes: string | null;
  cursor: string | null;
  fact_count: number;
  last_fact_at: string | null;
} & SourceStats;

export type Health = { api: boolean; queue: Record<string, number> };

export type DeadJob = {
  id: string;
  stage: string;
  payload: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
};

export type DeadJobs = {
  by_stage: { stage: string; n: number; latest: string; recent: number }[];
  jobs: DeadJob[];
  /** stages safe to bulk-retry — publish is excluded (duplicate-post risk) */
  recoverable: string[];
};

export const fetchDeadJobs = () => get<DeadJobs>("/jobs/dead");

export const requeueDeadJobs = (stage?: string) =>
  post<{ requeued: number }>("/jobs/requeue", stage ? { stage } : {});

export type WatchCandidate = {
  key: string;
  name: string;
  source_count: number;
  fact_count: number;
  latest_observed: string;
  tiers: number[];
  lat: number | null;
  lon: number | null;
  county: string | null;
  score: number;
  state: "watching" | "corroborating";
  fact_ids: string[];
  samples: { content: string; tier: number; observed_at: string }[];
  verdict: WatchVerdict | null;
  geo_source: "reported" | "named_feature" | null;
  geo_place: string | null;
};

export type WatchVerdict = {
  verdict: "active_new" | "historical" | "not_a_fire" | "uncertain";
  confidence: number;
  reasoning: string;
  evidence_quote: string;
  in_california: boolean | null;
  suggested_action: "keep" | "archived" | "ignored";
  checked_at: string;
};

export type WatchState = "ignored" | "archived" | "keep";

export async function triageWatch(key: string, state: WatchState): Promise<void> {
  const res = await fetch("/api/watch/triage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, state }),
  });
  if (!res.ok) throw new Error(`triage failed (${res.status})`);
}

export async function classifyWatch(key: string): Promise<WatchVerdict> {
  const res = await fetch("/api/watch/classify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export const fetchWatch = () =>
  get<{ candidates: WatchCandidate[] }>("/watch").then((r) => r.candidates);

export async function promoteCandidate(
  key: string,
): Promise<{ incident_id: string; name: string; facts_attributed: number }> {
  const res = await fetch("/api/watch/promote", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export type Draft = {
  id: string;
  incident_id: string | null;
  incident_name: string | null;
  kind: string;
  parent_id: string | null;
  status: "draft" | "needs_review" | "approved" | "published" | "rejected";
  title: string | null;
  window_start: string | null;
  window_end: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
  cited_count: number;
};

export type CitedFact = {
  id: string;
  content: string;
  kind: string;
  tier: number;
  observed_at: string;
  source_name: string;
};

export type Publication = {
  id: string;
  channel: string;
  /** 'unknown' means we never heard back — it may or may not have gone out */
  status: "queued" | "sent" | "failed" | "unknown";
  external_url: string | null;
  receipt: string | null;
  sent_at: string | null;
};

export type DraftDetail = {
  draft: Draft & { body_md: string; cited_fact_ids: string };
  cited_facts: CitedFact[];
  publications: Publication[];
};

export type ResearchJob = {
  key: string;
  query: string;
  state: "starting" | "researching" | "parked" | "failed";
  incident_id: string | null;
  incident_name: string | null;
  since: string;
  error: string | null;
};

export const fetchDrafts = () =>
  get<{ drafts: Draft[]; research_jobs: ResearchJob[] }>("/drafts");
export const fetchDraft = (id: string) => get<DraftDetail>(`/drafts/${id}`);

async function post<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    markConnection(false);
    throw e;
  }
  markConnection(true);
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export const generateDraft = (incidentId: string, window?: { window_start?: string }) =>
  post<{ draft_id: string; title: string; facts_used: number; cited: number }>(
    `/incidents/${incidentId}/draft`,
    window ?? {},
  );

export const transitionDraft = (id: string, status: string) =>
  post<{ status: string }>(`/drafts/${id}/status`, { status });

export const createSocialDraft = (id: string) =>
  post<{ draft_id: string; post_chars: number; short_chars: number }>(`/drafts/${id}/social`, {});

export const cancelResearch = (key: string) =>
  post<{ cancelled_jobs: number }>("/research/cancel", { key });

export async function saveDraft(id: string, body: { body_md?: string; title?: string }): Promise<void> {
  const res = await fetch(`/api/drafts/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`save: ${res.status}`);
}

export type AdjudicationCandidate = {
  incident_id: string;
  incident_name: string;
  score: number;
  name_score: number;
  geo_score: number;
  time_score: number;
};

export type AdjudicationItem = {
  fact: Fact;
  candidates: AdjudicationCandidate[];
  claimed_name: string | null;
  unknown_fire: boolean;
};

export type TriageState = "not_incident" | "watch" | "deferred";

export async function triageFact(factId: string, state: TriageState): Promise<void> {
  const res = await fetch(`/api/adjudication/${factId}/triage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
}

export async function triageBulk(
  factIds: string[],
  state: TriageState,
): Promise<{ triaged: number }> {
  const res = await fetch("/api/adjudication/triage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fact_ids: factIds, state }),
  });
  if (!res.ok) throw new Error(`bulk triage failed (${res.status})`);
  return res.json();
}

export const fetchDeferredCount = () =>
  get<{ deferred: number }>("/adjudication/deferred").then((r) => r.deferred);

export async function restoreDeferred(): Promise<number> {
  const res = await fetch("/api/adjudication/restore-deferred", { method: "POST" });
  if (!res.ok) throw new Error(`restore failed (${res.status})`);
  return (await res.json()).restored;
}

export type AnalyzeResult = {
  url: string;
  claims: number;
  dropped: number;
  prompt_version: string;
  facts: {
    fact_id: string;
    content: string;
    kind: string;
    attribution: string;
    top_candidate: { incident: string; score: number } | null;
  }[];
};

/* Backend reachability, tracked centrally.
 *
 * Every call in this file goes through get()/post(), so this is the one place
 * that knows whether the backend is answering. It matters more here than in a
 * normal app: PSPS outages and flaky links are this tool's stated operating
 * condition, so the workstation must degrade to stale-but-visible and SAY it is
 * stale — never silently show old numbers as if they were current, and never
 * replace the operator's screen with an error page because one poll failed. */
let connected = true;
let lastOkAt: string | null = null;
const listeners = new Set<() => void>();

export function connectionState() {
  return { connected, lastOkAt };
}

export function onConnectionChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function markConnection(ok: boolean) {
  if (ok) lastOkAt = new Date().toISOString();
  if (ok === connected) return;
  connected = ok;
  for (const fn of listeners) fn();
}

/** Does this response mean "the backend is not there", as opposed to "the
 *  backend answered and said no"?
 *
 *  A dead upstream behind the dev proxy surfaces as a 500, which is
 *  indistinguishable from a genuine API error on an arbitrary endpoint. So
 *  liveness is judged on /healthz — whose only job is to answer 200 — plus the
 *  gateway codes, which always mean the hop failed. Guessing from any 5xx would
 *  cry wolf on a single bad query; that is how an outage banner becomes noise. */
function meansUnreachable(path: string, status: number): boolean {
  if (path.startsWith("/healthz")) return true;
  return status === 502 || status === 503 || status === 504;
}

async function get<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { cache: "no-store" });
  } catch (e) {
    // network-level failure: the backend is down or unreachable
    markConnection(false);
    throw e;
  }
  if (!res.ok) {
    if (meansUnreachable(path, res.status)) markConnection(false);
    throw new Error(`${path}: ${res.status}`);
  }
  markConnection(true);
  return res.json();
}

/** Drop repeated ids from a server list.
 *
 * React keys must be unique, and a repeated row is a bug wherever it came from:
 * rendering the same fire or the same claim twice is wrong on screen before it
 * is wrong in React. Enforcing it here means no list component has to think
 * about it, and the warning names the endpoint so the next occurrence points at
 * its own cause instead of at a stack trace inside the renderer. */
function uniqueBy<T>(rows: T[], key: (row: T) => string, what: string): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const row of rows) {
    const k = key(row);
    if (seen.has(k)) {
      console.warn(`[toast] duplicate ${what} from the API, dropped:`, k, row);
      continue;
    }
    seen.add(k);
    out.push(row);
  }
  return out;
}

const byId = <T extends { id: string }>(rows: T[], what: string) =>
  uniqueBy(rows, (r) => r.id, `${what} id`);

export const fetchIncidents = () =>
  get<{ incidents: Incident[] }>("/incidents").then((r) => byId(r.incidents, "incident"));
export const fetchIncidentFacts = (id: string) =>
  get<{ facts: Fact[] }>(`/incidents/${id}/facts`).then((r) => byId(r.facts, "incident fact"));
export const fetchIntake = () =>
  get<{ facts: Fact[] }>("/intake?limit=80").then((r) => byId(r.facts, "intake fact"));
export const fetchSources = () => get<{ sources: Source[] }>("/sources").then((r) => r.sources);

export const fetchAdjudication = () =>
  get<{ queue: AdjudicationItem[] }>("/adjudication").then((r) =>
    // one row per fact, and one chip per candidate incident — a repeat in
    // either is a bug worth naming rather than a silent React key collision
    uniqueBy(
      r.queue.map((it) => ({
        ...it,
        candidates: uniqueBy(it.candidates, (c) => c.incident_id, "adjudication candidate"),
      })),
      (it) => it.fact.id,
      "adjudication row",
    ),
  );

export async function resolveAdjudication(factId: string, incidentId: string): Promise<void> {
  const res = await fetch(`/api/adjudication/${factId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ incident_id: incidentId }),
  });
  if (!res.ok) throw new Error(`resolve: ${res.status}`);
}

export async function analyzeUrl(url: string): Promise<AnalyzeResult> {
  const res = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export type FireSearchResult = {
  ref_type: string;
  ref_id: string;
  name: string;
  county: string | null;
  acreage: number | null;
  containment_pct: number | null;
  year: string | null;
  started_at: string | null;
  is_active: boolean;
  url: string | null;
  already_in_ledger: boolean;
};

export const searchFires = (q: string, year?: number) => {
  const params = new URLSearchParams({ q });
  if (year) params.set("year", String(year));
  return get<{ results: FireSearchResult[] }>(`/search/fires?${params}`).then((r) => r.results);
};

export async function importFire(refId: string): Promise<{ name: string; status: string; facts: number }> {
  const res = await fetch("/api/import/fire", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref_id: refId }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export type ProbeResult = {
  url: string;
  detected: string;
  adapter: string;
  config: Record<string, unknown>;
  suggested_tier: number;
  title: string | null;
  note: string;
  supported: boolean;
  warnings: string[];
};

export async function probeSource(url: string): Promise<ProbeResult> {
  const res = await fetch("/api/sources/probe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export async function createSource(body: {
  name: string;
  adapter: string;
  config: Record<string, unknown>;
  tier: number;
  type?: string;
  enabled?: boolean;
}): Promise<{ source_id: string }> {
  const res = await fetch("/api/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export type Reading = {
  value: number | null;
  prev: number | null;
  delta: number | null;
  observed_at: string | null;
  source_name?: string;
  tier?: number;
};

export type MetricValue = Reading & {
  /** the agency record (T1–T2) */
  official: Reading | null;
  /** press, social and scraped reporting (T3+) */
  reported: Reading | null;
  /** set only when the two disagree materially */
  divergence: { gap: number; pct: number; reported_higher: boolean } | null;
};

export type TextMetric = {
  content: string;
  observed_at: string;
  source_name: string;
  tier: number;
} | null;

export type ImpactFact = {
  id: string;
  content: string;
  evac_status: string | null;
  observed_at: string;
  tier: number;
  structured: string;
  source_name: string;
  /** agency instruction vs. a newsroom paraphrase of one */
  official: boolean;
  /** the issuing page — evac text must always travel with its deep-link */
  source_url: string | null;
};

export type IncidentIntel = {
  air_resources?: AirResource[];
  aircraft_observed?: number;
  metrics: {
    acreage: MetricValue;
    containment: MetricValue;
    start: TextMetric;
    cause: TextMetric;
  };
  confidence: {
    avg_confidence: number | null;
    source_count: number;
    corroborated_facts: number;
    fact_count: number;
  };
  situation: {
    kind: string;
    content: string;
    observed_at: string;
    source_name: string;
    tier: number;
    confidence: number;
  }[];
  resources: {
    metrics: Record<string, { value: number | string; observed_at: string; source: string; tier: number }>;
    notes: { content: string; observed_at: string; source: string; tier: number }[];
    /** one row per resource class: agency record vs. what reporting says */
    grid: {
      key: string;
      label: string;
      icon: string;
      official: { value: number | string; observed_at: string; source: string; tier: number } | null;
      reported: {
        value: number;
        observed_at: string;
        source: string;
        tier: number;
        quote: string;
      } | null;
    }[];
  };
  evac: ImpactFact[];
  road: ImpactFact[];
  impact_counts: {
    orders: number;
    warnings: number;
    shelter_in_place: number;
    lifted: number;
    unclassified: number;
    road_closures: number;
  };
};

export type TimelineEntry = {
  at: string;
  type: "event" | "fact";
  label: string;
  content?: string;
  tier?: number;
  source_name?: string;
  superseded?: boolean;
  detail?: string;
  actor?: string;
};

export type Note = {
  id: string;
  incident_id: string | null;
  body: string;
  done: number;
  created_at: string;
};

export async function fetchIntel(incidentId: string): Promise<IncidentIntel> {
  return get<IncidentIntel>(`/incidents/${incidentId}/intel`);
}

export async function fetchTimeline(incidentId: string): Promise<TimelineEntry[]> {
  const d = await get<{ timeline: TimelineEntry[] }>(`/incidents/${incidentId}/timeline`);
  return d.timeline;
}

export async function followIncident(incidentId: string, followed: boolean): Promise<void> {
  const res = await fetch(`/api/incidents/${incidentId}/follow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ followed }),
  });
  if (!res.ok) throw new Error(`follow failed (${res.status})`);
}

export async function fetchNotes(incidentId?: string): Promise<Note[]> {
  const q = incidentId ? `?incident_id=${incidentId}` : "";
  const d = await get<{ notes: Note[] }>(`/notes${q}`);
  return d.notes;
}

export async function addNote(body: string, incidentId?: string): Promise<void> {
  const res = await fetch("/api/notes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body, incident_id: incidentId }),
  });
  if (!res.ok) throw new Error(`note failed (${res.status})`);
}

export async function toggleNote(id: string): Promise<void> {
  await fetch(`/api/notes/${id}/toggle`, { method: "POST" });
}

export async function deleteNote(id: string): Promise<void> {
  await fetch(`/api/notes/${id}`, { method: "DELETE" });
}

export async function setSourceTier(id: string, tier: number): Promise<{ name: string }> {
  const res = await fetch(`/api/sources/${id}/tier`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tier }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export async function dismissTierSuggestion(id: string): Promise<void> {
  await fetch(`/api/sources/${id}/tier/dismiss`, { method: "POST" });
}

export async function recomputeSourceStats(): Promise<{ computed: number; suggestions: unknown[] }> {
  const res = await fetch("/api/sources/stats/recompute", { method: "POST" });
  if (!res.ok) throw new Error(`recompute failed (${res.status})`);
  return res.json();
}

/* Credentials. The API never returns secret values — only whether a field is
   set, a masked hint, and the last connection test. */
export type CredentialField = {
  name: string;
  label: string;
  secret: boolean;
  placeholder: string;
  set: boolean;
  masked: string;
  source: "operator" | "env" | null;
};

export type CredentialService = {
  id: string;
  label: string;
  why: string;
  signup_url: string;
  signup_label: string;
  notes: string;
  testable: boolean;
  configured: boolean;
  fields: CredentialField[];
  status: "ok" | "rejected" | "error" | "unconfigured" | "unchecked";
  message: string | null;
  checked_at: string | null;
};

export const fetchCredentials = () =>
  get<{ services: CredentialService[] }>("/credentials").then((r) => r.services);

export async function saveCredentials(
  credentials: Record<string, string>,
): Promise<CredentialService[]> {
  const res = await fetch("/api/credentials", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ credentials }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json().then((d) => d.services);
}

export async function testCredentials(
  service: string,
): Promise<{ status: string; message: string | null }> {
  const res = await fetch(`/api/credentials/${service}/test`, { method: "POST" });
  if (!res.ok) throw new Error(`test failed (${res.status})`);
  return res.json();
}

export type BackupStatus = {
  /** raw archive replication — the evidence behind the claims */
  raw_offsite?: number | null;
  raw_pending?: number | null;
  raw_pending_bytes?: number | null;
  snapshots: number;
  latest_at: string | null;
  latest: { file: string; bytes: number; verified: { counts: Record<string, number> } } | null;
  offsite_configured: boolean;
  at_risk: boolean;
  snapshot_dir: string;
};

export type OriginCheck = {
  incident_id: string;
  name?: string;
  lat: number | null;
  lon: number | null;
  verified: boolean;
  confidence: "high" | "medium" | "low" | "none";
  official?: {
    lat: number;
    lon: number;
    delta_km: number;
    name: string | null;
    county: string | null;
    url: string | null;
    updated: string | null;
  } | null;
  checks: { check: string; ok: boolean | null; detail: string }[];
};

export async function refreshResources(incidentId: string): Promise<{
  matched: boolean;
  recorded?: number;
  reason?: string;
  personnel?: number | null;
  match_distance_km?: number;
}> {
  const res = await fetch(`/api/incidents/${incidentId}/resources/refresh`, { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export const fetchOriginCheck = (incidentId: string) =>
  get<OriginCheck>(`/incidents/${incidentId}/origin-check`);

export type IncidentWeather = {
  available?: boolean;
  reason?: string;
  cached?: boolean;
  fetched_at?: string;
  observed_at?: string;
  station?: { id: string; name: string | null; distance_km: number | null };
  temperature_f?: number | null;
  relative_humidity?: number | null;
  wind_mph?: number | null;
  gust_mph?: number | null;
  wind_compass?: string | null;
  wind_degrees?: number | null;
  conditions?: string | null;
  red_flag?: boolean;
  critical?: boolean;
  alerts?: { event: string; severity: string; headline: string; ends: string | null; critical: boolean }[];
  source?: string;
};

export const fetchWeather = (incidentId: string, refresh = false) =>
  get<IncidentWeather>(`/incidents/${incidentId}/weather${refresh ? "?refresh=true" : ""}`);

export type Analytics = {
  window_days: number;
  totals: Record<string, number>;
  quality: {
    attributed: number;
    unattributed_open: number;
    triaged: number;
    attribution_rate: number | null;
    assessed: number;
    corroborated: number;
    corroboration_rate: number | null;
    avg_confidence: number | null;
    superseded: number;
  };
  daily: { day: string; facts: number; sources: number }[];
  by_kind: { kind: string; n: number }[];
  by_tier: { tier: number; n: number }[];
  by_county: { county: string; incidents: number; active: number }[];
  top_sources: {
    name: string;
    tier: number;
    facts: number;
    lead_time_median_s: number | null;
    superseded_rate: number | null;
  }[];
};

export const fetchAnalytics = (days = 14) => get<Analytics>(`/analytics?days=${days}`);

export async function starFact(factId: string, starred: boolean): Promise<void> {
  const res = await fetch(`/api/facts/${factId}/star`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ starred }),
  });
  if (!res.ok) throw new Error(`star failed (${res.status})`);
}

export type EditablePrompt = {
  id: string;
  label: string;
  purpose: string;
  version: string;
  edited: boolean;
  updated_at: string | null;
  template: string;
  default_template: string;
  placeholders: string[];
};

export const fetchPrompts = () =>
  get<{ prompts: EditablePrompt[] }>("/prompts").then((r) => r.prompts);

export async function savePrompt(id: string, template: string): Promise<EditablePrompt[]> {
  const res = await fetch(`/api/prompts/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json().then((d) => d.prompts);
}

export async function resetPrompt(id: string): Promise<EditablePrompt[]> {
  const res = await fetch(`/api/prompts/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`revert failed (${res.status})`);
  return res.json().then((d) => d.prompts);
}

export type PerimeterCollection = {
  type: string;
  features: {
    type: string;
    geometry: unknown;
    properties: {
      poly_IncidentName?: string;
      poly_GISAcres?: number;
      incident_id?: string | null;
      incident_name?: string;
    };
  }[];
  fetched_at?: string;
  mapped_latest?: string | null;
  matched?: number;
  count?: number;
  source?: string;
  note?: string;
  error?: string;
};

export const fetchPerimeters = () => get<PerimeterCollection>("/perimeters");

export type Hotspot = {
  lat: number;
  lon: number;
  observed_at: string;
  frp: number | null;      // fire radiative power, MW
  confidence: string | null; // FIRMS: n(ominal) | l(ow) | h(igh)
  attached: boolean;       // tied to a known incident
};

export type HotspotSet = {
  hotspots: Hotspot[];
  count: number;
  latest_pass: string | null;
  window_hours: number;
  note: string;
};

export const fetchHotspots = (hours = 24) =>
  get<HotspotSet>(`/hotspots?hours=${hours}`);

export type Aircraft = {
  hex: string;
  flight: string | null;
  type: string | null;
  descr: string | null;
  lat: number;
  lon: number;
  alt_ft: number | null;
  gs: number | null;
  track: number | null;
  fire_ops: boolean;      // heuristic: firefighting type or low rotorcraft
  role: "tanker" | "air_attack" | "helicopter" | "other";
  incident_id: string;
  seen_at: string;
  trail: [number, number][];
};

export type AircraftSet = {
  aircraft: Aircraft[];
  count: number;
  fire_ops: number;
  note: string;
  error?: string;
};

export const fetchAircraft = () => get<AircraftSet>("/aircraft");

export type WindPoint = {
  incident_id: string;
  name: string;
  lat: number;
  lon: number;
  wind_degrees: number;   // direction wind blows FROM (NWS convention)
  wind_compass: string | null;
  wind_mph: number | null;
  gust_mph: number | null;
  station_km: number | null;
  fetched_at: string;
};

export type WindSet = { winds: WindPoint[]; count: number; note: string };

export const fetchWind = () => get<WindSet>("/wind");

export type AirResource = {
  hex: string;
  flight: string | null;
  type: string | null;
  descr: string | null;
  role: "tanker" | "air_attack" | "helicopter" | "other";
  fire_ops: boolean;
  alt_ft: number | null;
  gs: number | null;
  track: number | null;
  seen_at: string;
};

export type AircraftPhoto = {
  hex: string;
  found: boolean;
  thumb_url?: string | null;
  large_url?: string | null;
  photographer?: string | null;
  link?: string | null;
};

export const fetchAircraftPhoto = (hex: string) =>
  get<AircraftPhoto>(`/aircraft/${hex}/photo`);

export const fetchGnis = () => get<{ features: number }>("/gnis");

export async function loadGnis(): Promise<{ job_id: string }> {
  const res = await fetch("/api/gnis/load", { method: "POST" });
  if (!res.ok) throw new Error(`load failed (${res.status})`);
  return res.json();
}

export async function fetchBackups(): Promise<BackupStatus> {
  return get<BackupStatus>("/backups");
}

export async function runBackup(): Promise<{ manifest: { file: string } }> {
  const res = await fetch("/api/backups/run", { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export type SettingEntry = {
  value: string;
  default: string;
  overridden: boolean;
  label: string;
  kind: string;
};

export type SettingsPayload = {
  settings: Record<string, SettingEntry>;
  models: string[];
};

export async function fetchSettings(): Promise<SettingsPayload> {
  return get<SettingsPayload>("/settings");
}

export async function saveSettings(
  updates: Record<string, string>,
): Promise<SettingsPayload> {
  const res = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: updates }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export type SourceSuggestion = {
  id: string;
  domain: string;
  sample_url: string;
  contexts: string[];
  hit_count: number;
  first_seen_at: string;
};

export async function fetchSourceSuggestions(): Promise<SourceSuggestion[]> {
  const d = await get<{ suggestions: SourceSuggestion[] }>("/sources/suggestions");
  return d.suggestions;
}

export async function approveSuggestion(id: string, tier = 4): Promise<{ feed_url: string }> {
  const res = await fetch(`/api/sources/suggestions/${id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tier }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export async function dismissSuggestion(id: string): Promise<void> {
  const res = await fetch(`/api/sources/suggestions/${id}/dismiss`, { method: "POST" });
  if (!res.ok) throw new Error(`dismiss failed (${res.status})`);
}

export async function startResearch(query: string, incidentId?: string): Promise<{ job_id: string }> {
  const res = await fetch("/api/research", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, incident_id: incidentId }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(String(detail));
  }
  return res.json();
}

export async function fetchHealth(): Promise<Health> {
  try {
    const [h, q] = await Promise.all([
      get<{ ok: boolean }>("/healthz"),
      get<{ jobs: Record<string, number> }>("/queue"),
    ]);
    return { api: h.ok, queue: q.jobs };
  } catch {
    return { api: false, queue: {} };
  }
}
