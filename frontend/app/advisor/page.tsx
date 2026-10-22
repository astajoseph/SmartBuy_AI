"use client";
import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { RequireAuth, useAuth } from "@/components/AuthProvider";
import ProductPicker from "@/components/ProductPicker";
import RecommendationView from "@/components/RecommendationView";
import { Card, Loading, Notice } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { useConversation } from "@/hooks/useConversation";
import { usePrefs } from "@/hooks/usePrefs";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import type { ProductSummary, Recommendation } from "@/types/api";

function Advisor() {
  const params = useSearchParams();
  const router = useRouter();
  const { profile } = useAuth();
  const convo = useConversation(profile?.user_id);
  const { data: products } = useApi<ProductSummary[]>("/api/products");
  const { prefs } = usePrefs();
  const [productId, setProductId] = useState(params.get("product") ?? "");
  const [form, setForm] = useState({ target: "", budget: "", deadline: "", vendor: "", maxEmi: "", tenure: "", objective: "lowest_total_cost", withVendors: false, withReviews: false });
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const draftKey = `smartbuy.chat.draft.${profile?.user_id}`;

  useEffect(() => {
    if (!productId && products?.length === 1) setProductId(products[0].id);
  }, [products, productId]);
  useEffect(() => {
    setForm((f) => ({ ...f, target: f.target || (prefs.targets[productId]?.toString() ?? ""), budget: f.budget || (prefs.maxBudget?.toString() ?? ""),
      maxEmi: f.maxEmi || (prefs.maxMonthlyEmi?.toString() ?? ""), objective: prefs.objective }));
  }, [prefs, productId]);
  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [convo.messages]);

  // Reopen the conversation from the URL, or the one the user was last in (continue where it stopped).
  const { open, lastSessionId, sessionId } = convo;
  const wanted = params.get("session");
  useEffect(() => { if (wanted !== sessionId) void open(wanted ?? lastSessionId()); }, [wanted]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (sessionId && sessionId !== params.get("session")) router.replace(`/advisor?session=${sessionId}`, { scroll: false });
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps
  // The latest recommendation in the conversation is shown next to it.
  useEffect(() => {
    const last = [...convo.messages].reverse().find((m) => m.meta?.recommendation);
    if (last?.meta.recommendation) setRec(last.meta.recommendation);
  }, [convo.messages]);
  // An unsent draft survives reloads and dropped connections.
  useEffect(() => { try { setMsg(localStorage.getItem(draftKey) ?? ""); } catch { /* ignore */ } }, [draftKey]);
  const setDraft = (v: string) => { setMsg(v); try { if (v) localStorage.setItem(draftKey, v); else localStorage.removeItem(draftKey); } catch { /* ignore */ } };

  const num = (s: string) => (s.trim() ? Number(s) : undefined);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!productId) return;
    setBusy(true); setErr(null);
    try {
      setRec(await api<Recommendation>("/api/recommendations", { method: "POST", body: JSON.stringify({
        product_id: productId, target_price: num(form.target), max_budget: num(form.budget), deadline_days: num(form.deadline),
        preferred_vendor: form.vendor || undefined, max_monthly_emi: num(form.maxEmi), preferred_tenure_months: num(form.tenure),
        objective: form.objective, include_vendor_analysis: form.withVendors, include_reviews: form.withReviews, user_banks: prefs.banks,
      }) }));
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }

  function send(e: React.FormEvent) {
    e.preventDefault();
    const text = msg.trim();
    if (!text) return;
    convo.send(text, productId || undefined, { target_price: num(form.target), max_budget: num(form.budget), max_monthly_emi: num(form.maxEmi),
      preferred_tenure_months: num(form.tenure), objective: form.objective, user_banks: prefs.banks });
    setDraft("");
  }
  const busyAnswer = convo.messages.some((m) => m.role === "assistant" && m.status === "pending");

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Purchase advisor</h1>
        <p className="text-sm text-slate-600">The decision comes from a tested rule-based engine using recorded prices. The AI assistant only explains it.</p>
      </div>
      <div className="grid gap-6 lg:grid-cols-5">
        <Card title="Your situation" className="lg:col-span-2">
          <form onSubmit={submit} className="grid grid-cols-2 gap-3">
            <div className="col-span-2"><ProductPicker products={products} value={productId} onChange={setProductId} /></div>
            <div><label className="label" htmlFor="t">Desired price (₹)</label><input id="t" className="input" inputMode="numeric" value={form.target} onChange={set("target")} /></div>
            <div><label className="label" htmlFor="b">Maximum budget (₹)</label><input id="b" className="input" inputMode="numeric" value={form.budget} onChange={set("budget")} /></div>
            <div><label className="label" htmlFor="d">Buy within (days)</label><input id="d" className="input" inputMode="numeric" value={form.deadline} onChange={set("deadline")} /></div>
            <div><label className="label" htmlFor="v">Preferred vendor</label><input id="v" className="input" value={form.vendor} onChange={set("vendor")} /></div>
            <div><label className="label" htmlFor="m">Max monthly EMI (₹)</label><input id="m" className="input" inputMode="numeric" value={form.maxEmi} onChange={set("maxEmi")} /></div>
            <div><label className="label" htmlFor="n">Preferred tenure (months)</label><input id="n" className="input" inputMode="numeric" value={form.tenure} onChange={set("tenure")} /></div>
            <div className="col-span-2"><label className="label" htmlFor="o">Priority</label>
              <select id="o" className="input" value={form.objective} onChange={set("objective")}>
                <option value="lowest_total_cost">Lowest total cost</option><option value="lowest_monthly_payment">Lowest monthly payment</option>
                <option value="shortest_tenure">Shortest repayment</option><option value="lowest_interest">Lowest interest</option><option value="balanced">Balance cost and convenience</option>
              </select></div>
            <label className="col-span-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={form.withVendors} onChange={set("withVendors")} /> Compare vendors &amp; EMI plans (checks vendor sites if cached data is old)</label>
            <label className="col-span-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={form.withReviews} onChange={set("withReviews")} /> Include review analysis</label>
            <button className="btn-primary col-span-2" disabled={!productId || busy}>{busy ? "Analysing…" : "Get recommendation"}</button>
          </form>
        </Card>
        <Card title="Recommendation" className="lg:col-span-3">
          {err && <Notice tone="error">{err}</Notice>}
          {busy && <Loading what="Analysing price history" />}
          {rec ? <RecommendationView rec={rec} /> : !busy && <p className="text-sm text-slate-500">Fill in your situation or ask a question below.</p>}
        </Card>
      </div>
      <div className="grid gap-6 lg:grid-cols-4">
        <Card title="Your conversations" className="lg:col-span-1" actions={<button className="btn-secondary px-3 py-1" onClick={() => { router.replace("/advisor", { scroll: false }); void convo.open(null); }}>New</button>}>
          {convo.sessions.length === 0 ? <p className="text-sm text-slate-500">Questions you ask are saved here.</p> : (
            <ul className="-mx-2 max-h-[30rem] space-y-1 overflow-y-auto">{convo.sessions.map((s) => (
              <li key={s.id}>
                <button onClick={() => router.replace(`/advisor?session=${s.id}`, { scroll: false })}
                  className={`w-full rounded-lg px-2 py-1.5 text-left text-sm ${s.id === convo.sessionId ? "bg-brand-50 text-brand-900" : "hover:bg-slate-100"}`}>
                  <div className="truncate font-medium">{s.title}</div>
                  <div className="text-xs text-slate-500">{s.questions ?? 0} question{s.questions === 1 ? "" : "s"} · {ago(s.updated_at)}</div>
                </button>
              </li>))}</ul>
          )}
        </Card>
        <Card title={convo.sessionId ? convo.title : "Ask the assistant"} className="lg:col-span-3">
          {convo.connection !== "online" && (
            <div className="mb-3"><Notice tone="warn">{convo.connection === "offline" ? "You are offline." : "The server is not answering."} Your question is saved and will be sent automatically when the connection is back.</Notice></div>
          )}
          {convo.error && <div className="mb-3"><Notice tone="error">{convo.error}</Notice></div>}
          <div className="max-h-[28rem] space-y-3 overflow-y-auto pr-1" aria-live="polite">
            {convo.loading && <Loading what="Loading conversation" />}
            {!convo.loading && convo.messages.length === 0 && <p className="text-sm text-slate-500">Try: “Is it worth waiting for Big Billion Days?”, “Which EMI plan costs the least overall?”, “What if I can afford only ₹500 per month?”, “What are the main review complaints?” Follow-up questions keep the context of the conversation.</p>}
            {convo.messages.map((t) => t.role === "user" ? (
              <div key={`${t.request_id}-u`} className="ml-auto max-w-3xl whitespace-pre-wrap rounded-2xl bg-brand-600 px-4 py-2 text-sm text-white">{t.content}</div>
            ) : t.status === "pending" ? (
              <div key={`${t.request_id}-a`} className="max-w-3xl rounded-2xl bg-slate-100 px-4 py-2 text-sm"><Loading what={t.local === "sending" && convo.connection !== "online" ? "Waiting for connection" : "Thinking"} /></div>
            ) : (
              <div key={`${t.request_id}-a`} className={`max-w-3xl rounded-2xl px-4 py-2 text-sm ${t.status === "failed" ? "bg-rose-50 text-rose-900 ring-1 ring-rose-200" : "bg-slate-100 text-slate-900"}`}>
                <div className="whitespace-pre-wrap">{t.content}</div>
                {t.status === "failed"
                  ? <button className="btn-secondary mt-2 px-3 py-1" onClick={() => convo.retry(t.request_id)}>Retry</button>
                  : <div className="mt-1 text-[11px] text-slate-500">{t.meta?.llm_used ? "Explained by Gemini" : "Template explanation"} · vendor sites {t.meta?.vendor_data_accessed ? "contacted" : "not contacted"}</div>}
              </div>
            ))}
            <div ref={endRef} />
          </div>
          <form onSubmit={send} className="mt-3 flex gap-2">
            <input className="input" placeholder="Ask about price, timing, EMI or reviews…" value={msg} onChange={(e) => setDraft(e.target.value)} maxLength={2000} aria-label="Message" />
            <button className="btn-primary" disabled={busyAnswer || !msg.trim()}>Send</button>
          </form>
          <p className="mt-2 text-xs text-slate-500">Conversations are saved to your account. Never share card numbers, CVV, OTPs or passwords. The assistant is read-only and cannot buy anything.</p>
        </Card>
      </div>
    </div>
  );
}

export default function AdvisorPage() {
  return <RequireAuth><Suspense fallback={<Loading />}><Advisor /></Suspense></RequireAuth>;
}
