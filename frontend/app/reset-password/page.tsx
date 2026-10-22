"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import { Card, Loading, Notice } from "@/components/ui";
import { supabase } from "@/lib/supabase";

// Opened from the password-reset e-mail. Supabase signs the user in from the link, then they choose a new password.
export default function ResetPasswordPage() {
  const { ready, session } = useAuth();
  const router = useRouter();
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw !== pw2) { setErr("The passwords do not match."); return; }
    setBusy(true); setErr(null);
    const { error } = await supabase!.auth.updateUser({ password: pw });
    setBusy(false);
    if (error) setErr(error.message); else router.replace("/account?password=changed");
  }

  if (!ready) return <Loading what="Checking reset link" />;
  if (!session) return <Notice tone="warn">This reset link is invalid or has expired. Request a new one from the sign-in page.</Notice>;
  return (
    <div className="mx-auto max-w-md space-y-4 pt-6">
      <h1 className="text-center text-2xl font-semibold">Choose a new password</h1>
      <Card>
        <form onSubmit={submit} className="space-y-3">
          <div><label className="label" htmlFor="pw">New password</label><input id="pw" type="password" required minLength={8} autoComplete="new-password" className="input" value={pw} onChange={(e) => setPw(e.target.value)} /></div>
          <div><label className="label" htmlFor="pw2">Repeat new password</label><input id="pw2" type="password" required minLength={8} autoComplete="new-password" className="input" value={pw2} onChange={(e) => setPw2(e.target.value)} /></div>
          {err && <Notice tone="error">{err}</Notice>}
          <button className="btn-primary w-full" disabled={busy}>{busy ? "Saving…" : "Save new password"}</button>
        </form>
      </Card>
    </div>
  );
}
