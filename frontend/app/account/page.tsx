"use client";
import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { RequireAuth, useAuth } from "@/components/AuthProvider";
import { ActivityList, KIND_LABEL } from "@/components/History";
import { Card, Loading, Notice } from "@/components/ui";
import { useActivityPages } from "@/hooks/useActivityPages";
import { useApi } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { ago, dateTime, money } from "@/lib/format";
import type { ActivityKind, ChatSession, UserTarget } from "@/types/api";

const TABS = [["conversations", "Conversations"], ["history", "History"], ["alerts", "Price alerts"], ["profile", "Profile"]] as const;
type Tab = (typeof TABS)[number][0];

function Account() {
  const params = useSearchParams();
  const { profile, refreshProfile, signOut } = useAuth();
  const [tab, setTab] = useState<Tab>("conversations");
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Your account</h1>
          <p className="text-sm text-slate-600">{profile!.email} · {profile!.role === "admin" ? "Administrator" : "User"} · member since {dateTime(profile!.created_at)}</p>
        </div>
        <button className="btn-secondary" onClick={() => void signOut()}>Sign out</button>
      </div>
      {params.get("password") === "changed" && <Notice>Your password was changed.</Notice>}
      <div className="flex flex-wrap gap-1 border-b border-slate-200 text-sm" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
            className={`-mb-px border-b-2 px-3 py-2 ${tab === k ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-slate-600 hover:text-slate-900"}`}>{label}</button>
        ))}
      </div>
      {tab === "conversations" && <Conversations />}
      {tab === "history" && <History />}
      {tab === "alerts" && <Alerts />}
      {tab === "profile" && <ProfileForm onSaved={refreshProfile} />}
    </div>
  );
}

function Conversations() {
  const { data, error, loading, reload } = useApi<ChatSession[]>("/api/assistant/sessions");
  const [err, setErr] = useState<string | null>(null);
  async function remove(id: string) {
    setErr(null);
    try { await api(`/api/assistant/sessions/${id}`, { method: "DELETE" }); reload(); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <Card title="Saved assistant conversations" actions={<Link href="/advisor?session=" className="btn-primary px-3 py-1.5">New conversation</Link>}>
      {(error || err) && <Notice tone="error">{error || err}</Notice>}
      {loading && !data && <Loading />}
      {data?.length === 0 && <p className="text-sm text-slate-500">No conversations yet. Ask the assistant on the Purchase advisor page.</p>}
      <ul className="divide-y divide-slate-100">{data?.map((s) => (
        <li key={s.id} className="flex flex-wrap items-center gap-3 py-3">
          <div className="min-w-0 flex-1">
            <Link href={`/advisor?session=${s.id}`} className="font-medium text-slate-900 hover:text-brand-700">{s.title}</Link>
            <div className="truncate text-xs text-slate-500">{s.questions} question{s.questions === 1 ? "" : "s"} · last active {ago(s.updated_at)}{s.product_id && ` · ${s.product_id}`}{s.last_question && ` · “${s.last_question}”`}</div>
          </div>
          <Link href={`/advisor?session=${s.id}`} className="btn-secondary px-3 py-1">Continue</Link>
          <button className="btn-secondary px-3 py-1 text-rose-700" onClick={() => void remove(s.id)}>Delete</button>
        </li>
      ))}</ul>
    </Card>
  );
}

function History() {
  const [kind, setKind] = useState<ActivityKind | "">("");
  const { items, done, error, more } = useActivityPages(`/api/me/activity${kind ? `?kind=${kind}` : ""}`);
  return (
    <Card title="Your history" actions={
      <select className="input w-auto py-1" value={kind} onChange={(e) => setKind(e.target.value as ActivityKind | "")} aria-label="Filter">
        <option value="">Everything</option>
        {(Object.keys(KIND_LABEL) as ActivityKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k][0]}</option>)}
      </select>}>
      {error && <Notice tone="error">{error}</Notice>}
      <ActivityList items={items} chatHref={(id) => `/advisor?session=${id}`} />
      {!done && items.length > 0 && <button className="btn-secondary mt-3" onClick={() => void more()}>Load more</button>}
    </Card>
  );
}

function Alerts() {
  const { data, error } = useApi<UserTarget[]>("/api/me/targets");
  return (
    <Card title="Price alerts saved on your account">
      {error && <Notice tone="error">{error}</Notice>}
      {data?.length === 0 && <p className="text-sm text-slate-500">No price alerts yet. Set target prices on the Settings page.</p>}
      {!!data?.length && (
        <table className="table"><thead><tr><th>Product</th><th>Target price</th><th>Max budget</th><th>Alerts</th></tr></thead>
          <tbody>{data.map((t) => <tr key={t.product_id}><td><Link className="hover:text-brand-700" href={`/products/${t.product_id}`}>{t.product_id}</Link></td><td>{money(t.target_price)}</td><td>{money(t.max_budget)}</td><td>{t.alerts_enabled ? "On" : "Off"}</td></tr>)}</tbody></table>
      )}
      <p className="mt-3 text-xs text-slate-500">Alerts appear under Settings → Recent notifications. <Link className="text-brand-700 hover:underline" href="/settings">Edit targets</Link></p>
    </Card>
  );
}

function ProfileForm({ onSaved }: { onSaved: () => Promise<void> }) {
  const { profile } = useAuth();
  const [name, setName] = useState(profile?.display_name ?? "");
  const [msg, setMsg] = useState<{ tone: "info" | "error"; text: string } | null>(null);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    try { await api("/api/me", { method: "POST", body: JSON.stringify({ display_name: name }) }); await onSaved(); setMsg({ tone: "info", text: "Saved." }); }
    catch (x) { setMsg({ tone: "error", text: (x as Error).message }); }
  }
  return (
    <Card title="Profile">
      <form onSubmit={save} className="grid max-w-md gap-3">
        <div><label className="label" htmlFor="em">E-mail</label><input id="em" className="input bg-slate-50" value={profile?.email ?? ""} readOnly /></div>
        <div><label className="label" htmlFor="dn">Display name</label><input id="dn" className="input" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} /></div>
        {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
        <div><button className="btn-primary">Save</button></div>
      </form>
      <p className="mt-4 text-xs text-slate-500">To change your password, sign out and use “Forgot your password?” on the sign-in page.</p>
    </Card>
  );
}

export default function AccountPage() {
  return <RequireAuth><Suspense fallback={<Loading />}><Account /></Suspense></RequireAuth>;
}
