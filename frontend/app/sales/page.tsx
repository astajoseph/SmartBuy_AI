"use client";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import ProductPicker from "@/components/ProductPicker";
import { Card, Limitations, Loading } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { date, money, pct } from "@/lib/format";
import type { PatternTest, ProductSummary, SaleEvent, Seasonality } from "@/types/api";

const STATUS_STYLE: Record<string, string> = {
  confirmed_official: "bg-emerald-50 text-emerald-800", reported_by_media: "bg-sky-50 text-sky-800",
  observed_on_source_chart: "bg-violet-50 text-violet-800", recurring_unconfirmed: "bg-slate-100 text-slate-700",
  calendar_date: "bg-teal-50 text-teal-800", hypothetical: "bg-amber-50 text-amber-800",
};
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Pattern({ title, t, labels }: { title: string; t: PatternTest | null; labels?: (k: string) => string }) {
  if (!t) return null;
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">{title}</h3>
        <span className={`rounded px-2 py-0.5 text-xs ${t.supported ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}`}>{t.supported ? "Pattern supported" : "No reliable pattern"}</span></div>
      <p className="mt-1 text-xs text-slate-600">{t.reason}{t.p_value != null && ` (p = ${t.p_value})`}</p>
      {t.groups && <div className="mt-2 flex flex-wrap gap-1 text-[11px]">{Object.entries(t.groups).map(([k, g]) => (
        <span key={k} className="rounded bg-slate-50 px-1.5 py-0.5">{labels ? labels(k) : k}: n={g.n}{g.median_rel_pct != null && `, ${pct(g.median_rel_pct)}`}</span>))}</div>}
      {t.note && <p className="mt-1 text-[11px] text-slate-500">{t.note}</p>}
    </div>
  );
}

function Sales() {
  const params = useSearchParams();
  const { data: products } = useApi<ProductSummary[]>("/api/products");
  const [pid, setPid] = useState(params.get("product") ?? "");
  const { data: events } = useApi<{ events: SaleEvent[] }>("/api/events");
  const { data: s } = useApi<Seasonality>(pid ? `/api/products/${pid}/seasonality` : null);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Sale intelligence</h1>
        <p className="text-sm text-slate-600">Indian sale events and how this product's recorded price behaved around them. Associations are not proof that a sale caused a price change.</p>
      </div>
      <Card title="Event registry">
        {!events ? <Loading /> : (
          <div className="overflow-x-auto"><table className="table">
            <thead><tr><th>Event</th><th>Retailer</th><th>Dates</th><th>Status</th><th>Source</th></tr></thead>
            <tbody>{events.events.map((e, i) => (
              <tr key={i}><td className="font-medium">{e.event_name}</td><td>{e.retailer}</td>
                <td>{e.start_date ? `${date(e.start_date)} – ${e.end_date ? date(e.end_date) : "end not announced"}` : <span className="text-slate-500">dates not confirmed</span>}</td>
                <td><span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLE[e.verification_status] ?? ""}`}>{e.status_label}</span></td>
                <td>{e.source_url ? <a className="text-xs text-brand-600 underline" href={e.source_url} target="_blank" rel="noreferrer">link</a> : <span className="text-xs text-slate-400">—</span>}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
      <Card><div className="max-w-md"><ProductPicker products={products} value={pid} onChange={setPid} /></div></Card>
      {pid && !s && <Loading />}
      {s && (
        <>
          <Card title="Past event windows (14 days before · event · 14 days after)">
            {s.event_analysis.events.length === 0 ? <p className="text-sm text-slate-500">No sale window overlaps the recorded history.</p> : (
              <div className="overflow-x-auto"><table className="table">
                <thead><tr><th>Event</th><th>Window</th><th>Median before</th><th>Lowest during</th><th>Median during</th><th>Median after</th><th>Change</th><th>Data</th></tr></thead>
                <tbody>{s.event_analysis.events.map((e, i) => (
                  <tr key={i}><td className="font-medium">{e.event_name}</td><td className="text-xs">{date(e.start)} – {date(e.end)}</td>
                    <td>{money(e.median_before)}</td><td>{money(e.min_during)}</td><td>{money(e.median_during)}</td><td>{money(e.median_after)}</td>
                    <td className={e.pct_change_median < 0 ? "text-emerald-700" : ""}>{pct(e.pct_change_median)}</td>
                    <td className="text-xs">{e.recorded_points_in_windows} recorded · {e.data_quality}</td></tr>
                ))}</tbody>
              </table></div>
            )}
            {!!s.event_analysis.skipped_events?.length && (
              <details className="mt-3 text-xs text-slate-600"><summary className="cursor-pointer">Events not analysed ({s.event_analysis.skipped_events.length})</summary>
                <ul className="mt-1 list-disc pl-5">{s.event_analysis.skipped_events.map((e, i) => <li key={i}>{e.event_name} ({date(e.start)}): {e.reason}</li>)}</ul></details>
            )}
            <Limitations items={s.event_analysis.limitations} />
          </Card>
          <Card title="Summary by event">
            {s.event_analysis.by_event.length === 0 ? <p className="text-sm text-slate-500">Not enough well-recorded event windows.</p> :
              s.event_analysis.by_event.map((e) => (
                <div key={e.event_name} className="mb-2 rounded-lg bg-slate-50 p-3 text-sm">
                  <strong>{e.event_name}</strong>: price dropped in {e.drop_frequency} comparable events; median change {pct(e.median_pct_change)}, typical saving at the lowest point {money(e.median_saving_at_min)}.
                  {e.note && <div className="text-xs text-amber-800">{e.note}</div>}
                </div>
              ))}
          </Card>
          <Card title="Calendar patterns">
            <div className="grid gap-3 md:grid-cols-3">
              <Pattern title="Day of week" t={s.calendar_patterns.day_of_week} labels={(k) => DOW[Number(k)] ?? k} />
              <Pattern title="Month" t={s.calendar_patterns.month} />
              <Pattern title="Month-end" t={s.calendar_patterns.month_end} />
            </div>
            <p className="mt-2 text-xs text-slate-500">{s.calendar_patterns.method}</p>
          </Card>
        </>
      )}
    </div>
  );
}

export default function SalesPage() {
  return <Suspense fallback={<Loading />}><Sales /></Suspense>;
}
