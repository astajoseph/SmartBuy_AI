// Browser-side API client. Only the public backend URL is used here; no secrets live in the frontend.
// When a user is signed in, their Supabase access token is attached automatically.
import { supabase } from "@/lib/supabase";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

// status 0 = the request never got an answer (offline, connection dropped, server unreachable).
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
  get retryable() { return this.status === 0 || this.status === 429 || this.status >= 500; }
}

async function accessToken(): Promise<string | null> {
  if (!supabase) return null;
  try { return (await supabase.auth.getSession()).data.session?.access_token ?? null; } catch { return null; }
}

export async function api<T>(path: string, init: RequestInit & { admin?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = init.admin || (await accessToken());
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers: { ...headers, ...(init.headers as object) }, cache: "no-store" });
  } catch {
    throw new ApiError(0, "Connection lost. Check your internet connection.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}
