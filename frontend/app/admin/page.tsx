"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { RequireAuth, useAuth } from "@/components/AuthProvider";
import { ActivityList, KIND_LABEL, Transcript } from "@/components/History";
import { Card, Loading, Notice, StatusDot } from "@/components/ui";
import { useActivityPages } from "@/hooks/useActivityPages";
import { api } from "@/lib/api";
import { ago, dateTime, money } from "@/lib/format";
import { authConfigured } from "@/lib/supabase";
import type { ActivityKind, ChatMessageRow, ChatSession, Profile, UserTarget } from "@/types/api";

// Signed-in admins use their own account. Without Supabase Auth configured, the static admin token
// (typed in here, kept in sessionStorage for this tab only, never bundled) still unlocks the operations tools.
type Row = Record<string, unknown>;
type UserRow = Profile & { chats: number; activities: number; last_activity_at: string | null };
type Call = <T>(path: string, init?: RequestInit) => Promise<T>;

export default function AdminPage() {
  if (!authConfigured) return <TokenAdmin />;
  return <RequireAuth admin><Suspense fallback={<Loading />}><Console call={(p, i) => api(p, i)} /></Suspense></RequireAuth>;
}

function TokenAdmin() {
  const [token, setToken] = useState("");
  const [unlocked, setUnlocked] = useState("");
  useEffect(() => { try { const t = sessionStorage.getItem("smartbuy.admin") ?? ""; setToken(t); setUnlocked(t); } catch { /* ignore */ } }, []);
  const call = useCallback<Call>((path, init = {}) => api(path, { ...init, admin: unlocked }), [unlocked]);
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Administration</h1>
      <Card title="Administrator token">
        <div className="flex gap-2">
          <input type="password" className="input max-w-md" placeholder="Administrator token" value={token} onChange={(e) => setToken(e.target.value)} aria-label="Admin token" />
          <button className="btn-primary" onClick={() => { try { sessionStorage.setItem("smartbuy.admin", token); } catch { /* ignore */ } setUnlocked(token); }}>Unlock</button>
        </div>
        <p className="mt-2 text-xs text-slate-500">Configure Supabase Auth to manage users and see their activity with an admin account.</p>
      </Card>
      {unlocked && <Operations call={call} />}
    </div>
  );
}

const TABS = [["overview", "Overview"], ["users", "Users"], ["activity", "All activity"], ["operations", "Operations"]] as const;
type Tab = (typeof TABS)[number][0];

function Console({ call }: { call: Call }) {
  const params = useSearchParams();
  const router = useRouter();
  const tab = (params.get("tab") as Tab) || "overview";
  const chat = params.get("chat");
  const user = params.get("user");
  const go = (q: Record<string, string | null>) => {
    const u = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(q)) { if (v) u.set(k, v); else u.delete(k); }
    router.push(`/admin?${u.toString()}`, { scroll: false });
  };
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Administration</h1>
      <div className="flex flex-wrap gap-1 border-b border-slate-200 text-sm" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => go({ tab: k, user: null, chat: null })}
            className={`-mb-px border-b-2 px-3 py-2 ${tab === k ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-slate-600 hover:text-slate-900"}`}>{label}</button>
        ))}
      </div>
      {chat && <ChatViewer id={chat} call={call} onClose={() => go({ chat: null })} />}
      {tab === "overview" && <Overview call={call} onUser={(id) => go({ tab: "users", user: id })} />}
      {tab === "users" && (user ? <UserDetail id={user} call={call} onBack={() => go({ user: null })} onChat={(id) => go({ chat: id })} />
                                : <Users call={call} onUser={(id) => go({ user: id })} />)}
      {tab === "activity" && <AllActivity />}
      {tab === "operations" && <Operations call={call} />}
    </div>
  );
}

function useLoad<T>(call: Call, path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(async () => { setError(null); try { setData(await call<T>(path)); } catch (e) { setError((e as Error).message); } }, [call, path]);
  useEffect(() => { void reload(); }, [path]); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, reload };
}

