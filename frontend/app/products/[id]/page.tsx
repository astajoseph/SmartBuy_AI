"use client";
import Link from "next/link";
import { use, useState } from "react";
import PriceHistoryChart, { type StoreLine } from "@/components/PriceHistoryChart";
import StoreComparison from "@/components/StoreComparison";
import ProductImage from "@/components/ProductImage";
import { ACTION, ActionBadge, KindTag, Limitations, Loading, Notice } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { usePrefs } from "@/hooks/usePrefs";
import { ago, catLabel, date, dateTime, money, pct } from "@/lib/format";
import type { Action, Forecast, HistorySummary, PricePoint, Seasonality, VendorPriceHistory } from "@/types/api";

const RANGES = [["30 days", 30], ["90 days", 90], ["1 year", 365], ["All", null]] as const;
const TABS = ["Price history", "Forecast", "Sale events", "Data & sources", "Past advice"] as const;
type Tab = (typeof TABS)[number];
type Rec = { id: number; generated_at: string; recommended_action: Action; current_price: string; confidence_level: string };
type ProductDetail = {
  name: string; brand: string | null; category?: string | null; image_url: string | null;
  price_sources: { source_url: string; last_success_at: string | null; adapter_name: string }[];
  vendors: { vendor_name: string; source_url: string }[];
};

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-4 border-b border-slate-100 py-2.5 text-sm last:border-0"><span className="text-slate-600">{k}</span><span className="text-right font-semibold tabular-nums">{v}</span></div>;
}

