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
  observed: ["Observed", "bg-emerald-50 text-emerald-700 ring-emerald-200"],
  source: ["Source-stated", "bg-sky-50 text-sky-700 ring-sky-200"],
  estimate: ["Estimate", "bg-amber-50 text-amber-800 ring-amber-200"],
  forecast: ["Forecast", "bg-violet-50 text-violet-700 ring-violet-200"],
  unknown: ["Unknown", "bg-slate-100 text-slate-600 ring-slate-200"],
  vendor: ["Vendor page", "bg-teal-50 text-teal-700 ring-teal-200"],
};
export function KindTag({ kind }: { kind: DataKind }) {
  const [label, cls] = KIND[kind];
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ${cls}`}>{label}</span>;
}

const ACTION: Record<Action, [string, string]> = {
  BUY_NOW: ["Buy now", "bg-emerald-600 text-white"],
  WAIT: ["Wait", "bg-amber-500 text-white"],
  MONITOR: ["Monitor", "bg-sky-600 text-white"],
  BUY_IF_TARGET_REACHED: ["Buy if target reached", "bg-indigo-600 text-white"],
  COMPARE_VENDORS: ["Compare vendors", "bg-teal-600 text-white"],
  INSUFFICIENT_DATA: ["Insufficient data", "bg-slate-500 text-white"],
};
export function ActionBadge({ action, large }: { action: Action; large?: boolean }) {
  const [label, cls] = ACTION[action];
  return <span className={`inline-block rounded-full font-semibold ${cls} ${large ? "px-4 py-1.5 text-base" : "px-2.5 py-0.5 text-xs"}`}>{label}</span>;
}

export function StatusDot({ status }: { status: string }) {
  const color = { healthy: "bg-emerald-500", degraded: "bg-amber-500", stale: "bg-amber-500", failing: "bg-rose-500", never_run: "bg-slate-400", disabled: "bg-slate-300" }[status] ?? "bg-slate-400";
  return <span className="inline-flex items-center gap-1.5 text-xs text-slate-600"><span className={`h-2 w-2 rounded-full ${color}`} />{status.replace("_", " ")}</span>;
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "error"; children: ReactNode }) {
  const cls = { info: "border-sky-200 bg-sky-50 text-sky-900", warn: "border-amber-200 bg-amber-50 text-amber-900", error: "border-rose-200 bg-rose-50 text-rose-900" }[tone];
  return <div className={`rounded-lg border px-3 py-2 text-sm ${cls}`} role={tone === "error" ? "alert" : undefined}>{children}</div>;
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
