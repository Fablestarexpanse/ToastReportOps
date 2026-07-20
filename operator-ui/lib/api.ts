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
};

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
};

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

export type DraftDetail = {
  draft: Draft & { body_md: string; cited_fact_ids: string };
  cited_facts: CitedFact[];
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
  const res = await fetch(`/api${path}`, {
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

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

export const fetchIncidents = () =>
  get<{ incidents: Incident[] }>("/incidents").then((r) => r.incidents);
export const fetchIncidentFacts = (id: string) =>
  get<{ facts: Fact[] }>(`/incidents/${id}/facts`).then((r) => r.facts);
export const fetchIntake = () => get<{ facts: Fact[] }>("/intake?limit=80").then((r) => r.facts);
export const fetchSources = () => get<{ sources: Source[] }>("/sources").then((r) => r.sources);

export const fetchAdjudication = () =>
  get<{ queue: AdjudicationItem[] }>("/adjudication").then((r) => r.queue);

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

export type MetricValue = {
  value: number | null;
  prev: number | null;
  delta: number | null;
  observed_at: string | null;
  source_name?: string;
  tier?: number;
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
};

export type IncidentIntel = {
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
  evac: ImpactFact[];
  road: ImpactFact[];
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

export type BackupStatus = {
  snapshots: number;
  latest_at: string | null;
  latest: { file: string; bytes: number; verified: { counts: Record<string, number> } } | null;
  offsite_configured: boolean;
  at_risk: boolean;
  snapshot_dir: string;
};

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
