"use client";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import type { Session } from "@supabase/supabase-js";
import { Loading, Notice } from "@/components/ui";
import { api } from "@/lib/api";
import { authConfigured, supabase } from "@/lib/supabase";
import type { Profile } from "@/types/api";

interface AuthState {
  ready: boolean; session: Session | null; profile: Profile | null; error: string | null;
  isAdmin: boolean; signOut: () => Promise<void>; refreshProfile: () => Promise<void>;
}
const AuthContext = createContext<AuthState>({
  ready: false, session: null, profile: null, error: null, isAdmin: false, signOut: async () => {}, refreshProfile: async () => {},
});
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(!authConfigured);
  const [session, setSession] = useState<Session | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadProfile = useCallback(async (s: Session | null, signedInNow = false) => {
    if (!s) { setProfile(null); setError(null); return; }
    try {
      setProfile(await api<Profile>(signedInNow ? "/api/me/sign-in" : "/api/me", signedInNow ? { method: "POST" } : {}));
      setError(null);
    } catch (e) { setError((e as Error).message); }
  }, []);

  useEffect(() => {
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s);
      // Defer the API call: Supabase must not be called back synchronously from inside its own listener.
      // SIGNED_IN can repeat (e.g. on tab focus); the server keeps at most one sign-in entry per few minutes.
      setTimeout(() => { void loadProfile(s, event === "SIGNED_IN").finally(() => setReady(true)); }, 0);
    });
    return () => data.subscription.unsubscribe();
  }, [loadProfile]);

  const signOut = useCallback(async () => { await supabase?.auth.signOut(); setProfile(null); }, []);
  const refreshProfile = useCallback(() => loadProfile(session), [loadProfile, session]);

  return (
    <AuthContext.Provider value={{ ready, session, profile, error, isAdmin: profile?.role === "admin", signOut, refreshProfile }}>
      {children}
    </AuthContext.Provider>
  );
}

// Wrap a page that needs a signed-in user (and optionally an admin). Sends visitors to /login and back afterwards.
export function RequireAuth({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { ready, session, profile, error, isAdmin } = useAuth();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    if (authConfigured && ready && !session) router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [ready, session, path, router]);

  if (!authConfigured) return <Notice tone="warn">Sign-in is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY (see frontend/.env.example).</Notice>;
  if (!ready || !session) return <Loading what="Checking sign-in" />;
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!profile) return <Loading what="Loading your account" />;
  if (admin && !isAdmin) return <Notice tone="warn">This page is for administrators. Your account ({profile.email}) is a normal user account.</Notice>;
  return <>{children}</>;
}
