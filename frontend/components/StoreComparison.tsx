"use client";
/**
 * "Where to buy" on one price scale (chart option D, store part): each store's latest checked price as a dot,
 * the usual price as a faint tick on every row. Filled dot = cheapest store with a recent (≤ 48 h) in-stock price.
 * Clicking a row draws that store's own price line on the chart above.
 */
import type { VendorHistoryRow } from "@/types/api";
import { money } from "@/lib/format";

const AMBER = "#b86e00", TEAL = "#008e9b", STALE_H = 48;

export default function StoreComparison({ vendors, usual, picked, onPick }: {
  vendors: VendorHistoryRow[]; usual?: number | null; picked?: number | null; onPick?: (id: number) => void;
}) {
  const rows = vendors.filter((v) => v.is_active).map((v) => ({
    v, price: v.stats.in_stock ? (v.stats.effective_price ?? v.stats.price) : null, stale: v.stats.age_hours > STALE_H,
  })).sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));
  const priced = rows.filter((r) => r.price != null);
  if (!rows.length) return null;
  const vals = [...priced.map((r) => r.price as number), ...(usual ? [usual] : [])];
  const lo = Math.min(...vals) * 0.96, hi = Math.max(...vals) * 1.03;
  const pos = (v: number) => `${((v - lo) / (hi - lo || 1)) * 100}%`;
  const best = priced.find((r) => !r.stale);
  const age = (h: number) => (h < 1 ? "just now" : h < 48 ? `${Math.round(h)} h ago` : `${Math.round(h / 24)} days ago`);

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <h3 className="font-bold">Price at each store</h3>
        <span className="text-xs text-slate-500">Click a store to draw its price line above</span>
      </div>
      <ul>
        {rows.map(({ v, price, stale }) => {
          const isBest = best?.v.vendor_source_id === v.vendor_source_id, on = picked === v.vendor_source_id;
          return (
            <li key={v.vendor_source_id}>
              <button type="button" onClick={() => onPick?.(v.vendor_source_id)} aria-pressed={on}
                className={`grid w-full grid-cols-[minmax(90px,150px)_1fr_auto] items-center gap-3 border-b border-slate-100 px-2 py-2 text-left text-[13px] hover:bg-slate-50 ${on ? "bg-slate-50" : ""}`}>
                <span className={`truncate ${on ? "font-semibold text-brand-600" : ""}`}>{v.vendor}</span>
                <span className="relative h-4">
                  <span className="absolute inset-x-0 top-1/2 h-px bg-slate-200" />
                  {usual && <span className="absolute top-0 h-4 w-px" style={{ left: pos(usual), background: TEAL, opacity: 0.4 }} />}
                  {price != null && (
                    <span className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2"
                      style={{ left: pos(price), borderColor: stale ? "#9aa6a8" : AMBER, background: isBest ? AMBER : "#fff" }} />
                  )}
                </span>
                <span className="min-w-[110px] text-right tabular-nums">
                  {price != null
                    ? <><b className={stale ? "font-medium text-slate-400" : isBest ? "font-bold" : "font-medium"}>{money(price)}</b>{isBest && <span className="ml-1 text-xs font-semibold text-good-600">cheapest</span>}
                        {stale && <span className="block text-[11px] text-slate-400">{age(v.stats.age_hours)}</span>}</>
                    : <span className="text-slate-400">{v.stats.in_stock ? "No price" : "Out of stock"}</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="mt-1.5 text-[11px] text-slate-500">
        Filled dot = cheapest store checked in the last 2 days. Faint tick = usual price{usual ? ` (${money(usual)})` : ""}. Grey = price more than 2 days old.
      </p>
    </div>
  );
}
