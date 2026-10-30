import type { BuyTiming, Recommendation } from "@/types/api";
import { dateTime, money } from "@/lib/format";
import { ActionBadge, Disclaimer, KindTag, Limitations, Stat } from "./ui";

export default function RecommendationView({ rec }: { rec: Recommendation }) {
  const fc = rec.evidence.forecast as { status?: string; predicted_price?: number; lower_price_bound?: number; upper_price_bound?: number; message?: string } | undefined;
  const timing = rec.evidence.buy_timing as BuyTiming | undefined;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <ActionBadge action={rec.action} large />
        <span className="text-sm text-slate-600">Confidence: <strong>{rec.confidence.toLowerCase()}</strong></span>
        <span className="text-xs text-slate-500">Generated {dateTime(rec.generated_at)} · {rec.recommendation_version}</span>
      </div>
      <p className="text-base font-medium text-slate-800">{rec.suggested_action}</p>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Current price" kind="observed" value={money(rec.current_price)} hint={rec.current_price_as_of ? `as of ${dateTime(rec.current_price_as_of)}` : undefined} />
        <Stat label="Target / budget" value={money(rec.target_price)} />
        <Stat label={`Chance of target in ${rec.forecast_horizon_days ?? "?"} days`} kind={rec.probability_band ? "estimate" : "unknown"} value={rec.probability_band ?? "Not reliable"} />
        <Stat label={`${rec.forecast_horizon_days ?? ""}-day forecast`} kind={fc?.status === "ok" ? "forecast" : "unknown"}
          value={fc?.status === "ok" ? money(fc.predicted_price) : "Insufficient data"}
          hint={fc?.status === "ok" ? `range ${money(fc.lower_price_bound)} – ${money(fc.upper_price_bound)}` : fc?.message} />
      </div>
      {timing && timing.score !== null && (
        <div className="rounded-lg border border-slate-200 p-3 text-sm">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-2xl font-semibold">{timing.score}<span className="text-sm font-normal text-slate-500">/100</span></span>
            <span className="font-medium">Buy-timing: {timing.label}</span>
            <KindTag kind="estimate" />
          </div>
          <ul className="mt-2 space-y-0.5 text-xs text-slate-600">{timing.components.map((c) => <li key={c.name}>• {c.note}</li>)}</ul>
          {timing.caveats?.map((c, i) => <div key={i} className="mt-1 text-xs text-amber-700">{c}</div>)}
          <div className="mt-1 text-[11px] text-slate-500">{timing.explanation}</div>
        </div>
      )}
      {rec.estimated_savings !== null && (
        <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
          <div className="flex items-center gap-2 font-medium">Estimated saving from waiting: {money(rec.estimated_savings)} <KindTag kind="estimate" /></div>
          {rec.estimated_savings_range && <div>Range {money(rec.estimated_savings_range[0])} – {money(rec.estimated_savings_range[1])}</div>}
          {rec.savings_explanation && <div className="mt-1 text-xs">{rec.savings_explanation}</div>}
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-1 text-sm font-semibold">Why</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm">{rec.reasoning.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </div>
        <div>
          <h3 className="mb-1 text-sm font-semibold">Risks</h3>
          {rec.risks.length ? <ul className="list-disc space-y-1 pl-5 text-sm">{rec.risks.map((r, i) => <li key={i}>{r}</li>)}</ul>
            : <p className="text-sm text-slate-500">No specific risks flagged.</p>}
        </div>
      </div>
      <div className="text-xs text-slate-500">
        Vendor websites {rec.vendor_data_accessed ? "were contacted for this answer" : "were not contacted for this answer"}.
      </div>
      <Limitations items={rec.limitations} />
      <Disclaimer />
    </div>
  );
}
