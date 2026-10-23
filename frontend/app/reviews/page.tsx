"use client";
import { useState } from "react";
import ProductPicker from "@/components/ProductPicker";
import { Card, Limitations, Loading, Notice } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { aspectLabel, date, dateTime } from "@/lib/format";
import type { ProductSummary, ReviewBlock, ReviewExcerpt, ReviewIntegrity, SellerReputation, VendorReviews } from "@/types/api";

const FLAG_LABEL: Record<string, string> = { incentivized: "free / discounted product", copied: "copied text", unverified: "not a verified purchase",
  generic: "generic praise", mismatch: "stars contradict text", burst: "burst of ratings", repeat_author: "same reviewer repeatedly" };
const TRUST_STYLE = { likely_genuine: "text-emerald-700", uncertain: "text-amber-700", suspicious: "text-rose-700" } as const;
const LABEL_STYLE = { organic: "bg-emerald-50 text-emerald-800", mixed: "bg-amber-50 text-amber-800", doubtful: "bg-rose-50 text-rose-800" } as const;

function Integrity({ i }: { i: ReviewIntegrity }) {
  const flags = Object.entries(i.flag_counts).filter(([, n]) => n > 0);
  return (
    <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Review integrity</span>
        {i.integrity_label && <span className={`rounded px-2 py-0.5 text-xs font-semibold ${LABEL_STYLE[i.integrity_label]}`}>{i.integrity_label} · {i.integrity_score}/100</span>}
        <span className="text-xs text-slate-500">confidence {i.confidence}</span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <div><div className="text-slate-500">Looks genuine / uncertain / suspicious</div><div className="font-medium">{i.likely_genuine} / {i.uncertain} / {i.suspicious} <span className="font-normal text-slate-500">of {i.counted}</span></div></div>
        <div><div className="text-slate-500">Rating of reviews read → trust-weighted</div><div className="font-medium">{i.sample_avg_rating ?? "—"} → {i.trust_weighted_rating ?? "—"}★</div></div>
        <div><div className="text-slate-500">Sentiment → trust-weighted</div><div className="font-medium">{i.sample_sentiment ?? "—"} → {i.trust_weighted_sentiment ?? "—"}</div></div>
        <div><div className="text-slate-500">Verified purchases</div><div className="font-medium">{i.verified_share != null ? `${Math.round(i.verified_share * 100)}%` : "not shown"}</div></div>
      </div>
      {i.rating_distribution && (
        <div className="mt-2 space-y-0.5">{["5", "4", "3", "2", "1"].map((k) => (
          <div key={k} className="flex items-center gap-2 text-xs"><span className="w-6">{k}★</span>
            <div className="h-2 flex-1 rounded bg-slate-200"><div className="h-2 rounded bg-brand-600" style={{ width: `${i.rating_distribution![k] ?? 0}%` }} /></div>
            <span className="w-10 text-right tabular-nums">{i.rating_distribution![k] ?? 0}%</span></div>))}</div>
      )}
      <div className="mt-2 text-xs"><span className="text-slate-500">Rating growth: </span>
        {i.growth.status}{i.growth.ratings_per_day != null && <> · {i.growth.ratings_per_day} new ratings/day ({i.growth.growth_pct_per_month}%/month)</>} · {i.growth.snapshots} snapshot(s)</div>
      {!!flags.length && <div className="mt-2 text-xs"><span className="text-slate-500">Signals found: </span>{flags.map(([f, n]) => `${FLAG_LABEL[f] ?? f} ×${n}`).join(" · ")}</div>}
      {!!i.vendor_flags.length && <ul className="mt-2 list-disc pl-5 text-xs text-rose-700">{i.vendor_flags.map((f) => <li key={f}>{f}</li>)}</ul>}
      {i.other_variant_reviews > 0 && <div className="mt-1 text-xs text-slate-500">{i.other_variant_reviews} review(s) of another pack size were not counted.</div>}
    </div>
  );
}

function Quote({ e }: { e: ReviewExcerpt }) {
  return (
    <blockquote className="border-l-2 border-slate-300 pl-3 text-sm text-slate-700">“{e.text}”
      <span className="ml-1 text-xs text-slate-500">{e.rating != null ? `${e.rating}★` : ""} {e.date ? date(e.date) : ""} {e.verified ? "· verified" : ""}</span>
      {e.trust && <span className={`ml-1 text-xs font-medium ${TRUST_STYLE[e.trust]}`}>· {e.trust.replace("_", " ")}</span>}
      {!!e.reasons?.length && <div className="text-[11px] text-slate-500">{e.reasons.join("; ")}</div>}
    </blockquote>
  );
}

