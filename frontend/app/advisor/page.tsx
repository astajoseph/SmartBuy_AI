"use client";
import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import ProductPicker from "@/components/ProductPicker";
import RecommendationView from "@/components/RecommendationView";
import { Card, Loading, Notice } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { usePrefs } from "@/hooks/usePrefs";
import { api } from "@/lib/api";
import type { ProductSummary, Recommendation } from "@/types/api";

interface ChatTurn { role: "user" | "assistant"; content: string; meta?: { vendor?: boolean; llm?: boolean } }

function Advisor() {
  const params = useSearchParams();
  const { data: products } = useApi<ProductSummary[]>("/api/products");
  const { prefs } = usePrefs();
  const [productId, setProductId] = useState(params.get("product") ?? "");
  const [form, setForm] = useState({ target: "", budget: "", deadline: "", vendor: "", maxEmi: "", tenure: "", objective: "lowest_total_cost", withVendors: false, withReviews: false });
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [chat, setChat] = useState<ChatTurn[]>([]);
  const [msg, setMsg] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!productId && products?.length === 1) setProductId(products[0].id);
  }, [products, productId]);
  useEffect(() => {
    setForm((f) => ({ ...f, target: f.target || (prefs.targets[productId]?.toString() ?? ""), budget: f.budget || (prefs.maxBudget?.toString() ?? ""),
      maxEmi: f.maxEmi || (prefs.maxMonthlyEmi?.toString() ?? ""), objective: prefs.objective }));
  }, [prefs, productId]);
  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [chat]);

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

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = msg.trim();
    if (!text) return;
    const history = chat.map(({ role, content }) => ({ role, content }));
    setChat((c) => [...c, { role: "user", content: text }]); setMsg(""); setChatBusy(true);
    try {
      const r = await api<{ reply: string; vendor_data_accessed?: boolean; llm_used?: boolean; recommendation?: Recommendation }>("/api/assistant/chat", {
        method: "POST", body: JSON.stringify({ message: text, product_id: productId || undefined, history,
          preferences: { target_price: num(form.target), max_budget: num(form.budget), max_monthly_emi: num(form.maxEmi),
            preferred_tenure_months: num(form.tenure), objective: form.objective, user_banks: prefs.banks } }) });
      setChat((c) => [...c, { role: "assistant", content: r.reply, meta: { vendor: r.vendor_data_accessed, llm: r.llm_used } }]);
      if (r.recommendation) setRec(r.recommendation);
    } catch (x) { setChat((c) => [...c, { role: "assistant", content: `Error: ${(x as Error).message}` }]); } finally { setChatBusy(false); }
  }

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
      <Card title="Ask the assistant">
        <div className="max-h-[28rem] space-y-3 overflow-y-auto pr-1" aria-live="polite">
          {chat.length === 0 && <p className="text-sm text-slate-500">Try: “Is it worth waiting for Big Billion Days?”, “Which EMI plan costs the least overall?”, “What if I can afford only ₹500 per month?”, “What are the main review complaints?”</p>}
          {chat.map((t, i) => (
            <div key={i} className={`max-w-3xl rounded-2xl px-4 py-2 text-sm ${t.role === "user" ? "ml-auto bg-brand-600 text-white" : "bg-slate-100 text-slate-900"}`}>
              <div className="whitespace-pre-wrap">{t.content}</div>
              {t.meta && <div className="mt-1 text-[11px] text-slate-500">{t.meta.llm ? "Explained by Gemini" : "Template explanation"} · vendor sites {t.meta.vendor ? "contacted" : "not contacted"}</div>}
            </div>
          ))}
          {chatBusy && <Loading what="Thinking" />}
          <div ref={endRef} />
        </div>
        <form onSubmit={send} className="mt-3 flex gap-2">
          <input className="input" placeholder="Ask about price, timing, EMI or reviews…" value={msg} onChange={(e) => setMsg(e.target.value)} maxLength={2000} aria-label="Message" />
          <button className="btn-primary" disabled={chatBusy || !msg.trim()}>Send</button>
        </form>
        <p className="mt-2 text-xs text-slate-500">Never share card numbers, CVV, OTPs or passwords. The assistant is read-only and cannot buy anything.</p>
      </Card>
    </div>
  );
}

export default function AdvisorPage() {
  return <Suspense fallback={<Loading />}><Advisor /></Suspense>;
}
