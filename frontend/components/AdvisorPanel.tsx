"use client";
/**
 * "Should I buy?" chat panel that slides in from the right on a product page.
 * It already knows the product: every question is sent with this product's id, and opening the panel for the
 * first time asks "Should I buy <product> now?" straight away. Works without signing in (POST /api/assistant/chat);
 * for signed-in users the backend also records the question in their history. The chat is kept per product in this
 * browser (localStorage) so closing and reopening the panel continues it.
 */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useAuth } from "@/components/AuthProvider";
import { ACTION } from "@/components/ui";
import { api } from "@/lib/api";
import type { Action } from "@/types/api";

type Msg = { role: "user" | "assistant"; content: string; action?: Action; failed?: boolean };
const QUICK = ["What's a good target price?", "Will the next sale bring it down?", "Which store is cheapest right now?", "Is there a bank offer or EMI deal?"];
const key = (pid: string) => `smartbuy.panel.${pid}`;
const load = (pid: string): Msg[] => { try { return JSON.parse(localStorage.getItem(key(pid)) || "[]") as Msg[]; } catch { return []; } };
const save = (pid: string, m: Msg[]) => { try { localStorage.setItem(key(pid), JSON.stringify(m.slice(-30))); } catch { /* storage unavailable */ } };

/** Tiny formatter for the assistant's text: **bold**, "- " / "* " bullets, paragraphs. No HTML is injected. */
function Rich({ text }: { text: string }) {
  const bold = (s: string): ReactNode[] => s.split(/(\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p));
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => { if (list.length) { blocks.push(<ul key={blocks.length} className="my-1 list-disc space-y-0.5 pl-5">{list.map((l, i) => <li key={i}>{bold(l)}</li>)}</ul>); list = []; } };
  for (const line of text.split("\n")) {
    const m = line.match(/^\s*[-*•]\s+(.*)/);
    if (m) { list.push(m[1]); continue; }
    flush();
    if (line.trim()) blocks.push(<p key={blocks.length} className="my-1">{bold(line)}</p>);
  }
  flush();
  return <>{blocks}</>;
}

export default function AdvisorPanel({ open, onClose, productId, productName }: {
  open: boolean; onClose: () => void; productId: string; productName: string;
}) {
  const { session } = useAuth();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const started = useRef(false);

  useEffect(() => { setMsgs(load(productId)); started.current = false; }, [productId]);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [msgs, busy]);

  const ask = useCallback(async (text: string, base?: Msg[]) => {
    const q = text.trim();
    if (!q || busy) return;
    const before = base ?? msgs;
    const next: Msg[] = [...before, { role: "user", content: q }];
    setMsgs(next); setInput(""); setBusy(true);
    try {
      const r = await api<{ reply: string; recommendation?: { action?: Action } }>("/api/assistant/chat", {
        method: "POST",
        body: JSON.stringify({
          message: q, product_id: productId,
          history: before.filter((m) => !m.failed).slice(-10).map((m) => ({ role: m.role, content: m.content.slice(0, 4000) })),
        }),
      });
      const done: Msg[] = [...next, { role: "assistant", content: r.reply, action: r.recommendation?.action }];
      setMsgs(done); save(productId, done);
    } catch (e) {
      const status = (e as { status?: number }).status;
      const why = status === 429 ? "You've asked a lot of questions in a short time. Please wait a few minutes and try again."
        : status === 0 ? "Connection lost. Check your internet connection and try again."
        : "Sorry, the advisor couldn't answer just now. Please try again.";
      setMsgs([...next, { role: "assistant", content: why, failed: true }]);
    } finally {
      setBusy(false);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [busy, msgs, productId]);

  // first open: ask the obvious question for this product
  useEffect(() => {
    if (open && !started.current && msgs.length === 0 && !busy) {
      started.current = true;
      void ask(`Should I buy the ${productName} now?`, []);
    }
    if (open) setTimeout(() => inputRef.current?.focus(), 50);
  }, [open, msgs.length, busy, ask, productName]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const submit = (e: FormEvent) => { e.preventDefault(); void ask(input); };
  const reset = () => { setMsgs([]); save(productId, []); started.current = false; };
  const lastFailed = msgs[msgs.length - 1]?.failed;

  return (
    <>
      <div onClick={onClose} aria-hidden
        className={`fixed inset-0 z-40 !m-0 bg-slate-900/20 transition-opacity ${open ? "opacity-100" : "pointer-events-none opacity-0"}`} />
      <aside role="dialog" aria-modal="true" aria-label={`Purchase advisor for ${productName}`}
        className={`fixed inset-y-0 right-0 z-50 !m-0 flex w-full flex-col border-l border-slate-200 bg-white shadow-[-8px_0_24px_rgba(27,33,36,.08)] transition-transform duration-200 sm:w-[440px] ${open ? "translate-x-0" : "translate-x-full"}`}>
        <header className="flex items-start gap-3 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0 flex-1">
            <div className="text-xs font-semibold uppercase tracking-wide text-brand-600">Purchase advisor</div>
            <h2 className="mt-0.5 line-clamp-2 font-bold leading-snug">{productName}</h2>
          </div>
          {msgs.length > 0 && <button onClick={reset} className="mt-1 text-[13px] font-semibold text-slate-500 hover:text-brand-600">New chat</button>}
          <button onClick={onClose} aria-label="Close advisor" className="-mr-1 rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-900">
            <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" /></svg>
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4 text-[14px] leading-relaxed" aria-live="polite">
          {msgs.map((m, i) => m.role === "user" ? (
            <div key={i} className="ml-10 rounded-lg bg-brand-600 px-3.5 py-2.5 text-white">{m.content}</div>
          ) : (
            <div key={i} className={`mr-4 rounded-lg border px-3.5 py-3 ${m.failed ? "border-alert-50 bg-alert-50 text-alert-600" : "border-slate-200 bg-slate-50 text-slate-800"}`}>
              {m.action && ACTION[m.action] && <span className={`tag mb-1.5 ${ACTION[m.action].cls}`}>{ACTION[m.action].label}</span>}
              <Rich text={m.content} />
            </div>
          ))}
          {busy && <div className="mr-4 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-3 text-slate-500">Checking the price history…</div>}
          {lastFailed && !busy && (
            <button className="btn-quiet" onClick={() => { const q = msgs[msgs.length - 2]?.content; if (q) void ask(q, msgs.slice(0, -2)); }}>Try again</button>
          )}
          {!busy && msgs.length > 0 && !lastFailed && (
            <div className="flex flex-wrap gap-2 pt-1">
              {QUICK.map((q) => <button key={q} onClick={() => void ask(q)} className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-left text-[13px] text-slate-700 hover:border-brand-600 hover:text-brand-600">{q}</button>)}
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form onSubmit={submit} className="border-t border-slate-200 px-4 py-3">
          <div className="flex items-end gap-2">
            <label htmlFor="advisor-q" className="sr-only">Ask about this product</label>
            <textarea id="advisor-q" ref={inputRef} rows={1} value={input} maxLength={2000}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void ask(input); } }}
              placeholder="Ask about this product…" className="input max-h-32 min-h-[42px] resize-none" />
            <button type="submit" disabled={busy || !input.trim()} className="btn-primary h-[42px]">Send</button>
          </div>
          <p className="mt-2 text-[11px] text-slate-500">
            Answers come from recorded prices; SmartBuy never invents prices.{" "}
            {session ? "This chat is added to your history." : <><Link href="/login" className="font-semibold text-brand-600 hover:underline">Sign in</Link> to keep a history of your questions.</>}
          </p>
        </form>
      </aside>
    </>
  );
}
