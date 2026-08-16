// Mirrors backend/app/schemas.py. Keep the two in sync by hand until there is
// enough surface area to justify generating this from the OpenAPI schema.

export type MemoryStatus = "ACTIVE" | "EXPIRED";
export type Resolution = "REINFORCE" | "ADDITIVE" | "SUPERSEDE";

export interface ChatRequest {
  user_id: string;
  message: string;
}

export interface ChatResponse {
  reply: string;
  event_id: string | null;
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
}
