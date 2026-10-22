"use client";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import { Card, Loading, Notice } from "@/components/ui";
import { authConfigured, supabase } from "@/lib/supabase";

type Mode = "sign-in" | "sign-up" | "forgot";
const TITLES: Record<Mode, string> = { "sign-in": "Sign in", "sign-up": "Create an account", forgot: "Reset your password" };

function Login() {
  const params = useSearchParams();
  const router = useRouter();
  const { session } = useAuth();
  const next = params.get("next")?.startsWith("/") ? params.get("next")! : "/advisor";
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => { if (session) router.replace(next); }, [session, next, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!supabase) return;
    setErr(null); setInfo(null);
    if (mode === "sign-up" && password !== password2) { setErr("The passwords do not match."); return; }
    setBusy(true);
    try {
      if (mode === "sign-in") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      } else if (mode === "sign-up") {
        const { data, error } = await supabase.auth.signUp({ email, password, options: { emailRedirectTo: `${location.origin}/login` } });
        if (error) throw error;
        if (!data.session) setInfo(`We sent a confirmation link to ${email}. Open it, then sign in here.`);
      } else {
        const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo: `${location.origin}/reset-password` });
        if (error) throw error;
        setInfo(`If ${email} has an account, a password-reset link is on its way.`);
      }
    } catch (x) {
      const m = (x as Error).message;
      setErr(m === "Invalid login credentials" ? "Wrong e-mail or password." : m === "Failed to fetch" ? "Connection lost. Check your internet connection." : m);
    } finally { setBusy(false); }
  }

  if (!authConfigured) return <Notice tone="warn">Sign-in is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY (see frontend/.env.example).</Notice>;
  return (
    <div className="mx-auto max-w-md space-y-4 pt-6">
      <div className="text-center">
        <h1 className="text-2xl font-semibold">{TITLES[mode]}</h1>
        <p className="mt-1 text-sm text-slate-600">Your assistant conversations, recommendations and alerts are saved to your account.</p>
      </div>
      <Card>
        <div className="mb-4 grid grid-cols-2 rounded-lg bg-slate-100 p-1 text-sm" role="tablist">
          {(["sign-in", "sign-up"] as const).map((m) => (
            <button key={m} role="tab" aria-selected={mode === m} onClick={() => { setMode(m); setErr(null); setInfo(null); }}
              className={`rounded-md py-1.5 ${mode === m ? "bg-white font-medium shadow-sm" : "text-slate-600"}`}>{m === "sign-in" ? "Sign in" : "Create account"}</button>
          ))}
        </div>
        <form onSubmit={submit} className="space-y-3">
          <div><label className="label" htmlFor="email">E-mail</label>
            <input id="email" type="email" autoComplete="email" required className="input" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
          {mode !== "forgot" && (
            <div><label className="label" htmlFor="pw">Password</label>
              <input id="pw" type="password" required minLength={mode === "sign-up" ? 8 : undefined} autoComplete={mode === "sign-up" ? "new-password" : "current-password"}
                className="input" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
          )}
          {mode === "sign-up" && (
            <div><label className="label" htmlFor="pw2">Repeat password</label>
              <input id="pw2" type="password" required minLength={8} autoComplete="new-password" className="input" value={password2} onChange={(e) => setPassword2(e.target.value)} />
              <p className="mt-1 text-xs text-slate-500">At least 8 characters.</p></div>
          )}
          {err && <Notice tone="error">{err}</Notice>}
          {info && <Notice>{info}</Notice>}
          <button className="btn-primary w-full" disabled={busy}>{busy ? "Please wait…" : mode === "sign-in" ? "Sign in" : mode === "sign-up" ? "Create account" : "Send reset link"}</button>
        </form>
        <div className="mt-3 text-center text-sm">
          {mode === "forgot"
            ? <button className="text-brand-700 hover:underline" onClick={() => setMode("sign-in")}>Back to sign in</button>
            : <button className="text-brand-700 hover:underline" onClick={() => { setMode("forgot"); setErr(null); setInfo(null); }}>Forgot your password?</button>}
        </div>
      </Card>
    </div>
  );
}

export default function LoginPage() {
  return <Suspense fallback={<Loading />}><Login /></Suspense>;
}