function Overview({ call, onUser }: { call: Call; onUser: (id: string) => void }) {
  const { data: st, error } = useLoad<Row & { by_kind_7d: Record<string, number> }>(call, "/api/admin/stats");
  const { data: recent } = useLoad<UserRow[]>(call, "/api/admin/users?limit=8");
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!st) return <Loading />;
  const tiles: [string, unknown][] = [["Accounts", st.users], ["Admins", st.admins], ["Active last 24 h", st.active_24h], ["Active last 7 days", st.active_7d],
    ["Questions last 7 days", st.questions_7d], ["Disabled accounts", st.disabled], ["Failed answers", st.failed_answers]];
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {tiles.map(([label, v]) => <Card key={label}><div className="text-xs text-slate-500">{label}</div><div className="mt-1 text-2xl font-semibold tabular-nums">{String(v ?? 0)}</div></Card>)}
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Activity last 7 days">
          {Object.keys(st.by_kind_7d).length === 0 ? <p className="text-sm text-slate-500">No activity yet.</p> : (
            <ul className="space-y-1 text-sm">{Object.entries(st.by_kind_7d).map(([k, n]) => <li key={k} className="flex justify-between"><span>{KIND_LABEL[k as ActivityKind]?.[0] ?? k}</span><span className="tabular-nums">{n}</span></li>)}</ul>
          )}
        </Card>
        <Card title="Recently seen accounts">
          <ul className="space-y-1 text-sm">{recent?.map((u) => (
            <li key={u.user_id}><button className="flex w-full justify-between rounded px-1 py-0.5 text-left hover:bg-slate-50" onClick={() => onUser(u.user_id)}>
              <span className="truncate">{u.display_name || u.email}</span><span className="text-xs text-slate-500">{ago(u.last_seen_at)}</span></button></li>
          ))}</ul>
        </Card>
      </div>
    </div>
  );
}

function Users({ call, onUser }: { call: Call; onUser: (id: string) => void }) {
  const { profile } = useAuth();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const { data, error, reload } = useLoad<UserRow[]>(call, `/api/admin/users${query ? `?q=${encodeURIComponent(query)}` : ""}`);
  const [err, setErr] = useState<string | null>(null);
  async function act(path: string, body: object) {
    setErr(null);
    try { await call(path, { method: "POST", body: JSON.stringify(body) }); await reload(); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <Card title={`Accounts${data ? ` (${data.length})` : ""}`} actions={
      <form onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }} className="flex gap-2">
        <input className="input w-56 py-1" placeholder="Search e-mail or name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users" />
        <button className="btn-secondary px-3 py-1">Search</button>
      </form>}>
      {(error || err) && <div className="mb-3"><Notice tone="error">{error || err}</Notice></div>}
      {!data ? <Loading /> : (
        <div className="overflow-x-auto"><table className="table">
          <thead><tr><th>Account</th><th>Role</th><th>Status</th><th>Joined</th><th>Last seen</th><th>Chats</th><th>Activity</th><th>Actions</th></tr></thead>
          <tbody>{data.map((u) => {
            const self = u.user_id === profile?.user_id;
            return (
              <tr key={u.user_id}>
                <td><button className="text-left font-medium hover:text-brand-700" onClick={() => onUser(u.user_id)}>{u.display_name || u.email}</button>{u.display_name && <div className="text-xs text-slate-500">{u.email}</div>}</td>
                <td>{u.role === "admin" ? <span className="rounded bg-brand-600 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-white">Admin</span> : <span className="text-xs text-slate-600">User</span>}</td>
                <td className={`text-xs font-medium ${u.is_disabled ? "text-rose-700" : "text-emerald-700"}`}>{u.is_disabled ? "Disabled" : "Active"}</td>
                <td className="text-xs">{dateTime(u.created_at)}</td>
                <td className="text-xs">{ago(u.last_seen_at)}</td>
                <td className="text-xs tabular-nums">{u.chats}</td>
                <td className="text-xs tabular-nums">{u.activities}</td>
                <td className="space-x-1 whitespace-nowrap">
                  <button className="btn-secondary px-2 py-1 text-xs" onClick={() => onUser(u.user_id)}>View</button>
                  <button className="btn-secondary px-2 py-1 text-xs" disabled={self} onClick={() => void act(`/api/admin/users/${u.user_id}/role`, { role: u.role === "admin" ? "user" : "admin" })}>{u.role === "admin" ? "Remove admin" : "Make admin"}</button>
                  <button className={`btn-secondary px-2 py-1 text-xs ${u.is_disabled ? "" : "text-rose-700"}`} disabled={self} onClick={() => void act(`/api/admin/users/${u.user_id}/disabled`, { disabled: !u.is_disabled })}>{u.is_disabled ? "Enable" : "Disable"}</button>
                </td>
              </tr>
            );
          })}</tbody>
        </table></div>
      )}
      <p className="mt-3 text-xs text-slate-500">A disabled account can still sign in to Supabase but every SmartBuy request is refused. You cannot disable or demote yourself, and at least one active admin always remains.</p>
    </Card>
  );
}

