"use client";
/**
 * Price history chart ("clean line + usual-price band").
 * - One stepped line for the recorded price history, no per-record dots; it breaks where nothing was recorded for
 *   over 30 days (same carry-forward rule as the backend).
 * - Shaded band = usual price range of the last 12 months (10th–90th percentile of known days), dashed line = median.
 * - Optional second line: one store's own daily checks (SmartBuy's data).
 * - Offer prices (after bank / coupon offers) as small hollow markers in the same colour (shape, not a third hue).
 * - A few extreme records (e.g. multi-pack listings) are kept off the scale and disclosed below the chart.
 * Colours validated with the dataviz palette checker: teal #008e9b + amber #b86e00 on #ffffff (CVD + contrast pass).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { PricePoint, SaleEvent } from "@/types/api";
import { money } from "@/lib/format";

export interface StoreLine { label: string; points: { t: number; price: number }[] }
export interface UsualRange { p10: number; p90: number; median: number }
interface Props {
  selling: PricePoint[]; offer: PricePoint[]; events?: SaleEvent[]; usual?: UsualRange | null;
  store?: StoreLine | null; target?: number | null; height?: number;
}

const TEAL = "#008e9b", AMBER = "#b86e00", GRID = "#eef1f1", AXIS = "#e1e6e6", MUTED = "#6b7679", SALE = "#ece7db";
const DAY = 86_400_000, GAP = 30 * DAY;
const M_WIDE = { t: 12, r: 104, b: 28, l: 62 }, M_NARROW = { t: 12, r: 66, b: 28, l: 56 };
const dLong = (t: number) => new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    setW(Math.max(280, Math.round(ref.current.clientWidth)));
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/** last point at or before t (points sorted by time) */
function at<T extends { t: number }>(pts: T[], t: number): T | null {
  let lo = 0, hi = pts.length - 1, best: T | null = null;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (pts[mid].t <= t) { best = pts[mid]; lo = mid + 1; } else hi = mid - 1; }
  return best;
}

function stepPath(pts: { t: number; v: number }[], X: (t: number) => number, Y: (v: number) => number) {
  let d = "", prevT = -Infinity;
  for (const p of pts) {
    const x = X(p.t).toFixed(1), y = Y(p.v).toFixed(1);
    d += !d || p.t - prevT > GAP ? `M${x},${y}` : `H${x}V${y}`;
    prevT = p.t;
  }
  return d;
}

