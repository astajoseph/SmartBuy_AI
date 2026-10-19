"use client";
import Link from "next/link";
import { useApi } from "@/hooks/useApi";
import { usePrefs } from "@/hooks/usePrefs";
import { ago, date, dateTime, money, pct } from "@/lib/format";
import type { ProductSummary, SaleEvent } from "@/types/api";
import { Card, KindTag, Loading, Notice, StatusDot } from "@/components/ui";

export default function Dashboard() {
  const { data: products, error, loading } = useApi<ProductSummary[]>("/api/products");
  const { data: events } = useApi<{ upcoming: SaleEvent[] }>("/api/events");
  const { prefs } = usePrefs();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-sm text-slate-600">Tracked products, their latest recorded prices and upcoming Indian sale events.</p>
      </div>
      {error && <Notice tone="error">Could not reach the API: {error}</Notice>}
      {loading && !products && <Loading what="Loading products" />}
      {products && products.length === 0 && <Notice>No products registered yet. Add them in config/products.yaml or the Admin page.</Notice>}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {products?.map((p) => {
          const target = prefs.targets[p.id];
          const ch = p.changes?.["7d"];
          return (
            <Card key={p.id}>
              <div className="flex gap-4">
                {p.image_url && <img src={p.image_url} alt="" className="h-20 w-20 flex-none rounded-lg border border-slate-200 object-contain" />}
                <div className="min-w-0">
                  <Link href={`/products/${p.id}`} className="line-clamp-2 font-semibold text-slate-900 hover:text-brand-700">{p.name}</Link>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">{p.brand} · <StatusDot status={p.tracking_status} /></div>
                </div>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div>
                  <div className="flex items-center gap-1 text-xs text-slate-500">Current <KindTag kind="observed" /></div>
                  <div className="text-2xl font-semibold tabular-nums">{money(p.current?.price)}</div>
                  <div className="text-xs text-slate-500">{p.current ? `seen ${ago(p.current.as_of)}` : "no data"}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-500">Offer price</div>
                  <div className="text-lg font-semibold tabular-nums text-rose-700">{p.current_offer_price ? money(p.current_offer_price.price) : <span className="text-slate-500">None shown</span>}</div>
                  <div className="text-xs text-slate-500">{p.current_offer_price ? "after bank/coupon offers" : "no current offer recorded"}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-500">7-day change</div>
                  <div className={`font-medium ${ch && !ch.unknown && (ch.pct ?? 0) < 0 ? "text-emerald-700" : "text-slate-800"}`}>
                    {ch && !ch.unknown ? `${money(ch.abs)} (${pct(ch.pct)})` : "Unknown"}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-slate-500">Lowest recorded</div>
                  <div className="font-medium">{money(p.historical_low?.price)}</div>
                  <div className="text-xs text-slate-500">{p.historical_low ? date(p.historical_low.first_seen) : ""}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-500">Your target</div>
                  <div className="font-medium">{target ? money(target) : <Link className="text-brand-600 underline" href="/settings">Set target</Link>}</div>
                  {target && p.current && <div className="text-xs text-slate-500">{p.current.price <= target ? "Target reached" : `${money(p.current.price - target)} above`}</div>}
                </div>
                <div>
                  <div className="text-xs text-slate-500">Last collection</div>
                  <div className="text-xs">{dateTime(p.last_collection_at)}</div>
                </div>
              </div>
              {p.last_error && <div className="mt-3"><Notice tone="warn">Last collection error: {p.last_error}</Notice></div>}
              <div className="mt-4 flex gap-2">
                <Link href={`/products/${p.id}`} className="btn-secondary">Details</Link>
                <Link href={`/advisor?product=${p.id}`} className="btn-primary">Should I buy?</Link>
              </div>
            </Card>
          );
        })}
      </div>
      <Card title="Upcoming sale events">
        {!events ? <Loading /> : events.upcoming.length === 0 ? <p className="text-sm text-slate-500">None registered.</p> : (
          <table className="table">
            <thead><tr><th>Event</th><th>Retailer</th><th>Dates</th><th>Status</th></tr></thead>
            <tbody>
              {events.upcoming.map((e, i) => (
                <tr key={i}>
                  <td className="font-medium">{e.event_name}</td><td>{e.retailer}</td>
                  <td>{e.start_date ? `${date(e.start_date)}${e.end_date ? " – " + date(e.end_date) : " (end not announced)"}` : "Not announced"}</td>
                  <td className="text-xs">{e.status_label}{e.source_url && <> · <a className="text-brand-600 underline" href={e.source_url} target="_blank" rel="noreferrer">source</a></>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
