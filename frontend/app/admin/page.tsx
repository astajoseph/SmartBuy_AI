"use client";
import { useCallback, useEffect, useState } from "react";
import { Card, Notice, StatusDot } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";

// The admin token is typed in by the administrator and kept in sessionStorage for this tab only.
// It is never bundled into the frontend build.
type Row = Record<string, unknown>;

export default function AdminPage() {
  const [token, setToken] = useState("");
  const [health, setHealth] = useState<Row[] | null>(null);
  const [jobs, setJobs] = useState<Row[] | null>(null);
  const [cache, setCache] = useState<Row[] | null>(null);
  const [out, setOut] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState({ url: "", kind: "price_history", adapter: "auto", config: "{}" });

  useEffect(() => { try { setToken(sessionStorage.getItem("smartbuy.admin") ?? ""); } catch { /* ignore */ } }, []);
  const call = useCallback(async <T,>(path: string, init: RequestInit = {}) => api<T>(path, { ...init, admin: token }), [token]);
  const refresh = useCallback(async () => {
    if (!token) return;
    setErr(null);
    try {
      const [h, j, c] = await Promise.all([call<Row[]>("/api/admin/sources/health"), call<Row[]>("/api/admin/jobs?limit=50"), call<Row[]>("/api/admin/cache-status")]);
      setHealth(h); setJobs(j); setCache(c);
    } catch (e) { setErr((e as Error).message); }
  }, [token, call]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true); setErr(null); setOut(`${label}…`);
    try { setOut(JSON.stringify(await fn(), null, 2)); await refresh(); } catch (e) { setErr((e as Error).message); setOut(""); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Administration</h1>
      <Card title="Administrator token">
        <div className="flex gap-2">
          <input type="password" className="input max-w-md" placeholder="Administrator token" value={token} onChange={(e) => setToken(e.target.value)} aria-label="Admin token" />
          <button className="btn-primary" onClick={() => { try { sessionStorage.setItem("smartbuy.admin", token); } catch { /* ignore */ } void refresh(); }}>Unlock</button>
        </div>
      </Card>
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
