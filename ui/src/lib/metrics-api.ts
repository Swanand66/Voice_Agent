export type Turn = {
  turn_index: number;
  ts: number;
  llm_prompt_tokens: number;
  llm_completion_tokens: number;
  llm_cost_usd: number;
  tts_chars: number;
  tts_cost_usd: number;
  stt_seconds: number;
  stt_cost_usd: number;
  total_cost_usd: number;
  llm_ttfb_ms: number;
  tts_ttfb_ms: number;
  end_to_end_ms: number;
};

export type Session = {
  id: string;
  client_id?: string;
  started_at: string;
  ended_at: string | null;
  turn_count: number;
  total_cost_usd: number;
  turns?: Turn[];
};

export type Client = {
  id: string;
  name: string;
  created_at: string;
  total_cost_usd: number;
  total_turns: number;
  session_count: number;
  last_active: string | null;
  sessions?: Session[];
};

const BASE = (import.meta.env.VITE_METRICS_URL ?? "http://localhost:8081").replace(
  /\/$/,
  ""
);

const PASSWORD_STORAGE_KEY = "dashboard-password";

export function getStoredPassword(): string | null {
  try {
    return sessionStorage.getItem(PASSWORD_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredPassword(pw: string): void {
  sessionStorage.setItem(PASSWORD_STORAGE_KEY, pw);
}

export function clearStoredPassword(): void {
  sessionStorage.removeItem(PASSWORD_STORAGE_KEY);
}

export class AuthError extends Error {}

async function req<T>(
  path: string,
  init: RequestInit = {},
  password?: string
): Promise<T> {
  const pw = password ?? getStoredPassword();
  const headers = new Headers(init.headers);
  if (pw) headers.set("X-Dashboard-Password", pw);

  const res = await fetch(`${BASE}${path}`, { ...init, headers });

  if (res.status === 401) {
    clearStoredPassword();
    throw new AuthError("Invalid dashboard password");
  }
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const metricsApi = {
  verifyPassword: (pw: string) =>
    req<{ ok: true }>("/api/auth", { method: "POST" }, pw),
  listClients: () => req<Client[]>("/api/clients"),
  clientDetail: (id: string) => req<Client>(`/api/clients/${id}`),
  sessionDetail: (id: string) => req<Session>(`/api/sessions/${id}`),
  currentSession: (clientId?: string) => {
    const q = clientId ? `?client_id=${encodeURIComponent(clientId)}` : "";
    return req<Session | null>(`/api/sessions/current${q}`);
  },
};

export function formatUsd(v: number): string {
  if (v === 0) return "$0.0000";
  if (v < 0.01) return `$${v.toFixed(5)}`;
  if (v < 100) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

export function formatTime(ts: number | string): string {
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  return d.toLocaleTimeString();
}

export function formatDate(ts: number | string): string {
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  return d.toLocaleString();
}
