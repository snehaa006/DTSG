// Mirrors backend/app/schemas.py. Keep the two in sync by hand until there is
// enough surface area to justify generating this from the OpenAPI schema.

export type MemoryStatus = "ACTIVE" | "EXPIRED";
export type Resolution = "REINFORCE" | "ADDITIVE" | "SUPERSEDE";

export interface ChatRequest {
  user_id: string;
  message: string;
}

export interface Fact {
  subject: string;
  predicate: string;
  object: string;
  confidence: number;
}

export interface FactOutcome {
  fact: Fact;
  resolution: Resolution;
  reasoning: string;
  memory_id: string | null;
  expired_memory_ids: string[];
  reinforced_memory_id: string | null;
}

export interface ChatResponse {
  reply: string;
  event_id: string;
  outcomes: FactOutcome[];
  error: string | null;
}

export interface Event {
  id: string;
  user_id: string;
  raw_text: string;
  timestamp: string;
}

export interface MemoryListResponse {
  memories: Memory[];
}

export type RetrieveMode = "now" | "as_of" | "changes";

/** A memory with the scoring terms kept separate, so a ranking can be explained. */
export interface ScoredMemory extends Memory {
  similarity: number;
  status_weight: number;
  recency: number;
  score: number;
}

export interface RetrieveRequest {
  user_id: string;
  query: string;
  limit?: number;
  mode?: RetrieveMode;
  as_of?: string;
  lambda_per_day?: number;
  expired_weight?: number;
}

export interface RetrieveResponse {
  results: ScoredMemory[];
  mode: RetrieveMode;
  evaluated_at: string;
  candidates_considered: number;
  lambda_per_day: number;
  expired_weight: number;
}

export interface EventListResponse {
  events: Event[];
  next_before_timestamp: string | null;
  next_before_id: string | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  database: boolean;
  phase: number;
}

export interface Memory {
  id: string;
  user_id: string;
  subject: string | null;
  predicate: string | null;
  object: string | null;
  status: MemoryStatus;
  confidence: number;
  source_event: string | null;
  supersedes: string | null;
  superseded_by: string | null;
  valid_from: string | null;
  valid_until: string | null;
  last_reinforced_at: string | null;
}
