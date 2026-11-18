"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useApi } from "@/hooks/useApi";
import { usePrefs } from "@/hooks/usePrefs";
import { api } from "@/lib/api";
import { catLabel, date, money, pct } from "@/lib/format";
import type { Action, ProductSummary, SaleEvent } from "@/types/api";
import { ACTION, HealthText, Loading, Notice, needsAttention } from "@/components/ui";
import ProductImage from "@/components/ProductImage";

type LatestRec = { recommended_action: Action; generated_at: string } | null;
const ORDER: Action[] = ["BUY_NOW", "BUY_IF_TARGET_REACHED", "COMPARE_VENDORS", "MONITOR", "WAIT", "INSUFFICIENT_DATA"];
const SORTS = { best: "Best time to buy", low: "Price: low to high", high: "Price: high to low", drop: "Biggest 7-day drop" } as const;
type SortKey = keyof typeof SORTS;

/** Latest stored recommendation per product (one request each; the list is small). */
function useLatestRecs(ids: string[]) {
  const [recs, setRecs] = useState<Record<string, LatestRec>>({});
  const key = ids.join(",");
  useEffect(() => {
    if (!key) return;
    let live = true;
    void Promise.all(ids.map(async (id) => {
      try { return [id, (await api<NonNullable<LatestRec>[]>(`/api/products/${id}/recommendations`))[0] ?? null] as const; }
      catch { return [id, null] as const; }
    })).then((pairs) => { if (live) setRecs(Object.fromEntries(pairs)); });
    return () => { live = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return recs;
}

function Check({ label, count, on, onChange }: { label: string; count: number; on: boolean; onChange: () => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 py-1 text-[13px] text-slate-700">
      <input type="checkbox" checked={on} onChange={onChange} className="h-4 w-4 rounded border-slate-400 accent-brand-600" />
      <span className="flex-1">{label}</span><span className="text-xs text-slate-400">{count}</span>
    </label>
  );
}

function Storefront() {
  const params = useSearchParams();
  const q = (params.get("q") ?? "").trim().toLowerCase();
  const { data: products, error, loading } = useApi<ProductSummary[]>("/api/products");
  const { data: events } = useApi<{ upcoming: SaleEvent[] }>("/api/events");
  const { prefs } = usePrefs();
  const recs = useLatestRecs(products?.map((p) => p.id) ?? []);
  const [advice, setAdvice] = useState<Set<string>>(new Set());
  const [cats, setCats] = useState<Set<string>>(new Set());
  const [brands, setBrands] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<SortKey>("best");

  const actionOf = (p: ProductSummary): Action | "NONE" => recs[p.id]?.recommended_action ?? "NONE";
  const toggle = (set: Set<string>, v: string, put: (s: Set<string>) => void) => {
    const n = new Set(set); if (n.has(v)) n.delete(v); else n.add(v); put(n);
  };
  const searched = useMemo(() => (products ?? []).filter((p) =>
    !q || `${p.name} ${p.brand ?? ""} ${p.category ?? ""}`.toLowerCase().includes(q)), [products, q]);
  const count = (fn: (p: ProductSummary) => string | null | undefined) => {
    const m = new Map<string, number>();
    searched.forEach((p) => { const k = fn(p); if (k) m.set(k, (m.get(k) ?? 0) + 1); });
    return [...m.entries()].sort();
  };
  const shown = searched
    .filter((p) => !advice.size || advice.has(actionOf(p)))
    .filter((p) => !cats.size || cats.has(p.category ?? ""))
    .filter((p) => !brands.size || brands.has(p.brand ?? ""))
    .sort((a, b) => {
      const pa = a.current?.price ?? Infinity, pb = b.current?.price ?? Infinity;
      if (sort === "low") return pa - pb;
      if (sort === "high") return (b.current?.price ?? -1) - (a.current?.price ?? -1);
      if (sort === "drop") return (a.changes?.["7d"]?.pct ?? 0) - (b.changes?.["7d"]?.pct ?? 0);
      const ia = ORDER.indexOf(actionOf(a) as Action), ib = ORDER.indexOf(actionOf(b) as Action);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
  const adviceCounts = ORDER.map((a) => [a, searched.filter((p) => actionOf(p) === a).length] as const).filter(([, n]) => n > 0);
  const anyFilter = advice.size + cats.size + brands.size > 0;

  return (
    <div className="space-y-8">
      <div>
        <div className="crumb mb-2"><Link href="/">Home</Link> › {q ? `Search: “${params.get("q")}”` : "All tracked products"}</div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-bold tracking-tight">
            {q ? <>Results for “{params.get("q")}”</> : "All tracked products"}
            {products && <span className="ml-2 text-base font-medium text-slate-500">({shown.length} {shown.length === 1 ? "product" : "products"})</span>}
          </h1>
          <label className="flex items-center gap-2 text-sm text-slate-600">Sort by
            <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className="input w-auto py-1.5">
              {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
        </div>
      </div>

      {error && <Notice tone="error">Could not reach the price service: {error}</Notice>}

      <div className="grid gap-6 lg:grid-cols-[230px_1fr]">
        <aside className="self-start rounded-lg border border-slate-200 bg-white px-4 py-1" aria-label="Filters">
          <div className="border-b border-slate-100 py-3">
            <h2 className="mb-1 text-[13px] font-bold">Buying advice</h2>
            {adviceCounts.length ? adviceCounts.map(([a, n]) => (
              <Check key={a} label={ACTION[a].label} count={n} on={advice.has(a)} onChange={() => toggle(advice, a, setAdvice)} />
            )) : <p className="py-1 text-xs text-slate-500">Ask “Should I buy?” on a product to get advice.</p>}
          </div>
          {count((p) => p.category).length > 0 && (
            <div className="border-b border-slate-100 py-3">
              <h2 className="mb-1 text-[13px] font-bold">Category</h2>
              {count((p) => p.category).map(([c, n]) => <Check key={c} label={catLabel(c)} count={n} on={cats.has(c)} onChange={() => toggle(cats, c, setCats)} />)}
            </div>
          )}
          {count((p) => p.brand).length > 0 && (
            <div className="py-3">
              <h2 className="mb-1 text-[13px] font-bold">Brand</h2>
              {count((p) => p.brand).map(([b, n]) => <Check key={b} label={b} count={n} on={brands.has(b)} onChange={() => toggle(brands, b, setBrands)} />)}
            </div>
          )}
          {anyFilter && (
            <button className="mb-3 text-[13px] font-semibold text-brand-600 hover:underline"
              onClick={() => { setAdvice(new Set()); setCats(new Set()); setBrands(new Set()); }}>Clear filters</button>
          )}
        </aside>

        <section aria-label="Products">
          {loading && !products && <Loading what="Loading products" />}
          {products && products.length === 0 && <Notice>No products are tracked yet. Add them in config/products.yaml or on the Admin page.</Notice>}
          {products && products.length > 0 && shown.length === 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-8 text-center text-sm text-slate-600">
              No products match{q && <> “{params.get("q")}”</>}{anyFilter && " with these filters"}.
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {shown.map((p) => {
              const a = actionOf(p);
              const ch = p.changes?.["7d"];
              const target = prefs.targets[p.id];
              return (
                <article key={p.id} className="group flex flex-col rounded-lg border border-slate-200 bg-white p-4 transition-colors hover:border-slate-300">
                  <Link href={`/products/${p.id}`} className="mb-3 block" tabIndex={-1} aria-hidden>
                    <ProductImage src={p.image_url} name={p.name} className="h-44" />
                  </Link>
                  {p.brand && <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{p.brand}</div>}
                  <Link href={`/products/${p.id}`} className="mt-0.5 line-clamp-2 min-h-[2.6em] font-semibold leading-snug text-slate-900 group-hover:text-brand-600">{p.name}</Link>
                  <div className="mt-2 min-h-[24px]">{a !== "NONE" && <span className={`tag ${ACTION[a].cls}`}>{ACTION[a].label}</span>}</div>
                  <div className="mt-2 text-xl font-bold tabular-nums tracking-tight">{money(p.current?.price)}</div>
                  {p.current?.store && <div className="text-xs text-slate-600">at {p.current.store}</div>}
                  <div className="text-xs text-slate-500">
                    {ch && !ch.unknown && ch.pct !== undefined
                      ? <span className={ch.pct < 0 ? "font-medium text-good-600" : ch.pct > 0 ? "font-medium text-alert-600" : ""}>{ch.pct === 0 ? "No change" : pct(ch.pct)} this week</span>
                      : "Weekly change unknown"}
                    {p.historical_low && <> · lowest {money(p.historical_low.price)}</>}
                  </div>
                  {target && p.current && (
                    <div className="mt-1 text-xs font-medium text-brand-600">{p.current.price <= target ? `At or below your target ${money(target)}` : `${money(p.current.price - target)} above your target`}</div>
                  )}
                  {needsAttention(p.tracking_status) && <div className="mt-1"><HealthText status={p.tracking_status} /></div>}
                  <div className="mt-auto space-y-1.5 pt-4">
                    <Link href={`/products/${p.id}`} className="btn-secondary w-full">View prices</Link>
                    <Link href={`/advisor?product=${p.id}`} className="block py-1 text-center text-[13px] font-semibold text-brand-600 hover:underline">Should I buy?</Link>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      </div>

      <section aria-labelledby="sales-h">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 id="sales-h" className="text-xl font-bold tracking-tight">Upcoming sales</h2>
          <Link href="/sales" className="text-sm font-semibold text-brand-600 hover:underline">Full sale calendar</Link>
        </div>
        {!events ? <Loading /> : events.upcoming.length === 0 ? <p className="text-sm text-slate-500">No sale events registered.</p> : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {events.upcoming.slice(0, 8).map((e, i) => (
              <div key={i} className="rounded-lg border border-slate-200 bg-white p-4">
                <div className="text-xs font-semibold uppercase tracking-wide text-gold-800">{e.retailer === "any" ? "Most stores" : e.retailer}</div>
                <div className="mt-0.5 font-semibold">{e.event_name}</div>
                <div className="mt-1 text-xs text-slate-500">
                  {e.start_date ? `${date(e.start_date)}${e.end_date ? " – " + date(e.end_date) : ""}` : "Dates not announced yet"}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading what="Loading products" />}><Storefront /></Suspense>;
}
