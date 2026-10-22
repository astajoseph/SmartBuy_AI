// Supabase Auth in the browser (e-mail + password). Only the public project URL and the public
// anon / publishable key are used here; they are safe to expose. Never put the service-role key in the frontend.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const authConfigured = Boolean(url && key);
export const supabase: SupabaseClient | null = authConfigured
  ? createClient(url!, key!, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true } })
  : null;
