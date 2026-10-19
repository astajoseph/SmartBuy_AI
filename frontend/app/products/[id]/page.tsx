"use client";
import Link from "next/link";
import { use, useState } from "react";
import PriceChart from "@/components/PriceChart";
import { ActionBadge, Card, KindTag, Limitations, Loading, Notice, Stat } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { usePrefs } from "@/hooks/usePrefs";
import { date, dateTime, money, pct } from "@/lib/format";
import type { Action, Forecast, HistorySummary, PricePoint, Seasonality } from "@/types/api";

const RANGES = [["30d", 30], ["90d", 90], ["1y", 365], ["All", null]] as const;

export default function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [range, setRange] = useState<number | null>(null);
  const { prefs } = usePrefs();
  const target = prefs.targets[id];
  const { data: product } = useApi<{ name: string; brand: string; image_url: string | null; price_sources: { source_url: string; last_success_at: string | null; adapter_name: string }[]; vendors: { vendor_name: string; source_url: string }[] }>(`/api/products/${id}`);
  const { data: hist, error } = useApi<HistorySummary>(`/api/products/${id}/history`);
  const { data: prices } = useApi<{ series: { selling_price: PricePoint[]; offer_price: PricePoint[] } }>(`/api/products/${id}/prices${range ? `?days=${range}` : ""}`);
  const { data: fc } = useApi<{ forecasts: Forecast[]; note: string }>(`/api/products/${id}/forecast${target ? `?target_price=${target}` : ""}`);
  const { data: season } = useApi<Seasonality>(`/api/products/${id}/seasonality`);
  const { data: recs } = useApi<{ id: number; generated_at: string; recommended_action: Action; current_price: string; confidence_level: string }[]>(`/api/products/${id}/recommendations`);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!hist || !product) return <Loading what="Loading product" />;
  const q = hist.data_quality;
  const stated = hist.source_stated_stats ?? {};
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start gap-4">
        {product.image_url && <img src={product.image_url} alt="" className="h-24 w-24 rounded-xl border border-slate-200 bg-white object-contain" />}
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold">{product.name}</h1>
          <p className="text-sm text-slate-600">{product.brand}</p>
        </div>
        <Link href={`/advisor?product=${id}`} className="btn-primary">Get a recommendation</Link>
      </div>

      <Card>
        <div className="grid grid-cols-2 gap-5 md:grid-cols-5">
          <Stat label="Current price" kind="observed" value={money(hist.current?.price)} hint={hist.current ? dateTime(hist.current.as_of) : undefined} />
          {hist.current_offer_price
            ? <Stat label="Offer price" kind="observed" value={money(hist.current_offer_price.price)} hint="after bank/coupon offers" />
            : <Stat label="Offer price" kind="unknown" value="None shown" hint={hist.last_offer_price_seen ? `last seen ${money(hist.last_offer_price_seen.price)} on ${date(hist.last_offer_price_seen.as_of)}` : "no offer recorded"} />}
          <Stat label="Lowest recorded" kind="observed" value={money(hist.historical_low?.price)} hint={hist.historical_low ? date(hist.historical_low.first_seen) : undefined} />
          <Stat label="Median (known days, 1y)" kind="estimate" value={money(hist.daily_stats_last_365d?.median)} hint={hist.daily_stats_last_365d ? `${hist.daily_stats_last_365d.days} known days` : undefined} />
          <Stat label="Cheaper than today" kind="estimate" value={hist.current_percentile_last_365d != null ? `${hist.current_percentile_last_365d.toFixed(0)}% of days` : "Unknown"} hint="last 365 days" />
        </div>
        {Object.keys(stated).length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-slate-100 pt-3 text-sm">
            <KindTag kind="source" />
            {Object.entries(stated).map(([k, v]) => <span key={k} className="text-slate-700">{k.replace("_", " ")}: <strong>{money(v.value)}</strong></span>)}
            <span className="text-xs text-slate-500">as stated by the price-history source</span>
          </div>
        )}
      </Card>

      <Card title="Price history" actions={
        <div className="flex gap-1">{RANGES.map(([l, d]) => (
          <button key={l} onClick={() => setRange(d)} className={`rounded-md px-2.5 py-1 text-xs ${range === d ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}>{l}</button>
        ))}</div>}>
        {prices ? <PriceChart selling={prices.series.selling_price} offer={prices.series.offer_price} events={season?.relevant_events} target={target} /> : <Loading />}
        <p className="mt-2 text-xs text-slate-500">Dots are recorded prices with their original dates. The line steps between records (it is not an observed daily price) and breaks where no price was recorded for over 30 days. Shaded bands are sale windows.</p>
        <div className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
          {Object.entries(hist.changes ?? {}).map(([k, c]) => (
            <div key={k} className="rounded-lg bg-slate-50 p-2">
              <div className="text-xs text-slate-500">Change over {k}</div>
              <div className="font-medium">{c && !c.unknown ? `${money(c.abs)} (${pct(c.pct)})` : "Unknown"}</div>
              {c?.unknown && <div className="text-[11px] text-slate-500">{c.reason}</div>}
              {c && !c.unknown && c.reference_was_recorded === false && <div className="text-[11px] text-slate-500">start value carried forward from an earlier record</div>}
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Forecast">
          {!fc ? <Loading /> : (
            <>
              <table className="table">
                <thead><tr><th>Horizon</th><th>Point</th><th>80% range</th><th>Method</th>{target && <th>Reach {money(target)}?</th>}</tr></thead>
                <tbody>{fc.forecasts.map((f) => (
                  <tr key={f.horizon_days}>
                    <td>{f.horizon_days} days</td>
                    {f.status === "ok" ? <>
                      <td className="tabular-nums">{money(f.predicted_price)}</td>
                      <td className="tabular-nums">{money(f.lower_price_bound)} – {money(f.upper_price_bound)}</td>
                      <td className="text-xs">{f.forecast_method}<div className="text-slate-500">{f.validation_summary?.reason}</div></td>
                      {target && <td className="text-xs">{f.probability_detail?.band ?? f.probability_detail?.reason ?? "—"}{f.probability_detail?.skill_demonstrated === false && <div className="text-amber-700">no backtest skill</div>}</td>}
                    </> : <td colSpan={target ? 4 : 3} className="text-slate-500">{f.message}</td>}
                  </tr>
                ))}</tbody>
              </table>
              <p className="mt-2 text-xs text-slate-500"><KindTag kind="forecast" /> {fc.note}</p>
              <Limitations items={fc.forecasts[0]?.limitations} />
            </>
          )}
        </Card>

        <Card title="Data quality & sources">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <dt className="text-slate-500">Recorded prices</dt><dd>{q.recorded_points} on {q.recorded_days} days</dd>
            <dt className="text-slate-500">History span</dt><dd>{date(q.first_record)} – {date(q.last_record)}</dd>
            <dt className="text-slate-500">Largest gap</dt><dd>{q.max_gap_days ?? "—"} days</dd>
            <dt className="text-slate-500">Unknown days</dt><dd>{q.unknown_day_share != null ? `${(q.unknown_day_share * 100).toFixed(0)}%` : "—"}</dd>
            <dt className="text-slate-500">Forecast possible</dt><dd>{q.sufficient_for_forecast ? "Yes" : "No"}</dd>
            <dt className="text-slate-500">Span long enough for seasonality</dt><dd>{q.sufficient_for_seasonality ? "Yes" : "No"}</dd>
          </dl>
          <h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Price-history source</h3>
          {product.price_sources.map((s) => (
            <div key={s.source_url} className="text-sm"><a className="break-all text-brand-600 underline" href={s.source_url} target="_blank" rel="noreferrer">{s.source_url}</a>
              <div className="text-xs text-slate-500">adapter {s.adapter_name} · last success {dateTime(s.last_success_at)}</div></div>
          ))}
          <h3 className="mt-3 text-xs font-semibold uppercase text-slate-500">Vendors (checked only on request)</h3>
          {product.vendors.length ? product.vendors.map((v) => <div key={v.source_url} className="text-sm">{v.vendor_name}</div>) : <p className="text-sm text-slate-500">No vendor URLs registered yet.</p>}
          <Limitations items={hist.limitations} />
        </Card>
      </div>

      <Card title="Sale-event analysis">
        {!season ? <Loading /> : (
          <>
            {season.event_analysis.by_event.length === 0 ? <p className="text-sm text-slate-500">No past sale windows overlap the recorded history.</p> : (
              <table className="table">
                <thead><tr><th>Event</th><th>Comparable events</th><th>Price dropped</th><th>Median change</th><th>Typical saving at lowest</th><th>Assessment</th></tr></thead>
                <tbody>{season.event_analysis.by_event.map((e) => (
                  <tr key={e.event_name}><td className="font-medium">{e.event_name}</td><td>{e.comparable_events}</td><td>{e.drop_frequency}</td>
                    <td>{pct(e.median_pct_change)}</td><td>{money(e.median_saving_at_min)}</td>
                    <td className="text-xs">{e.note ?? (e.vs_non_event_periods?.unusually_large ? "Larger drop than most non-sale periods" : "Not unusual vs non-sale periods")}</td></tr>
                ))}</tbody>
              </table>
            )}
            <div className="mt-3 text-xs text-slate-500"><Link className="text-brand-600 underline" href={`/sales?product=${id}`}>Open full sale intelligence</Link></div>
            <Limitations items={season.event_analysis.limitations} />
          </>
        )}
      </Card>

      <Card title="Recommendation history">
        {!recs?.length ? <p className="text-sm text-slate-500">No recommendations yet.</p> : (
          <table className="table"><thead><tr><th>When</th><th>Action</th><th>Price then</th><th>Confidence</th></tr></thead>
            <tbody>{recs.map((r) => <tr key={r.id}><td>{dateTime(r.generated_at)}</td><td><ActionBadge action={r.recommended_action} /></td><td>{money(r.current_price)}</td><td>{r.confidence_level}</td></tr>)}</tbody></table>
        )}
      </Card>
    </div>
  );
}