export default function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [range, setRange] = useState<number | null>(365);
  const [tab, setTab] = useState<Tab>("Price history");
  const [allStores, setAllStores] = useState(false);
  const [storePick, setStorePick] = useState<number | "none" | null>(null);   // null = follow the current-price store
  const { prefs } = usePrefs();
  const target = prefs.targets[id];
  const { data: product } = useApi<ProductDetail>(`/api/products/${id}`);
  const { data: hist, error } = useApi<HistorySummary>(`/api/products/${id}/history`);
  const { data: prices } = useApi<{ series: { selling_price: PricePoint[]; offer_price: PricePoint[] } }>(`/api/products/${id}/prices${range ? `?days=${range}` : ""}`);
  const { data: fc } = useApi<{ forecasts: Forecast[]; note: string }>(`/api/products/${id}/forecast${target ? `?target_price=${target}` : ""}`);
  const { data: season } = useApi<Seasonality>(`/api/products/${id}/seasonality`);
  const { data: recs } = useApi<Rec[]>(`/api/products/${id}/recommendations`);
  const { data: shops } = useApi<VendorPriceHistory>(`/api/products/${id}/vendor-price-history`);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!hist || !product) return <Loading what="Loading product" />;
  const q = hist.data_quality;
  const stated = hist.source_stated_stats ?? {};
  const cur = hist.current?.price ?? null;
  const mrp = stated.mrp?.value ?? null;
  const median = hist.daily_stats_last_365d?.median ?? null;
  const cheaper = hist.current_percentile_last_365d ?? null;
  const vsMedian = cur != null && median ? ((cur - median) / median) * 100 : null;
  const latest = recs?.[0];
  const recStale = latest && cur != null && Math.abs(Number(latest.current_price) - cur) >= 1;
  // Store rows: priced + in stock first (cheapest first); prices older than 2 days are shown greyed with their age.
  const STALE_H = 48;
  const storeRows = (shops?.vendors ?? []).filter((v) => v.is_active).map((v) => ({
    v, price: v.stats.in_stock ? (v.stats.effective_price ?? v.stats.price) : null, stale: v.stats.age_hours > STALE_H,
  })).sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));
  const [showAllStores, setShowAllStores] = [allStores, setAllStores];
  const visibleRows = showAllStores ? storeRows : storeRows.slice(0, 5);
  // Only offer "Go to <store>" when that store is the cheapest *recently checked* price AND nothing shown is cheaper.
  const fresh = storeRows.filter((r) => r.price != null && !r.stale);
  const best = fresh[0] && storeRows[0] === fresh[0] ? fresh[0] : null;
  // Store line for the chart: the picked store, or by default the store the current price comes from.
  const chartStores = (shops?.vendors ?? []).filter((v) => v.points?.some((p) => p.price != null && p.in_stock));
  const pickedId = storePick ?? chartStores.find((v) => v.vendor === hist.current?.store)?.vendor_source_id ?? "none";
  const picked = pickedId === "none" ? undefined : chartStores.find((v) => v.vendor_source_id === pickedId);
  const sinceMs = range ? Date.now() - range * 86_400_000 : -Infinity;
  const storeLine: StoreLine | null = picked ? {
    label: picked.vendor,
    points: (picked.points ?? []).filter((p) => p.price != null && p.in_stock)
      .map((p) => ({ t: new Date(p.t).getTime(), price: p.price as number })).filter((p) => p.t >= sinceMs),
  } : null;
  const ageText = (h: number) => (h < 48 ? `${Math.round(h)} h ago` : `${Math.round(h / 24)} days ago`);
  const tone = latest ? ACTION[latest.recommended_action].tone : "neutral";
  const verdictBox = { good: "bg-good-50 text-good-700", wait: "bg-gold-100 text-gold-800", brand: "bg-brand-50 text-brand-700", neutral: "bg-slate-100 text-slate-700" }[tone];
  const cat = product.category ?? null;

  return (
    <div className="space-y-5">
      <div className="crumb">
        <Link href="/">Home</Link>{cat && <> › <Link href={`/?q=${encodeURIComponent(cat)}`}>{catLabel(cat)}</Link></>}
        {product.brand && <> › <Link href={`/?q=${encodeURIComponent(product.brand)}`}>{product.brand}</Link></>}
      </div>

      <div className="grid gap-6 rounded-lg border border-slate-200 bg-white p-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_340px] lg:p-6">
        <ProductImage src={product.image_url} name={product.name} className="h-80 lg:h-[420px]" />

        <div>
          <h1 className="text-2xl font-bold leading-tight tracking-tight">{product.name}</h1>
          {product.brand && <Link href={`/?q=${encodeURIComponent(product.brand)}`} className="mt-1.5 inline-block text-[13px] font-semibold text-brand-600 hover:underline">More from {product.brand}</Link>}
          <ul className="mt-5 space-y-2 text-sm text-slate-700">
            <li>Price tracked since {date(q.first_record)}</li>
            <li>{q.recorded_points.toLocaleString("en-IN")} prices recorded{q.recorded_days != null && <> on {q.recorded_days.toLocaleString("en-IN")} days</>}</li>
            {product.price_sources[0] && <li>Last checked {dateTime(product.price_sources[0].last_success_at)}</li>}
            {hist.historical_low && <li>Lowest ever {money(hist.historical_low.price)} on {date(hist.historical_low.first_seen)}</li>}
          </ul>
          {Object.keys(stated).length > 0 && (
            <div className="mt-5 rounded-md bg-slate-50 p-3 text-sm">
              <div className="mb-1.5 flex items-center gap-2 text-xs text-slate-500">As stated by the price-history site <KindTag kind="source" /></div>
              <div className="flex flex-wrap gap-x-5 gap-y-1">
                {Object.entries(stated).map(([k, v]) => <span key={k} className="text-slate-700">{k === "mrp" ? "MRP" : k[0].toUpperCase() + k.slice(1).replace("_", " ")} <strong className="tabular-nums">{money(v.value)}</strong></span>)}
              </div>
            </div>
          )}
        </div>

        <aside className="self-start rounded-lg border border-slate-300 p-5" aria-label="Price and where to buy">
          <div className="text-3xl font-bold tabular-nums tracking-tight">{money(cur)}</div>
          {hist.current && <div className="text-[13px] text-slate-600">{hist.current.store ? <>at <a href={hist.current.store_url} target="_blank" rel="noreferrer" className="font-semibold text-brand-600 hover:underline">{hist.current.store}</a>, lowest of our latest store checks · {ago(hist.current.as_of)}</> : <>from the price-history site · {ago(hist.current.as_of)}</>}</div>}
          <div className="mt-0.5 text-[13px]">
            {mrp && cur != null && mrp > cur && <><span className="text-slate-500 line-through">MRP {money(mrp)}</span> <span className="font-semibold text-good-600">{Math.round(((mrp - cur) / mrp) * 100)}% off MRP</span></>}
          </div>
          {hist.current_offer_price && <div className="mt-1 text-[13px] font-semibold text-alert-600">{money(hist.current_offer_price.price)} after bank or coupon offers</div>}

          <div className={`mt-4 rounded-md p-3 ${latest ? verdictBox : "bg-slate-100 text-slate-700"}`}>
            <div className="font-bold">{latest ? ACTION[latest.recommended_action].label : "No advice yet"}</div>
            <p className="mt-0.5 text-[13px] leading-snug text-slate-700">
              {vsMedian != null && median != null
                ? <>{Math.abs(vsMedian) < 0.5 ? "About the same as" : `${Math.abs(vsMedian).toFixed(1)}% ${vsMedian > 0 ? "above" : "below"}`} the usual {money(median)}.{cheaper != null && <> Cheaper on {cheaper.toFixed(0)}% of days last year.</>}</>
                : "Not enough recent history to compare."}
            </p>
            {latest && <p className="mt-1.5 text-xs text-slate-500">Advice from {date(latest.generated_at)} at {money(latest.current_price)}{recStale && " · the price has changed since"}</p>}
          </div>

          <div className="mt-4">
            <h2 className="mb-1 text-[13px] font-bold">Where to buy</h2>
            {storeRows.length ? <>
              {visibleRows.map(({ v, price, stale }) => (
                <a key={v.vendor_source_id} href={v.url} target="_blank" rel="noreferrer" className="flex items-baseline justify-between gap-3 border-b border-slate-100 py-2 text-[13.5px] last:border-0 hover:text-brand-600">
                  <span className="min-w-0 truncate">{v.vendor}</span>
                  {price != null
                    ? <span className={`flex-none text-right tabular-nums ${stale ? "text-slate-400" : "font-semibold"}`}>{money(price)}{stale && <span className="block text-[11px] font-normal">{ageText(v.stats.age_hours)}</span>}</span>
                    : <span className="flex-none text-slate-400">{v.stats.in_stock ? "No price" : "Out of stock"}</span>}
                </a>
              ))}
              {storeRows.length > 5 && <button onClick={() => setShowAllStores(!showAllStores)} className="pt-1.5 text-[13px] font-semibold text-brand-600 hover:underline">{showAllStores ? "Show fewer" : `Show all ${storeRows.length} stores`}</button>}
            </> : product.vendors.length ? product.vendors.slice(0, 5).map((v) => (
              <a key={v.source_url} href={v.source_url} target="_blank" rel="noreferrer" className="flex justify-between border-b border-slate-100 py-2 text-[13.5px] last:border-0 hover:text-brand-600">
                <span>{v.vendor_name}</span><span className="text-slate-400">Not checked yet</span>
              </a>
            )) : <p className="text-[13px] text-slate-500">No stores registered for this product.</p>}
            {storeRows.length > 0 && <p className="mt-1 text-[11px] text-slate-400">Prices from each store's last check. Greyed prices are more than 2 days old.</p>}
          </div>

          <div className="mt-4 space-y-2">
            {best
              ? <a href={best.v.url} target="_blank" rel="noreferrer" className="btn-buy w-full py-2.5">Go to {best.v.vendor} · {money(best.price)}</a>
              : <Link href={`/vendors?product=${id}`} className="btn-buy w-full py-2.5">Compare store prices now</Link>}
            <Link href={`/advisor?product=${id}`} className="btn-secondary w-full">Should I buy now?</Link>
            <Link href="/settings" className="block pt-1 text-center text-[13px] font-semibold text-brand-600 hover:underline">
              {target ? `Your alert: ${money(target)}` : "Set a price alert"}
            </Link>
          </div>
        </aside>
      </div>

      <div>
        <div role="tablist" className="flex overflow-x-auto border-b-2 border-slate-200">
          {TABS.map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
              className={`-mb-0.5 flex-none whitespace-nowrap px-5 py-3 text-sm font-semibold ${tab === t ? "border-b-2 border-brand-600 text-brand-600" : "text-slate-500 hover:text-slate-800"}`}>{t}</button>
          ))}
        </div>
        <div role="tabpanel" className="rounded-b-lg border border-t-0 border-slate-200 bg-white p-5 lg:p-6">
          {tab === "Price history" && (
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
              <div className="min-w-0">
                <div className="mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-3">
                    <h2 className="font-bold">Recorded prices</h2>
                    {chartStores.length > 0 && (
                      <label className="flex items-center gap-2 text-[13px] text-slate-600">Store trend
                        <select className="input w-auto py-1 text-[13px]" value={String(pickedId)}
                          onChange={(e) => setStorePick(e.target.value === "none" ? "none" : Number(e.target.value))}>
                          <option value="none">None</option>
                          {chartStores.map((v) => <option key={v.vendor_source_id} value={v.vendor_source_id}>{v.vendor}</option>)}
                        </select>
                      </label>
                    )}
                  </div>
                  <div className="flex overflow-hidden rounded-md border border-slate-300">
                    {RANGES.map(([l, d]) => (
                      <button key={l} onClick={() => setRange(d)} className={`border-l border-slate-300 px-3 py-1 text-xs font-semibold first:border-l-0 ${range === d ? "bg-brand-600 text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}>{l}</button>
                    ))}
                  </div>
                </div>
                {prices ? <PriceHistoryChart selling={prices.series.selling_price} offer={prices.series.offer_price} events={season?.relevant_events} target={target} store={storeLine}
                  usual={hist.daily_stats_last_365d ? { p10: hist.daily_stats_last_365d.p10, p90: hist.daily_stats_last_365d.p90, median: hist.daily_stats_last_365d.median } : null} /> : <Loading />}
                {shops && shops.vendors.some((v) => v.is_active) && (
                  <div className="mt-6 border-t border-slate-100 pt-4">
                    <StoreComparison vendors={shops.vendors} usual={median} picked={picked?.vendor_source_id ?? null}
                      onPick={(id) => setStorePick(storePick === id || (storePick === null && picked?.vendor_source_id === id) ? "none" : id)} />
                  </div>
                )}
              </div>
              <div>
                <Row k="Price today" v={money(cur)} />
                <Row k="Usual price (1 year)" v={money(median)} />
                <Row k="Lowest ever" v={money(hist.historical_low?.price)} />
                <Row k="Cheaper than today" v={cheaper != null ? `${cheaper.toFixed(0)}% of days` : "Unknown"} />
                {Object.entries(hist.changes ?? {}).map(([k, c]) => (
                  <Row key={k} k={`Change over ${k}`} v={c && !c.unknown ? <span className={(c.pct ?? 0) < 0 ? "text-good-600" : (c.pct ?? 0) > 0 ? "text-alert-600" : ""}>{money(c.abs)} ({pct(c.pct)})</span> : "Unknown"} />
                ))}
              </div>
            </div>
          )}

          {tab === "Forecast" && (!fc ? <Loading /> : (
            <>
              <table className="table">
                <thead><tr><th>In</th><th>Expected price</th><th>80% range</th><th>Method</th>{target && <th>Reach {money(target)}?</th>}</tr></thead>
                <tbody>{fc.forecasts.map((f) => (
                  <tr key={f.horizon_days}>
                    <td>{f.horizon_days} days</td>
                    {f.status === "ok" ? <>
                      <td className="font-semibold tabular-nums">{money(f.predicted_price)}</td>
                      <td className="tabular-nums">{money(f.lower_price_bound)} – {money(f.upper_price_bound)}</td>
                      <td className="text-xs">{f.forecast_method}<div className="text-slate-500">{f.validation_summary?.reason}</div></td>
                      {target && <td className="text-xs">{f.probability_detail?.band ?? f.probability_detail?.reason ?? "—"}{f.probability_detail?.skill_demonstrated === false && <div className="text-gold-800">no backtest skill</div>}</td>}
                    </> : <td colSpan={target ? 4 : 3} className="text-slate-500">{f.message}</td>}
                  </tr>
                ))}</tbody>
              </table>
              <p className="mt-3 flex items-center gap-2 text-xs text-slate-500"><KindTag kind="forecast" /> {fc.note}</p>
              <Limitations items={fc.forecasts[0]?.limitations} />
            </>
          ))}

          {tab === "Sale events" && (!season ? <Loading /> : (
            <>
              {season.event_analysis.by_event.length === 0 ? <p className="text-sm text-slate-500">No past sale windows overlap the recorded history.</p> : (
                <table className="table">
                  <thead><tr><th>Sale</th><th>Past sales</th><th>Price dropped</th><th>Median change</th><th>Typical saving at lowest</th><th>Verdict</th></tr></thead>
                  <tbody>{season.event_analysis.by_event.map((e) => (
                    <tr key={e.event_name}><td className="font-semibold">{e.event_name}</td><td>{e.comparable_events}</td><td>{e.drop_frequency}</td>
                      <td className={(e.median_pct_change ?? 0) < 0 ? "text-good-600" : ""}>{pct(e.median_pct_change)}</td><td>{money(e.median_saving_at_min)}</td>
                      <td className="text-xs">{e.note ?? (e.vs_non_event_periods?.unusually_large ? "Bigger drop than most non-sale periods" : "Not unusual compared with non-sale periods")}</td></tr>
                  ))}</tbody>
                </table>
              )}
              <div className="mt-3 text-sm"><Link className="font-semibold text-brand-600 hover:underline" href={`/sales?product=${id}`}>Open the full sale calendar</Link></div>
              <Limitations items={season.event_analysis.limitations} />
            </>
          ))}

          {tab === "Data & sources" && (
            <div className="grid gap-8 lg:grid-cols-2">
              <div>
                <h2 className="mb-2 font-bold">Data quality</h2>
                <Row k="Recorded prices" v={`${q.recorded_points} on ${q.recorded_days} days`} />
                <Row k="History span" v={`${date(q.first_record)} – ${date(q.last_record)}`} />
                <Row k="Largest gap" v={`${q.max_gap_days ?? "—"} days`} />
                <Row k="Days with no record" v={q.unknown_day_share != null ? `${(q.unknown_day_share * 100).toFixed(0)}%` : "—"} />
                <Row k="Enough data to forecast" v={q.sufficient_for_forecast ? "Yes" : "No"} />
                <Row k="Enough data for seasonality" v={q.sufficient_for_seasonality ? "Yes" : "No"} />
                <Limitations items={hist.limitations} />
              </div>
              <div>
                <h2 className="mb-2 font-bold">Price-history source</h2>
                {product.price_sources.map((s) => (
                  <div key={s.source_url} className="mb-3 text-sm"><a className="break-all text-brand-600 underline" href={s.source_url} target="_blank" rel="noreferrer">{s.source_url}</a>
                    <div className="text-xs text-slate-500">Adapter {s.adapter_name} · last success {dateTime(s.last_success_at)}</div></div>
                ))}
                <h2 className="mb-2 mt-5 font-bold">Stores (checked only when you ask)</h2>
                {product.vendors.length ? product.vendors.map((v) => <div key={v.source_url} className="text-sm">{v.vendor_name}</div>) : <p className="text-sm text-slate-500">No store links registered yet.</p>}
              </div>
            </div>
          )}

          {tab === "Past advice" && (!recs?.length ? <p className="text-sm text-slate-500">No advice given yet. Use “Should I buy now?” to get some.</p> : (
            <table className="table"><thead><tr><th>When</th><th>Advice</th><th>Price then</th><th>Confidence</th></tr></thead>
              <tbody>{recs.map((r) => <tr key={r.id}><td>{dateTime(r.generated_at)}</td><td><ActionBadge action={r.recommended_action} /></td><td className="tabular-nums">{money(r.current_price)}</td><td>{r.confidence_level.toLowerCase()}</td></tr>)}</tbody></table>
          ))}
        </div>
      </div>
    </div>
  );
}
