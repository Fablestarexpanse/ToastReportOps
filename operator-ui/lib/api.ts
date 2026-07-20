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
};

export type Fact = {
  id: string;
  incident_id: string | null;
  incident_name?: string | null;
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
};

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

export const fetchDrafts = () => get<{ drafts: Draft[] }>("/drafts").then((r) => r.drafts);
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

export type AdjudicationItem = { fact: Fact; candidates: AdjudicationCandidate[] };

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
