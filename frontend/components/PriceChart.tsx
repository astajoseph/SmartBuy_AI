"use client";
import { CartesianGrid, ComposedChart, Legend, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import type { PricePoint, SaleEvent } from "@/types/api";
import { money } from "@/lib/format";

interface Props { selling: PricePoint[]; offer: PricePoint[]; events?: SaleEvent[]; target?: number | null; height?: number }

const GAP_MS = 30 * 86_400_000; // same rule as the backend: no carry-forward beyond 30 days

const fmtDate = (t: number) => new Date(t).toLocaleDateString("en-IN", { month: "short", year: "2-digit", timeZone: "Asia/Kolkata" });

export default function PriceChart({ selling, offer, events = [], target, height = 320 }: Props) {
  const s: { t: number; selling: number | null; kind?: string }[] = [];
  for (const p of selling) {
    const t = new Date(p.ts).getTime();
    const prev = s[s.length - 1];
    if (prev && t - prev.t > GAP_MS) s.push({ t: prev.t + GAP_MS, selling: null }); // break the line: unknown period
    s.push({ t, selling: p.price, kind: p.kind });
  }
  const o = offer.map((p) => ({ t: new Date(p.ts).getTime(), offer: p.price }));
  // offer prices get their own dataset so they never insert gaps into the selling-price line
  const data = s;
  const allT = [...s.map((r) => r.t), ...o.map((r) => r.t)];
  if (!allT.length) return <div className="grid h-40 place-items-center text-sm text-slate-500">No recorded prices yet.</div>;
  const min = Math.min(...allT), max = Math.max(...allT);
  // Scale to the 1st-99th percentile so a few extreme records (e.g. a third-party listing) don't flatten
  // the chart; anything above the scale is counted and disclosed under the chart.
  const prices = [...selling.map((p) => p.price), ...offer.map((p) => p.price)].sort((a, b) => a - b);
  const q = (f: number) => prices[Math.min(prices.length - 1, Math.max(0, Math.round(f * (prices.length - 1))))];
  const capHi = prices.length >= 50 ? Math.max(q(0.99) * 1.15, target ?? 0) : prices[prices.length - 1];
  const capLo = prices.length >= 50 ? Math.min(q(0.01) * 0.9, target ?? Infinity) : prices[0];
  const yLo = Math.max(0, Math.floor((Math.min(capLo, target ?? capLo) * 0.95) / 50) * 50);
  const yHi = Math.ceil((Math.max(capHi, target ?? 0) * 1.03) / 50) * 50;
  const above = prices.filter((p) => p > yHi), below = prices.filter((p) => p < yLo);
  const bands = events.filter((e) => e.start_date && e.end_date)
    .map((e) => ({ ...e, x1: new Date(e.start_date + "T00:00:00+05:30").getTime(), x2: new Date(e.end_date + "T23:59:59+05:30").getTime() }))
    .filter((e) => e.x2 >= min && e.x1 <= max);
  const names = Array.from(new Set(bands.map((b) => b.event_name)));
  return (
    <div>
    <div style={{ height }} role="img" aria-label="Recorded price history chart">
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 10, right: 16, bottom: 0, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="t" type="number" domain={[min, max]} scale="time" tickFormatter={fmtDate} fontSize={12} stroke="#64748b" />
          <YAxis tickFormatter={(v) => money(v)} width={80} fontSize={12} stroke="#64748b" domain={[yLo, yHi]} allowDataOverflow />
          <Tooltip labelFormatter={(t) => new Date(Number(t)).toLocaleDateString("en-IN", { dateStyle: "medium", timeZone: "Asia/Kolkata" })}
            formatter={(v: number, name: string) => [money(v), name === "selling" ? "Selling price (recorded)" : "Offer price (recorded)"]} />
          <Legend formatter={(v) => (v === "selling" ? "Selling price — recorded points, step between records" : "Offer price (after offers)")} />
          {bands.map((b, i) => (
            <ReferenceArea key={i} x1={b.x1} x2={b.x2} fill="#f59e0b" fillOpacity={0.15} ifOverflow="hidden" />
          ))}
          {target ? <ReferenceLine y={target} stroke="#4f46e5" strokeDasharray="6 4" label={{ value: `Target ${money(target)}`, fontSize: 11, fill: "#4f46e5", position: "insideBottomRight" }} /> : null}
          <Line dataKey="selling" type="stepAfter" stroke="#2563eb" strokeWidth={2} dot={{ r: 2 }} connectNulls={false} isAnimationActive={false} />
          <Scatter data={o} dataKey="offer" fill="#e11d48" isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
    {(names.length > 0 || above.length > 0 || below.length > 0) && (
      <p className="mt-1 text-xs text-slate-500">
        {names.length > 0 && <>Shaded: {names.join(", ")}. </>}
        {above.length > 0 && <>{above.length} recorded price(s) above {money(yHi)} (max {money(Math.max(...above))}) are outside the scale. </>}
        {below.length > 0 && <>{below.length} recorded price(s) below {money(yLo)} are outside the scale.</>}
      </p>
    )}
    </div>
  );
}