export default function PriceHistoryChart({ selling, offer, events = [], usual, store, target, height = 330 }: Props) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hoverT, setHoverT] = useState<number | null>(null);

  const g = useMemo(() => {
    const sell = selling.map((p) => ({ t: new Date(p.ts).getTime(), v: p.price })).sort((a, b) => a.t - b.t);
    const off = offer.map((p) => ({ t: new Date(p.ts).getTime(), v: p.price }));
    const st = (store?.points ?? []).map((p) => ({ t: p.t, v: p.price })).sort((a, b) => a.t - b.t);
    if (!sell.length && !st.length) return null;
    const now = Date.now();
    const x0 = Math.min(...[...sell, ...st].map((p) => p.t)), x1 = Math.max(now, ...[...sell, ...st].map((p) => p.t));
    // scale on the 1st–99th percentile so a handful of extreme listings don't flatten the chart
    const all = [...sell, ...off, ...st].map((p) => p.v).sort((a, b) => a - b);
    const q = (f: number) => all[Math.min(all.length - 1, Math.max(0, Math.round(f * (all.length - 1))))];
    const big = all.length >= 50;
    const extra = [usual?.p10, usual?.p90, target].filter((v): v is number => v != null);
    const lo = Math.min(big ? q(0.01) : all[0], ...extra), hi = Math.max(big ? q(0.99) : all[all.length - 1], ...extra);
    const pad = Math.max(20, (hi - lo) * 0.08);
    const step = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000].find((s) => (hi - lo + 2 * pad) / s <= 5) ?? 100000;
    const y0 = Math.max(0, Math.floor((lo - pad) / step) * step), y1 = Math.ceil((hi + pad) / step) * step;
    const outside = all.filter((v) => v > y1 || v < y0);
    return { sell, off, st, x0, x1, y0, y1, step, now, outside };
  }, [selling, offer, store, usual, target]);

  if (!g) return <div className="grid h-40 place-items-center text-sm text-slate-500">No recorded prices yet.</div>;

  const M = width < 560 ? M_NARROW : M_WIDE;
  const iw = width - M.l - M.r, ih = height - M.t - M.b;
  const X = (t: number) => M.l + ((t - g.x0) / (g.x1 - g.x0 || 1)) * iw;
  const Y = (v: number) => M.t + ih - ((v - g.y0) / (g.y1 - g.y0)) * ih;
  const yTicks: number[] = [];
  for (let v = g.y0; v <= g.y1; v += g.step) yTicks.push(v);
  const spanDays = (g.x1 - g.x0) / DAY;
  const spanMonths = Math.max(1, spanDays / 30.4);
  const every = [1, 2, 3, 6, 12, 24, 36].find((n) => iw / (spanMonths / n) >= 68) ?? 48;
  const xTicks: number[] = [];
  const d = new Date(g.x0); d.setDate(1); d.setMonth(d.getMonth() + 1);
  if (every >= 3) while (d.getMonth() % Math.min(every, 12)) d.setMonth(d.getMonth() + 1);
  for (; d.getTime() <= g.x1; d.setMonth(d.getMonth() + every)) xTicks.push(d.getTime());
  const xLabel = (t: number) => (every >= 12 ? String(new Date(t).getFullYear())
    : new Date(t).toLocaleDateString("en-IN", { month: "short", year: "2-digit", timeZone: "Asia/Kolkata" }));
  const bandX0 = Math.max(g.x0, g.now - 365 * DAY);
  const inScale = (v: number) => v >= g.y0 && v <= g.y1;
  const sellIn = g.sell.filter((p) => inScale(p.v));
  const last = g.sell[g.sell.length - 1];
  const stLast = g.st[g.st.length - 1];
  const bands = events.filter((e) => e.start_date && e.end_date).map((e) => ({
    name: e.event_name, a: new Date(e.start_date + "T00:00:00+05:30").getTime(), b: new Date(e.end_date + "T23:59:59+05:30").getTime(),
  })).filter((e) => e.b >= g.x0 && e.a <= g.x1);
  const saleNames = Array.from(new Set(bands.map((b) => b.name)));

  // end labels must not collide
  const endLabels = [
    ...(last && inScale(last.v) ? [{ y: Y(last.v), text: money(last.v), color: "#1b2124", bold: true }] : []),
    ...(stLast && inScale(stLast.v) && !(last && Math.round(last.v) === Math.round(stLast.v)) ? [{ y: Y(stLast.v), text: money(stLast.v), color: "#1b2124", bold: false }] : []),
    ...(usual && width >= 560 ? [{ y: Y(usual.median), text: `Usual ${money(usual.median)}`, color: MUTED, bold: false }] : []),
  ].sort((a, b) => a.y - b.y);
  for (let i = 1; i < endLabels.length; i++) if (endLabels[i].y - endLabels[i - 1].y < 15) endLabels[i].y = endLabels[i - 1].y + 15;

  // hover
  const h = hoverT != null ? {
    s: at(g.sell, hoverT), st: g.st.length ? at(g.st, hoverT) : null,
    off: g.off.find((o) => Math.abs(o.t - hoverT) < DAY * Math.max(1, spanDays / iw)),
    sale: bands.find((b) => hoverT >= b.a && hoverT <= b.b),
  } : null;
  const hx = hoverT != null ? X(hoverT) : 0;

  return (
    <div ref={box} className="relative w-full min-w-0">
      {width > 0 && <svg width={width} height={height} role="img" className="block select-none"
        aria-label={`Price history chart${store ? ` with ${store.label}` : ""}. Use the table tabs for exact values.`}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect(), x = e.clientX - r.left;
          setHoverT(x < M.l || x > width - M.r ? null : g.x0 + ((x - M.l) / iw) * (g.x1 - g.x0));
        }}
        onMouseLeave={() => setHoverT(null)}>
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={M.l} x2={width - M.r} y1={Y(v)} y2={Y(v)} stroke={GRID} />
            <text x={M.l - 10} y={Y(v) + 4} textAnchor="end" fontSize={11.5} fill={MUTED}>{money(v)}</text>
          </g>
        ))}
        {bands.map((b, i) => (
          <rect key={i} x={X(Math.max(b.a, g.x0))} y={M.t} width={Math.max(3, X(Math.min(b.b, g.x1)) - X(Math.max(b.a, g.x0)))} height={ih} fill={SALE} opacity={0.8} />
        ))}
        {usual && (
          <>
            <rect x={X(bandX0)} y={Y(usual.p90)} width={Math.max(0, X(g.now) - X(bandX0))} height={Math.max(0, Y(usual.p10) - Y(usual.p90))} fill={TEAL} opacity={0.09} rx={2} />
            <line x1={X(bandX0)} x2={X(g.now)} y1={Y(usual.median)} y2={Y(usual.median)} stroke={TEAL} strokeDasharray="4 4" opacity={0.7} />
          </>
        )}
        {target ? <line x1={M.l} x2={width - M.r} y1={Y(target)} y2={Y(target)} stroke="#1b6b43" strokeDasharray="2 3" /> : null}
        <line x1={M.l} x2={width - M.r} y1={M.t + ih} y2={M.t + ih} stroke={AXIS} />
        {xTicks.map((t) => <text key={t} x={X(t)} y={height - 8} textAnchor="middle" fontSize={11.5} fill={MUTED}>{xLabel(t)}</text>)}
        <path d={stepPath(sellIn, X, Y)} fill="none" stroke={TEAL} strokeWidth={2} strokeLinejoin="round" />
        {g.off.filter((o) => inScale(o.v)).map((o, i) => <circle key={i} cx={X(o.t)} cy={Y(o.v)} r={3.5} fill="#fff" stroke={TEAL} strokeWidth={1.5} />)}
        {g.st.length > 0 && <path d={stepPath(g.st.filter((p) => inScale(p.v)), X, Y)} fill="none" stroke={AMBER} strokeWidth={2} strokeLinejoin="round" />}
        {stLast && inScale(stLast.v) && <circle cx={X(stLast.t)} cy={Y(stLast.v)} r={4.5} fill={AMBER} stroke="#fff" strokeWidth={2} />}
        {last && inScale(last.v) && <circle cx={X(last.t)} cy={Y(last.v)} r={5} fill={TEAL} stroke="#fff" strokeWidth={2} />}
        {endLabels.map((l, i) => <text key={i} x={width - M.r + 8} y={l.y + 4} fontSize={l.bold ? 12.5 : 11.5} fontWeight={l.bold ? 700 : 500} fill={l.color}>{l.text}</text>)}
        {h && <line x1={hx} x2={hx} y1={M.t} y2={M.t + ih} stroke="#9aa6a8" />}
        {h?.s && inScale(h.s.v) && <circle cx={hx} cy={Y(h.s.v)} r={4} fill={TEAL} stroke="#fff" strokeWidth={2} />}
        {h?.st && inScale(h.st.v) && <circle cx={hx} cy={Y(h.st.v)} r={4} fill={AMBER} stroke="#fff" strokeWidth={2} />}
      </svg>}

      {h && (h.s || h.st) && (
        <div className="pointer-events-none absolute z-10 min-w-[180px] rounded-md border border-slate-200 bg-white px-3 py-2 text-[12.5px] shadow-[0_4px_14px_rgba(27,33,36,.08)]"
          style={{ left: Math.min(hx + 14, width - 200), top: 10 }}>
          <div className="mb-1 text-[11.5px] text-slate-500">{dLong(hoverT!)}{h.sale && <> · {h.sale.name}</>}</div>
          {h.s && <div className="flex justify-between gap-4"><span className="flex items-center gap-1.5 text-slate-600"><i className="inline-block h-2 w-2 rounded-full" style={{ background: TEAL }} />Price history</span><b className="tabular-nums">{hoverT! - h.s.t > GAP ? "no record" : money(h.s.v)}</b></div>}
          {h.st && store && <div className="flex justify-between gap-4"><span className="flex items-center gap-1.5 text-slate-600"><i className="inline-block h-2 w-2 rounded-full" style={{ background: AMBER }} />{store.label}</span><b className="tabular-nums">{money(h.st.v)}</b></div>}
          {h.off && <div className="flex justify-between gap-4"><span className="text-slate-600">After offers</span><b className="tabular-nums">{money(h.off.v)}</b></div>}
        </div>
      )}

      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[12.5px] text-slate-600">
        <span className="flex items-center gap-1.5"><i className="inline-block h-[3px] w-4" style={{ background: TEAL }} />Price history</span>
        {usual && <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-3.5 rounded-sm" style={{ background: TEAL, opacity: 0.2 }} />Usual range, last 12 months ({money(usual.p10)}–{money(usual.p90)})</span>}
        {g.off.length > 0 && <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-full border-[1.5px] bg-white" style={{ borderColor: TEAL }} />After bank or coupon offers</span>}
        {saleNames.length > 0 && <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-3.5 rounded-sm" style={{ background: SALE }} />Sales: {saleNames.join(", ")}</span>}
        {store && <span className="flex items-center gap-1.5"><i className="inline-block h-[3px] w-4" style={{ background: AMBER }} />{store.label} (SmartBuy&apos;s daily checks)</span>}
      </div>
      {g.outside.length > 0 && <p className="mt-1 text-xs text-slate-500">{g.outside.length} unusual record(s), up to {money(Math.max(...g.outside))}, are left off the scale (likely third-party or multi-pack listings).</p>}
    </div>
  );
}
