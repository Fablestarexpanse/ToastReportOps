/** API client — everything proxied through /api → FastAPI on 127.0.0.1:8400. */

export type Incident = {
  id: string;
  canonical_name: string;
  status: "candidate" | "active" | "contained" | "frozen";
  lat: number | null;
  lon: number | null;
  county: string | null;
  irwin_id: string | null;
  created_at: string;
  updated_at: string;
  fact_count: number;
  last_observed: string | null;
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