function Block({ title, b }: { title: string; b: ReviewBlock }) {
  return (
    <div className="rounded-xl border border-slate-200 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">{title}</h3>
        <a href={b.source_url} className="text-xs text-brand-600 underline" target="_blank" rel="noreferrer">source</a>
      </div>
      <div className="mt-2 flex flex-wrap gap-6 text-sm">
        <div><div className="text-xs text-slate-500">Aggregate rating</div><div className="text-xl font-semibold">{b.aggregate_rating != null ? `${b.aggregate_rating} / ${b.rating_scale ?? 5}` : "Unknown"}</div></div>
        <div><div className="text-xs text-slate-500">Ratings</div><div className="text-xl font-semibold">{b.rating_count?.toLocaleString("en-IN") ?? "Unknown"}</div></div>
        <div><div className="text-xs text-slate-500">Reviews analysed</div><div className="text-xl font-semibold">{b.reviews_analysed}</div></div>
        <div><div className="text-xs text-slate-500">Review dates</div><div className="text-sm">{b.review_date_range ? `${date(b.review_date_range[0])} – ${date(b.review_date_range[1])}` : "Unknown"}</div></div>
      </div>
      {(b.positive_themes?.length || b.negative_themes?.length) ? (
        <div className="mt-3 grid gap-3 md:grid-cols-2 text-sm">
          <div><div className="font-medium text-emerald-700">Positive themes</div>{b.positive_themes?.length ? b.positive_themes.map((t) => <div key={t.aspect}>{aspectLabel(t.aspect)} <span className="text-xs text-slate-500">({t.mentions} mentions)</span></div>) : <div className="text-slate-500">None recurring</div>}</div>
          <div><div className="font-medium text-rose-700">Negative themes</div>{b.negative_themes?.length ? b.negative_themes.map((t) => <div key={t.aspect}>{aspectLabel(t.aspect)} <span className="text-xs text-slate-500">({t.mentions} mentions)</span></div>) : <div className="text-slate-500">None recurring</div>}</div>
        </div>
      ) : null}
      {b.integrity && <Integrity i={b.integrity} />}
      {!!b.excerpts?.length && (
        <div className="mt-3 space-y-2"><div className="text-xs font-medium text-slate-600">Genuine-looking reviews</div>{b.excerpts.map((e, i) => <Quote key={i} e={e} />)}</div>
      )}
      {!!b.flagged_examples?.length && (
        <div className="mt-3 space-y-2"><div className="text-xs font-medium text-slate-600">Flagged reviews (suspicious, not proven fake)</div>{b.flagged_examples.map((e, i) => <Quote key={i} e={e} />)}</div>
      )}
      <div className="mt-2 text-xs text-slate-500">Fetched {dateTime(b.fetched_at)} · {b.method}</div>
      <Limitations items={b.limitations} />
    </div>
  );
}

export default function ReviewsPage() {
  const { data: products } = useApi<ProductSummary[]>("/api/products");
  const [pid, setPid] = useState("");
  const [data, setData] = useState<{ vendors: VendorReviews[]; message: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const { data: rep } = useApi<{ sellers: SellerReputation[]; note: string }>(pid ? `/api/products/${pid}/seller-reputation` : null);
  async function load(refresh = false) {
    setBusy(true); setErr(null);
    try { setData(await api(`/api/products/${pid}/reviews?refresh=${refresh}`)); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Review intelligence</h1>
        <p className="text-sm text-slate-600">Product reviews and seller reviews are kept separate. Only reviews actually retrieved from vendor pages are analysed; quotes are real excerpts.
          Each review is checked for signs of manipulation (copied text, free-product reviews, generic praise, stars that contradict the text, bursts, unverified buyers); suspicious reviews are down-weighted, not hidden.</p>
      </div>
      <Card><div className="grid items-end gap-3 md:grid-cols-3">
        <ProductPicker products={products} value={pid} onChange={setPid} />
        <div className="flex gap-2"><button className="btn-primary" disabled={!pid || busy} onClick={() => load(false)}>Analyse reviews</button>
          <button className="btn-secondary" disabled={!pid || busy} onClick={() => load(true)}>Refresh</button></div>
      </div></Card>
      {err && <Notice tone="error">{err}</Notice>}
      {busy && <Loading what="Checking cache and vendor pages" />}
      {data?.message && <Notice>{data.message}</Notice>}
      {!!rep?.sellers.length && (
        <Card title="Seller reputation growth (marketplace sub-sellers)">
          <p className="mb-2 text-xs text-slate-500">{rep.note}</p>
          <div className="overflow-x-auto"><table className="table">
            <thead><tr><th>Seller</th><th>On</th><th>Trend</th><th>Seller rating</th><th>New ratings / day</th><th>% positive</th><th>Flags</th></tr></thead>
            <tbody>{rep.sellers.map((s) => (
              <tr key={s.seller_id} className={s.is_active ? "" : "text-slate-400"}>
                <td className="font-medium">{s.seller_name}</td><td className="text-xs">{s.marketplace}</td>
                <td className={`text-xs font-medium ${s.status === "irregular" ? "text-rose-700" : s.status === "growing steadily" ? "text-emerald-700" : "text-slate-600"}`}>{s.status}</td>
                <td className="text-xs">{s.rating ?? "—"}/5 ({s.ratings?.toLocaleString("en-IN") ?? "?"})</td>
                <td className="text-xs tabular-nums">{s.ratings_per_day ?? "—"}</td>
                <td className="text-xs">{s.positive_pct != null ? `${s.positive_pct}%` : "—"}{s.positive_pct_change != null && s.positive_pct_change !== 0 && <span className={s.positive_pct_change < 0 ? "text-rose-700" : "text-emerald-700"}> ({s.positive_pct_change > 0 ? "+" : ""}{s.positive_pct_change})</span>}</td>
                <td className="max-w-xs text-xs text-rose-700">{s.flags.join("; ")}</td>
              </tr>
            ))}</tbody>
          </table></div>
        </Card>
      )}
      {data?.vendors.map((v) => (
        <Card key={v.vendor} title={v.vendor}>
          <p className={`mb-3 text-xs ${v.status === "stale_cache" ? "text-amber-700" : "text-slate-500"}`}>{v.freshness?.label ?? v.message}</p>
          {!v.product && !v.seller && <Notice tone="warn">{v.message ?? "No reviews or ratings could be retrieved from this vendor."}</Notice>}
          <div className="grid gap-4 lg:grid-cols-2">
            {v.product && <Block title="Product reviews" b={v.product} />}
            {v.seller && <Block title="Seller reviews" b={v.seller} />}
          </div>
        </Card>
      ))}
    </div>
  );
}
