"use client";
import { useState } from "react";
import ProductPicker from "@/components/ProductPicker";
import { Card, Disclaimer, KindTag, Loading, Notice } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { usePrefs } from "@/hooks/usePrefs";
import { api } from "@/lib/api";
import { dateTime, money } from "@/lib/format";
import type { ProductSummary, SellerList, VendorComparison } from "@/types/api";

export default function VendorsPage() {
  const { data: products } = useApi<ProductSummary[]>("/api/products");
  const { prefs } = usePrefs();
  const [pid, setPid] = useState("");
  const [objective, setObjective] = useState(prefs.objective);
  const [maxEmi, setMaxEmi] = useState("");
  const [data, setData] = useState<VendorComparison | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showGone, setShowGone] = useState(false);
  const { data: sellers } = useApi<SellerList>(pid ? `/api/products/${pid}/sellers?include_inactive=${showGone}` : null);

  async function load(refresh = false) {
    if (!pid) return;
    setBusy(true); setErr(null);
    const q = new URLSearchParams({ objective, refresh: String(refresh) });
    if (maxEmi) q.set("max_monthly_emi", maxEmi);
    if (prefs.banks.length) q.set("banks", prefs.banks.join(","));
    try { setData(await api<VendorComparison>(`/api/products/${pid}/vendor-comparison?${q}`)); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Vendors &amp; EMI comparison</h1>
        <p className="text-sm text-slate-600">Vendor pages are fetched only when you press Compare and the cached data is older than its refresh window (EMI 12 h, price 1 h).</p>
      </div>
      <Card>
        <div className="grid items-end gap-3 md:grid-cols-4">
          <ProductPicker products={products} value={pid} onChange={setPid} />
          <div><label className="label" htmlFor="obj">Priority</label>
            <select id="obj" className="input" value={objective} onChange={(e) => setObjective(e.target.value as typeof objective)}>
              <option value="lowest_total_cost">Lowest total cost</option><option value="lowest_monthly_payment">Lowest monthly payment</option>
              <option value="shortest_tenure">Shortest repayment</option><option value="lowest_interest">Lowest interest</option><option value="balanced">Balanced</option>
            </select></div>
          <div><label className="label" htmlFor="me">Max monthly EMI (₹)</label><input id="me" className="input" inputMode="numeric" value={maxEmi} onChange={(e) => setMaxEmi(e.target.value)} /></div>
          <div className="flex gap-2"><button className="btn-primary" disabled={!pid || busy} onClick={() => load(false)}>Compare</button>
            <button className="btn-secondary" disabled={!pid || busy} onClick={() => load(true)} title="Fetch vendor pages again now">Refresh</button></div>
        </div>
      </Card>
      {sellers && (sellers.sellers.length > 0 || sellers.discovered_sites.length > 0) && (
        <Card title="All sellers found" actions={<label className="flex items-center gap-2 text-xs text-slate-600"><input type="checkbox" checked={showGone} onChange={(e) => setShowGone(e.target.checked)} />show sellers that stopped</label>}>
          <p className="mb-2 text-xs text-slate-500">Checked automatically every 6 hours{sellers.last_checked_at && <> (last {dateTime(sellers.last_checked_at)})</>}. A seller or site not in stock for 30 days is removed. Prices are as listed at that check.</p>
          {sellers.sellers.length > 0 && <div className="overflow-x-auto"><table className="table">
            <thead><tr><th>Seller</th><th>On</th><th>Price</th><th>Stock</th><th>Shipped by</th><th>Seller rating</th><th>First seen</th></tr></thead>
            <tbody>{sellers.sellers.map((s) => (
              <tr key={s.id} className={s.is_active ? "" : "text-slate-400"}>
                <td className="font-medium">{s.seller_url ? <a className="underline" href={s.seller_url} target="_blank" rel="noreferrer">{s.seller_name ?? s.seller_key}</a> : (s.seller_name ?? s.seller_key)}
                  {!s.is_active && <div className="text-[11px]">{s.deactivation_reason}</div>}</td>
                <td className="text-xs">{s.marketplace}</td>
                <td>{money(s.price)}{s.min_quantity && s.min_quantity > 1 && <div className="text-[11px] text-amber-700">min. {s.min_quantity} units</div>}</td>
                <td className="text-xs">{s.in_stock ? "In stock" : s.in_stock === false ? "Not available" : "Unknown"}</td>
                <td className="text-xs">{s.fulfilled_by ?? "—"}</td>
                <td className="text-xs">{s.seller_rating ? `${s.seller_rating}/5 (${s.seller_rating_count ?? "?"})` : "—"}{s.seller_positive_pct && <div>{s.seller_positive_pct}% positive</div>}</td>
                <td className="text-xs">{dateTime(s.first_seen_at)}</td>
              </tr>
            ))}</tbody>
          </table></div>}
          {sellers.discovered_sites.length > 0 && <div className="mt-3 text-sm"><div className="font-medium">Other shops found by web search</div>
            <ul className="mt-1 space-y-1 text-xs">{sellers.discovered_sites.map((d) => <li key={d.id}><a className="text-brand-600 underline" href={d.source_url} target="_blank" rel="noreferrer">{d.vendor_name}</a> · last in stock {dateTime(d.last_in_stock_at)} · included in Compare</li>)}</ul></div>}
        </Card>
      )}
      {err && <Notice tone="error">{err}</Notice>}
      {busy && <Loading what="Checking cache and vendor pages" />}
      {data?.message && <Notice>{data.message}</Notice>}
      {data && data.vendors.length > 0 && (
        <>
          <Card title="Vendor overview">
            <div className="overflow-x-auto"><table className="table">
              <thead><tr><th>Vendor</th><th>Price</th><th>MRP</th><th>EMI</th><th>Availability</th><th>Seller &amp; returns</th><th>Data freshness</th></tr></thead>
              <tbody>{data.vendors.map((v) => (
                <tr key={v.vendor}>
                  <td className="font-medium">{v.vendor}{v.source_url && <div><a className="text-xs text-brand-600 underline" href={v.source_url} target="_blank" rel="noreferrer">page</a></div>}</td>
                  <td><span className="font-semibold">{money(v.displayed_price)}</span> <KindTag kind={v.displayed_price ? "vendor" : "unknown"} />
                    {v.details?.discount_ends_at && <div className="text-[11px] text-amber-700">discount ends {dateTime(v.details.discount_ends_at)}</div>}
                    {v.notes?.map((n, i) => <div key={i} className="text-[11px] text-slate-500">{n}</div>)}</td>
                  <td>{money(v.mrp)}{v.discount_pct && <div className="text-[11px] text-emerald-700">{v.discount_pct}% off</div>}</td>
                  <td className="max-w-[12rem] text-xs">{v.emi_status_label ?? "—"}</td>
                  <td>{v.availability ?? "Unknown"}{v.details?.stock_units != null && <div className="text-[11px] text-slate-500">{v.details.stock_units} units listed</div>}</td>
                  <td className="max-w-xs text-xs">{v.seller ?? "Unknown"}{v.return_policy && <div className="mt-1 text-[11px] text-slate-500">{v.return_policy}</div>}</td>
                  <td className={`text-xs ${v.status === "stale_cache" ? "font-medium text-amber-700" : v.status === "unavailable" ? "text-rose-700" : "text-slate-600"}`}>
                    {v.freshness?.label ?? v.message}{v.message && v.freshness && <div>{v.message}</div>}</td>
                </tr>
              ))}</tbody>
            </table></div>
          </Card>
          <Card title="Bank, cashback & partner offers">
            {data.vendors.every((v) => !v.bank_offers?.length) ? <p className="text-sm text-slate-500">No offers were listed on the vendor pages.</p> : (
              <div className="overflow-x-auto"><table className="table">
                <thead><tr><th>Vendor</th><th>Offer</th><th>Conditions</th><th>Estimated saving here</th><th>Your bank?</th></tr></thead>
                <tbody>{data.vendors.flatMap((v) => (v.bank_offers ?? []).map((o, i) => (
                  <tr key={`${v.vendor}-${i}`}>
                    <td className="font-medium">{v.vendor}<div className="text-[11px] text-slate-500">{o.category}</div></td>
                    <td className="max-w-sm">{o.title}{o.description && <div className="text-xs text-slate-500">{o.description}</div>}</td>
                    <td className="text-xs">{o.min_spend ? `Min spend ${money(o.min_spend)}` : "Min spend not shown"}{o.max_discount && <div>Max {money(o.max_discount)}</div>}{!o.terms_complete && <div className="text-amber-700">full terms not on page</div>}</td>
                    <td>{o.estimated_saving != null ? <>{money(o.estimated_saving, true)} <KindTag kind="estimate" /></> : <KindTag kind="unknown" />}<div className="max-w-xs text-[11px] text-slate-500">{o.saving_note}</div></td>
                    <td className="text-xs">{o.bank ? (o.you_hold_this_bank === true ? "Yes" : o.you_hold_this_bank === false ? "No" : "Not set (Settings)") : "Any"}</td>
                  </tr>
                )))}</tbody>
              </table></div>
            )}
          </Card>
          <Card title="EMI plans">
            {data.emi_summary && <div className="mb-3"><Notice>{data.emi_summary}</Notice></div>}
            {data.vendors.every((v) => !v.emi_options?.length) ? <p className="text-sm text-slate-500">No EMI plans to compare.</p> : (
              <div className="overflow-x-auto"><table className="table">
                <thead><tr><th>Vendor / bank</th><th>Tenure</th><th>Monthly EMI</th><th>Rate</th><th>Total interest</th><th>Fees + GST</th><th>Total cost</th><th>vs paying upfront</th><th>Notes</th></tr></thead>
                <tbody>{data.vendors.flatMap((v) => (v.emi_options ?? []).map((o) => {
                  const c = o.calculation;
                  return (
                    <tr key={o.emi_offer_id} className={o.conditional_on_bank ? "bg-slate-50" : ""}>
                      <td><div className="font-medium">{v.vendor}</div><div className="text-xs text-slate-500">{o.bank ?? "Any eligible"} {o.card_type?.replace("_", " ")}</div>{o.conditional_on_bank && <div className="text-[11px] text-amber-700">Conditional: needs this bank</div>}</td>
                      <td>{o.tenure_months ? `${o.tenure_months} mo` : "Unknown"}</td>
                      <td>{money(c.monthly_emi ?? o.advertised_monthly_emi, true)} <div className="text-[11px] text-slate-500">{c.emi_source === "advertised" ? "advertised" : c.emi_source === "calculated" ? "calculated" : ""}</div></td>
                      <td>{o.interest_rate_pct ? `${o.interest_rate_pct}% ${o.rate_type?.replace("_", " ")}` : o.no_cost_claim ? "No-cost claim" : "Unknown"}{c.implied_annual_rate_pct && <div className="text-[11px] text-slate-500">implied {c.implied_annual_rate_pct}% p.a.</div>}</td>
                      <td>{money(c.total_interest, true)}</td>
                      <td>{c.processing_fee ? money(Number(c.processing_fee) + Number(c.processing_fee_gst ?? 0), true) : "Unknown"}</td>
                      <td className="font-medium">{money(c.total_cost, true)} <KindTag kind="estimate" /></td>
                      <td>{money(c.extra_cost_vs_upfront, true)}</td>
                      <td className="max-w-xs text-[11px] text-slate-600">{o.label}{c.no_cost_verdict && <div className="font-medium text-amber-800">{c.no_cost_verdict}</div>}{[...c.assumptions, ...c.warnings].map((a, i) => <div key={i}>• {a}</div>)}{o.offer_valid_until && <div>Valid until {dateTime(o.offer_valid_until)}</div>}</td>
                    </tr>
                  );
                }))}</tbody>
              </table></div>
            )}
          </Card>
          <Card title={`Best options — ${data.objective?.replace(/_/g, " ")}`}>
            <ol className="list-decimal space-y-1 pl-5 text-sm">
              {data.ranked_options?.map((o, i) => (
                <li key={i}><strong>{o.vendor_name}</strong> — {o.kind === "upfront" ? "pay upfront" : `${o.tenure_months ?? "?"}-month EMI of ${money(o.monthly_payment, true)}`} · total {money(o.total_cost, true)}{!o.complete && <span className="text-amber-700"> (incomplete terms)</span>}</li>
              ))}
            </ol>
            {!!data.excluded_options?.length && <div className="mt-3 text-sm"><div className="font-medium">Excluded by your monthly limit</div>{data.excluded_options.map((e, i) => <div key={i} className="text-slate-600">{e.option.vendor_name}: {e.reason}</div>)}</div>}
            {!!data.conditional_options?.length && <div className="mt-3 text-sm text-slate-600">{data.conditional_options.length} bank-specific offer(s) are shown separately because you have not said you hold that bank's card (Settings).</div>}
            {data.tenure_tradeoff && (
              <div className="mt-4">
                <div className="text-sm font-medium">Tenure trade-off at {data.tenure_tradeoff.annual_rate_pct}% p.a.</div>
                <p className="text-xs text-slate-600">{data.tenure_tradeoff.explanation}</p>
                <table className="table mt-1"><thead><tr><th>Months</th><th>Monthly EMI</th><th>Total paid</th><th>Interest</th></tr></thead>
                  <tbody>{data.tenure_tradeoff.rows.map((r) => <tr key={r.tenure_months}><td>{r.tenure_months}</td><td>{money(r.monthly_emi, true)}</td><td>{money(r.total_paid, true)}</td><td>{money(r.total_interest, true)}</td></tr>)}</tbody></table>
              </div>
            )}
            <div className="mt-3"><Disclaimer /></div>
          </Card>
        </>
      )}
    </div>
  );
}