function UserDetail({ id, call, onBack, onChat }: { id: string; call: Call; onBack: () => void; onChat: (id: string) => void }) {
  const { data: u, error } = useLoad<Profile & { targets: UserTarget[]; sessions: ChatSession[] }>(call, `/api/admin/users/${id}`);
  const { items, done, error: aerr, more } = useActivityPages(`/api/admin/activity?user_id=${id}`);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!u) return <Loading />;
  return (
    <div className="space-y-6">
      <button className="text-sm text-brand-700 hover:underline" onClick={onBack}>← All accounts</button>
      <Card>
        <div className="text-lg font-semibold">{u.display_name || u.email}</div>
        <div className="text-sm text-slate-600">{u.email} · {u.role === "admin" ? "Administrator" : "User"} · {u.is_disabled ? "Disabled" : "Active"} · joined {dateTime(u.created_at)} · last seen {ago(u.last_seen_at)}</div>
      </Card>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title={`Conversations (${u.sessions.length})`}>
          {u.sessions.length === 0 ? <p className="text-sm text-slate-500">No conversations.</p> : (
            <ul className="divide-y divide-slate-100 text-sm">{u.sessions.map((s) => (
              <li key={s.id}><button className="w-full py-2 text-left hover:text-brand-700" onClick={() => onChat(s.id)}>
                <div className="font-medium">{s.title}</div><div className="text-xs text-slate-500">{s.questions} questions · {ago(s.updated_at)}</div></button></li>
            ))}</ul>
          )}
        </Card>
        <Card title="Price alerts">
          {u.targets.length === 0 ? <p className="text-sm text-slate-500">None.</p> : (
            <table className="table"><thead><tr><th>Product</th><th>Target</th><th>Alerts</th></tr></thead>
              <tbody>{u.targets.map((t) => <tr key={t.product_id}><td>{t.product_id}</td><td>{money(t.target_price)}</td><td>{t.alerts_enabled ? "On" : "Off"}</td></tr>)}</tbody></table>
          )}
        </Card>
      </div>
      <Card title="History">
        {aerr && <Notice tone="error">{aerr}</Notice>}
        <ActivityList items={items} chatHref={(sid) => `/admin?tab=users&user=${id}&chat=${sid}`} />
        {!done && items.length > 0 && <button className="btn-secondary mt-3" onClick={() => void more()}>Load more</button>}
      </Card>
    </div>
  );
}

function AllActivity() {
  const [kind, setKind] = useState<ActivityKind | "">("");
  const { items, done, error, more } = useActivityPages(`/api/admin/activity${kind ? `?kind=${kind}` : ""}`);
  return (
    <Card title="Activity of all users" actions={
      <select className="input w-auto py-1" value={kind} onChange={(e) => setKind(e.target.value as ActivityKind | "")} aria-label="Filter">
        <option value="">Everything</option>
        {(Object.keys(KIND_LABEL) as ActivityKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k][0]}</option>)}
      </select>}>
      {error && <Notice tone="error">{error}</Notice>}
      <ActivityList items={items} showUser chatHref={(sid) => `/admin?tab=activity&chat=${sid}`} />
      {!done && items.length > 0 && <button className="btn-secondary mt-3" onClick={() => void more()}>Load more</button>}
    </Card>
  );
}

function ChatViewer({ id, call, onClose }: { id: string; call: Call; onClose: () => void }) {
  const { data, error } = useLoad<ChatSession & { owner: Profile | null; messages: ChatMessageRow[] }>(call, `/api/admin/chats/${id}`);
  return (
    <Card title={data ? `${data.title} — ${data.owner?.email ?? "unknown user"}` : "Conversation"} actions={<button className="btn-secondary px-3 py-1" onClick={onClose}>Close</button>}>
      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error ? <Loading /> : data && <div className="max-h-[32rem] overflow-y-auto pr-1"><Transcript messages={data.messages} /></div>}
    </Card>
  );
}

