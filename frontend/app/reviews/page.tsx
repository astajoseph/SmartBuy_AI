"use client";
import { useState } from "react";
import ProductPicker from "@/components/ProductPicker";
import { Card, Limitations, Loading, Notice } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { aspectLabel, date, dateTime } from "@/lib/format";
import type { ProductSummary, ReviewBlock, VendorReviews } from "@/types/api";

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
      {!!b.excerpts?.length && (
        <div className="mt-3 space-y-2">{b.excerpts.map((e, i) => <blockquote key={i} className="border-l-2 border-slate-300 pl-3 text-sm text-slate-700">“{e.text}” <span className="text-xs text-slate-500">{e.rating != null ? `${e.rating}★` : ""} {e.date ? date(e.date) : ""}</span></blockquote>)}</div>
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
  async function load(refresh = false) {
    setBusy(true); setErr(null);
    try { setData(await api(`/api/products/${pid}/reviews?refresh=${refresh}`)); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Review intelligence</h1>
        <p className="text-sm text-slate-600">Product reviews and seller reviews are kept separate. Only reviews actually retrieved from vendor pages are analysed; quotes are real excerpts.</p>
      </div>
      <Card><div className="grid items-end gap-3 md:grid-cols-3">
        <ProductPicker products={products} value={pid} onChange={setPid} />
        <div className="flex gap-2"><button className="btn-primary" disabled={!pid || busy} onClick={() => load(false)}>Analyse reviews</button>
          <button className="btn-secondary" disabled={!pid || busy} onClick={() => load(true)}>Refresh</button></div>
      </div></Card>
      {err && <Notice tone="error">{err}</Notice>}
      {busy && <Loading what="Checking cache and vendor pages" />}
      {data?.message && <Notice>{data.message}</Notice>}
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
