"use client";
import { useEffect, useState } from "react";
import { Card, Notice } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { usePrefs, type Prefs } from "@/hooks/usePrefs";
import { dateTime } from "@/lib/format";
import type { ProductSummary } from "@/types/api";

export default function SettingsPage() {
  const { prefs, save } = usePrefs();
  const [draft, setDraft] = useState<Prefs>(prefs);
  const [saved, setSaved] = useState(false);
  const { data: products } = useApi<ProductSummary[]>("/api/products");
  const { data: notes } = useApi<{ id: number; kind: string; category: string; message: string; observed_at: string }[]>("/api/notifications");
  useEffect(() => setDraft(prefs), [prefs]);
  const n = (s: string) => (s.trim() ? Number(s) : undefined);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-slate-600">Stored in this browser only. Signed-in accounts (Supabase Auth) can also save targets on the server.</p>
      </div>
      <Card title="Budget & financing">
        <div className="grid gap-3 md:grid-cols-4">
          <div><label className="label" htmlFor="bud">Maximum budget (₹)</label><input id="bud" className="input" value={draft.maxBudget ?? ""} onChange={(e) => setDraft({ ...draft, maxBudget: n(e.target.value) })} /></div>
          <div><label className="label" htmlFor="emi">Max monthly EMI (₹)</label><input id="emi" className="input" value={draft.maxMonthlyEmi ?? ""} onChange={(e) => setDraft({ ...draft, maxMonthlyEmi: n(e.target.value) })} /></div>
          <div><label className="label" htmlFor="ten">Preferred tenure (months)</label><input id="ten" className="input" value={draft.preferredTenure ?? ""} onChange={(e) => setDraft({ ...draft, preferredTenure: n(e.target.value) })} /></div>
          <div><label className="label" htmlFor="obj">Priority</label>
            <select id="obj" className="input" value={draft.objective} onChange={(e) => setDraft({ ...draft, objective: e.target.value as Prefs["objective"] })}>
              <option value="lowest_total_cost">Lowest total cost</option><option value="lowest_monthly_payment">Lowest monthly payment</option>
              <option value="shortest_tenure">Shortest repayment</option><option value="lowest_interest">Lowest interest</option><option value="balanced">Balanced</option>
            </select></div>
          <div className="md:col-span-4"><label className="label" htmlFor="banks">Banks you hold cards with (names only, comma-separated)</label>
            <input id="banks" className="input" placeholder="e.g. HDFC, ICICI" value={draft.banks.join(", ")} onChange={(e) => setDraft({ ...draft, banks: e.target.value.split(",").map((b) => b.trim()).filter(Boolean) })} />
            <p className="mt-1 text-xs text-slate-500">Used only to separate offers you qualify for from conditional ones. Never enter card numbers.</p></div>
        </div>
      </Card>
      <Card title="Target prices">
        <div className="grid gap-3 md:grid-cols-2">
          {products?.map((p) => (
            <div key={p.id}><label className="label" htmlFor={`t-${p.id}`}>{p.name}</label>
              <input id={`t-${p.id}`} className="input" inputMode="numeric" value={draft.targets[p.id] ?? ""}
                onChange={(e) => { const v = n(e.target.value); const t = { ...draft.targets }; if (v) t[p.id] = v; else delete t[p.id]; setDraft({ ...draft, targets: t }); }} /></div>
          ))}
        </div>
      </Card>
      <Card title="Notification preferences">
        {(["targetReached", "priceDrop", "sales"] as const).map((k) => (
          <label key={k} className="mr-6 inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.alerts[k]} onChange={(e) => setDraft({ ...draft, alerts: { ...draft.alerts, [k]: e.target.checked } })} />
            {{ targetReached: "Target price reached", priceDrop: "Significant price drop", sales: "Upcoming sale events" }[k]}</label>
        ))}
        <p className="mt-2 text-xs text-slate-500">Alerts are shown in the app. E-mail/SMS delivery is not configured.</p>
      </Card>
      <div className="flex items-center gap-3"><button className="btn-primary" onClick={() => { save(draft); setSaved(true); setTimeout(() => setSaved(false), 2000); }}>Save settings</button>{saved && <span className="text-sm text-emerald-700">Saved</span>}</div>
      <Card title="Recent notifications">
        {!notes?.length ? <p className="text-sm text-slate-500">No notifications yet.</p> : (
          <ul className="space-y-2 text-sm">{notes.filter((x) => (x.kind === "target_reached" ? draft.alerts.targetReached : x.kind === "price_drop" ? draft.alerts.priceDrop : x.kind === "sale_upcoming" ? draft.alerts.sales : true))
            .map((x) => <li key={x.id} className="rounded-lg bg-slate-50 p-2"><span className="mr-2 rounded bg-white px-1.5 py-0.5 text-[10px] uppercase ring-1 ring-slate-200">{x.category}</span>{x.message}<div className="text-xs text-slate-500">observed {dateTime(x.observed_at)}</div></li>)}</ul>
        )}
      </Card>
      <Notice>Collection settings (URLs, intervals) are managed by administrators on the Admin page.</Notice>
    </div>
  );
}