function Operations({ call }: { call: Call }) {
  const [health, setHealth] = useState<Row[] | null>(null);
  const [jobs, setJobs] = useState<Row[] | null>(null);
  const [cache, setCache] = useState<Row[] | null>(null);
  const [out, setOut] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState({ url: "", kind: "price_history", adapter: "auto", config: "{}" });

  const refresh = useCallback(async () => {
    setErr(null);
    try {
      const [h, j, c] = await Promise.all([call<Row[]>("/api/admin/sources/health"), call<Row[]>("/api/admin/jobs?limit=50"), call<Row[]>("/api/admin/cache-status")]);
      setHealth(h); setJobs(j); setCache(c);
    } catch (e) { setErr((e as Error).message); }
  }, [call]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true); setErr(null); setOut(`${label}…`);
    try { setOut(JSON.stringify(await fn(), null, 2)); await refresh(); } catch (e) { setErr((e as Error).message); setOut(""); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      {err && <Notice tone="error">{err}</Notice>}
      {health && (
        <>
          <Card title="Source health & manual collection" actions={<button className="btn-secondary" onClick={() => run("Syncing registry", () => call("/api/admin/sync-registry", { method: "POST" }))} disabled={busy}>Sync products.yaml</button>}>
            <div className="overflow-x-auto"><table className="table">
              <thead><tr><th>Product</th><th>Source</th><th>Health</th><th>Last success</th><th>Points</th><th>Last error</th><th /></tr></thead>
              <tbody>{health.map((h) => (
                <tr key={String(h.id)}><td>{String(h.product_id)}</td><td className="max-w-xs break-all text-xs">{String(h.source_url)}<div className="text-slate-500">{String(h.adapter_name)} · every {String(h.collection_interval_minutes)} min</div></td>
                  <td><StatusDot status={String(h.health)} /></td><td className="text-xs">{dateTime(h.last_success_at as string)}</td>
                  <td className="text-xs">{String(h.history_points)} history · {String(h.observations)} obs.</td><td className="max-w-xs text-xs text-rose-700">{(h.last_error as string) ?? ""}</td>
                  <td className="space-y-1"><button className="btn-secondary w-full" disabled={busy} onClick={() => run("Collecting", () => call(`/api/admin/collect/${h.product_id}`, { method: "POST" }))}>Collect now</button>
                    <button className="btn-secondary w-full" disabled={busy} onClick={() => run("Refreshing vendors", () => call(`/api/admin/refresh-vendor-data/${h.product_id}`, { method: "POST" }))}>Refresh vendors</button></td></tr>
              ))}</tbody>
            </table></div>
          </Card>
          <Card title="Test a URL (inspect & report extractable fields)">
            <div className="grid gap-3 md:grid-cols-4">
              <input className="input md:col-span-2" placeholder="https://…" value={test.url} onChange={(e) => setTest({ ...test, url: e.target.value })} aria-label="URL" />
              <select className="input" value={test.kind} onChange={(e) => setTest({ ...test, kind: e.target.value })} aria-label="Kind"><option value="price_history">Price-history source</option><option value="vendor">Vendor page</option></select>
              <input className="input" value={test.adapter} onChange={(e) => setTest({ ...test, adapter: e.target.value })} aria-label="Adapter" />
              <textarea className="input md:col-span-3 font-mono text-xs" rows={2} value={test.config} onChange={(e) => setTest({ ...test, config: e.target.value })} aria-label="Adapter config JSON" />
              <button className="btn-primary" disabled={busy || !test.url} onClick={() => run("Testing source", async () => {
                let cfg = {}; try { cfg = JSON.parse(test.config || "{}"); } catch { throw new Error("Adapter config is not valid JSON"); }
                return call("/api/sources/test", { method: "POST", body: JSON.stringify({ url: test.url, kind: test.kind, adapter: test.adapter, adapter_config: cfg }) });
              })}>Run extraction test</button>
            </div>
            <p className="mt-2 text-xs text-slate-500">To register a tested URL permanently, add it to config/products.yaml and press “Sync products.yaml”, or POST /api/products/register.</p>
          </Card>
          {out && <Card title="Result"><pre className="max-h-96 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">{out}</pre></Card>}
          <Card title="Vendor cache status">
            {!cache?.length ? <p className="text-sm text-slate-500">No vendor sources registered.</p> : (
              <table className="table"><thead><tr><th>Product</th><th>Vendor</th><th>Last fetch</th><th>Status</th><th>Expires</th></tr></thead>
                <tbody>{cache.map((c) => <tr key={String(c.vendor_source_id)}><td>{String(c.product_id)}</td><td>{String(c.vendor)}</td><td className="text-xs">{dateTime(c.fetched_at as string)}</td><td>{String(c.fetch_status ?? "never fetched")}</td><td className="text-xs">{c.expires_by_kind ? Object.entries(c.expires_by_kind as Record<string, string>).map(([k, v]) => <div key={k}>{k}: {dateTime(v)}</div>) : "—"}</td></tr>)}</tbody></table>
            )}
          </Card>
          <Card title="Job log (latest 50)">
            <div className="overflow-x-auto"><table className="table">
              <thead><tr><th>#</th><th>Type</th><th>Product</th><th>Started</th><th>Status</th><th>Extracted / inserted</th><th>Retries</th><th>Error</th></tr></thead>
              <tbody>{jobs?.map((j) => (
                <tr key={String(j.id)}><td>{String(j.id)}</td><td className="text-xs">{String(j.job_type)}</td><td className="text-xs">{String(j.product_id ?? "")}</td><td className="text-xs">{dateTime(j.started_at as string)}</td>
                  <td className={`text-xs font-medium ${j.status === "failed" ? "text-rose-700" : j.status === "success" ? "text-emerald-700" : "text-amber-700"}`}>{String(j.status)}</td>
                  <td className="text-xs">{String(j.records_extracted)} / {String(j.records_inserted)}</td><td className="text-xs">{String(j.retry_count)}</td><td className="max-w-xs text-xs text-rose-700">{(j.error_message as string) ?? ""}</td></tr>
              ))}</tbody>
            </table></div>
          </Card>
        </>
      )}
    </div>
  );
}
