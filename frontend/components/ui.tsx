import type { ReactNode } from "react";
import type { Action } from "@/types/api";

export function Card({ title, children, actions, className = "" }: { title?: string; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {title && <h2 className="card-title">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, kind }: { label: string; value: ReactNode; hint?: ReactNode; kind?: DataKind }) {
  return (
    <div>
      <div className="flex items-center gap-2 text-xs text-slate-500">{label}{kind && <KindTag kind={kind} />}</div>
      <div className="mt-0.5 text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

// Every figure on screen is one of these. The tag makes the distinction visible.
export type DataKind = "observed" | "source" | "estimate" | "forecast" | "unknown" | "vendor";
const KIND: Record<DataKind, [string, string]> = {
  observed: ["Recorded", "bg-good-50 text-good-700"],
  source: ["Source-stated", "bg-brand-50 text-brand-700"],
  estimate: ["Estimate", "bg-gold-100 text-gold-800"],
  forecast: ["Forecast", "bg-slate-100 text-slate-700"],
  unknown: ["Unknown", "bg-slate-100 text-slate-500"],
  vendor: ["Store page", "bg-brand-50 text-brand-700"],
};
export function KindTag({ kind }: { kind: DataKind }) {
  const [label, cls] = KIND[kind];
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${cls}`}>{label}</span>;
}

// Shopper-facing wording for the recommendation engine's actions.
export const ACTION: Record<Action, { label: string; cls: string; tone: "good" | "wait" | "neutral" | "brand" }> = {
  BUY_NOW: { label: "Good time to buy", cls: "bg-good-50 text-good-700", tone: "good" },
  WAIT: { label: "Wait for a drop", cls: "bg-gold-100 text-gold-800", tone: "wait" },
  MONITOR: { label: "Keep watching", cls: "bg-slate-100 text-slate-700", tone: "neutral" },
  BUY_IF_TARGET_REACHED: { label: "Buy at your target", cls: "bg-brand-50 text-brand-700", tone: "brand" },
  COMPARE_VENDORS: { label: "Compare stores first", cls: "bg-brand-50 text-brand-700", tone: "brand" },
  INSUFFICIENT_DATA: { label: "Not enough history", cls: "bg-slate-100 text-slate-600", tone: "neutral" },
};
export function ActionBadge({ action, large }: { action: Action; large?: boolean }) {
  const a = ACTION[action];
  return <span className={`inline-block rounded font-semibold ${a.cls} ${large ? "px-3 py-1.5 text-base" : "px-2 py-0.5 text-xs"}`}>{a.label}</span>;
}

// Plain text, only for states that need attention (no coloured dots).
const HEALTH: Record<string, [string, string]> = {
  healthy: ["Collecting normally", "text-slate-600"], degraded: ["Some checks failing", "text-gold-800"],
  stale: ["Out of date", "text-gold-800"], failing: ["Collection failing", "text-alert-600"],
  never_run: ["Not collected yet", "text-slate-500"], disabled: ["Disabled", "text-slate-500"],
};
export function HealthText({ status }: { status: string }) {
  const [label, cls] = HEALTH[status] ?? [status.replace("_", " "), "text-slate-600"];
  return <span className={`text-xs font-medium ${cls}`}>{label}</span>;
}
export const needsAttention = (status: string) => !["healthy", "disabled"].includes(status);

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "error"; children: ReactNode }) {
  const cls = { info: "border-brand-100 bg-brand-50 text-brand-900", warn: "border-gold-100 bg-gold-50 text-gold-800", error: "border-alert-50 bg-alert-50 text-alert-600" }[tone];
  return <div className={`rounded-md border px-3 py-2 text-sm ${cls}`} role={tone === "error" ? "alert" : undefined}>{children}</div>;
}

export function Limitations({ items }: { items: string[] | undefined }) {
  if (!items?.length) return null;
  return (
    <details className="mt-3 text-xs text-slate-600">
      <summary className="cursor-pointer font-medium text-slate-700">Limitations ({items.length})</summary>
      <ul className="mt-1 list-disc space-y-0.5 pl-5">{items.map((l, i) => <li key={i}>{l}</li>)}</ul>
    </details>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="animate-pulse text-sm text-slate-500">{what}…</div>;
}

export function Disclaimer() {
  return <p className="text-xs text-slate-500">SmartBuy AI explains price data and financing costs; it is not personalised financial advice. Confirm final prices and loan terms with the seller or lender.</p>;
}
