import type {
  ChatRequest,
  ChatResponse,
  HealthResponse,
  MemoryListResponse,
  RetrieveRequest,
  RetrieveResponse,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export const DEV_USER_ID =
  import.meta.env.VITE_DEV_USER_ID ?? "00000000-0000-0000-0000-000000000001";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    // FastAPI puts validation errors under `detail`; fall back to the status.
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  return response.json() as Promise<T>;
}

export function sendMessage(message: string): Promise<ChatResponse> {
  const payload: ChatRequest = { user_id: DEV_USER_ID, message };
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function retrieve(
  options: Omit<RetrieveRequest, "user_id">,
): Promise<RetrieveResponse> {
  const payload: RetrieveRequest = { user_id: DEV_USER_ID, ...options };
  return request<RetrieveResponse>("/api/retrieve", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listMemories(
  status?: "ACTIVE" | "EXPIRED",
): Promise<MemoryListResponse> {
  const params = new URLSearchParams({ user_id: DEV_USER_ID });
  if (status) params.set("status", status);
  return request<MemoryListResponse>(`/api/memories?${params}`);
}
