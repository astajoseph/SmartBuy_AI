"use client";
// Saved, interruption-proof assistant conversations.
//
// Every question gets a request id before it is sent and is kept in an "outbox" in localStorage until the
// server has answered it. If the connection drops (or the tab is closed) mid-question, the outbox is replayed
// when the browser is back online or the page is reopened. The server treats a replayed request id as the
// same question: it returns the answer it already saved, or finishes the one it is still generating, so the
// conversation continues exactly where it stopped. The server also feeds the earlier messages of the
// conversation back to the assistant, so a resumed chat keeps its context.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ChatMessageRow, ChatSession } from "@/types/api";

interface Outgoing { sessionId: string; requestId: string; message: string; productId?: string; preferences: Record<string, unknown>; createSession?: boolean }
export type LocalMessage = ChatMessageRow & { local?: "sending" | "waiting" };
export type Connection = "online" | "offline" | "reconnecting";

const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
  (Number(c) ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (Number(c) / 4)))).toString(16)));
const read = <T,>(k: string, d: T): T => { try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : d; } catch { return d; } };
const write = (k: string, v: unknown) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } };

function localPair(o: Outgoing): LocalMessage[] {
  const now = new Date().toISOString();
  return [
    { id: -Date.now(), request_id: o.requestId, role: "user", content: o.message, status: "complete", meta: {}, created_at: now },
    { id: -Date.now() - 1, request_id: o.requestId, role: "assistant", content: "", status: "pending", meta: {}, created_at: now, local: "sending" },
  ];
}

export function useConversation(userId: string | undefined) {
  const outboxKey = `smartbuy.chat.outbox.${userId}`;
  const lastKey = `smartbuy.chat.last.${userId}`;
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [title, setTitle] = useState<string>("New conversation");
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [connection, setConnection] = useState<Connection>("online");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const flushing = useRef(false);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const backoff = useRef(2000);

  const outbox = useCallback(() => read<Outgoing[]>(outboxKey, []), [outboxKey]);
  const setOutbox = useCallback((o: Outgoing[]) => write(outboxKey, o), [outboxKey]);

  const refreshSessions = useCallback(async () => {
    if (!userId) return;
    try { setSessions(await api<ChatSession[]>("/api/assistant/sessions")); } catch { /* list is a convenience; keep the old one */ }
  }, [userId]);

  // Server state + any questions of this conversation still waiting in the outbox.
  const merge = useCallback((server: ChatMessageRow[], id: string): LocalMessage[] => {
    const known = new Set(server.map((m) => m.request_id));
    const pending = outbox().filter((o) => o.sessionId === id && !known.has(o.requestId)).flatMap(localPair);
    return [...server, ...pending];
  }, [outbox]);

  const load = useCallback(async (id: string) => {
    const s = await api<ChatSession & { messages: ChatMessageRow[] }>(`/api/assistant/sessions/${id}`);
    if (sessionRef.current !== id) return null;
    setTitle(s.title);
    setMessages(merge(s.messages, id));
    return s;
  }, [merge]);

  const flush = useCallback(async () => {
    if (flushing.current || !userId) return;
    flushing.current = true;
    clearTimeout(retryTimer.current);
    try {
      for (let o = outbox()[0]; o; o = outbox()[0]) {
        try {
          if (o.createSession) await api("/api/assistant/sessions", { method: "POST", body: JSON.stringify({ id: o.sessionId, product_id: o.productId }) });
          const r = await api<{ title: string; message: ChatMessageRow }>(`/api/assistant/sessions/${o.sessionId}/messages`, {
            method: "POST", body: JSON.stringify({ request_id: o.requestId, message: o.message, product_id: o.productId, preferences: o.preferences }) });
          setOutbox(outbox().filter((x) => x.requestId !== o.requestId));
          setConnection("online"); backoff.current = 2000;
          if (sessionRef.current === o.sessionId) {
            setTitle(r.title);
            setMessages((ms) => ms.map((m) => (m.request_id === o.requestId && m.role === "assistant" ? { ...r.message, local: r.message.status === "pending" ? "waiting" : undefined } : m)));
          }
        } catch (e) {
          const err = e as ApiError;
          if (err instanceof ApiError && err.retryable) {
            setConnection(err.status === 0 ? "offline" : "reconnecting");
            retryTimer.current = setTimeout(() => void flush(), backoff.current);
            backoff.current = Math.min(backoff.current * 2, 30000);
            return;
          }
          // Not retryable (e.g. conversation deleted, invalid input): show it on the answer and drop it from the outbox.
          setOutbox(outbox().filter((x) => x.requestId !== o.requestId));
          setMessages((ms) => ms.map((m) => (m.request_id === o.requestId && m.role === "assistant" ? { ...m, status: "failed", content: err.message, local: undefined } : m)));
        }
      }
      void refreshSessions();
    } finally { flushing.current = false; }
  }, [userId, outbox, setOutbox, refreshSessions]);

  const open = useCallback(async (id: string | null) => {
    sessionRef.current = id; setSessionId(id); setError(null);
    if (!id) { setMessages([]); setTitle("New conversation"); write(lastKey, null); return; }
    write(lastKey, id);
    setLoading(true);
    try { await load(id); } catch (e) {
      const err = e as ApiError;
      if (err.status === 404) { write(lastKey, null); sessionRef.current = null; setSessionId(null); setMessages([]); }
      else { setMessages(merge([], id)); }               // offline: still show questions waiting to be sent
      setError(err.status === 404 ? "That conversation no longer exists." : err.message);
    } finally { setLoading(false); }
    void flush();
  }, [load, merge, flush, lastKey]);

  const send = useCallback((message: string, productId: string | undefined, preferences: Record<string, unknown>) => {
    let id = sessionRef.current;
    const createSession = !id;
    if (!id) { id = uuid(); sessionRef.current = id; setSessionId(id); setTitle(message.slice(0, 60)); write(lastKey, id); }
    const o: Outgoing = { sessionId: id, requestId: uuid(), message, productId, preferences, createSession };
    setOutbox([...outbox(), o]);
    setMessages((ms) => [...ms, ...localPair(o)]);
    void flush();
    return id;
  }, [outbox, setOutbox, flush, lastKey]);

  const retry = useCallback((requestId: string) => {
    const q = messages.find((m) => m.request_id === requestId && m.role === "user");
    if (!q || !sessionRef.current) return;
    setOutbox([...outbox(), { sessionId: sessionRef.current, requestId, message: q.content, preferences: {} }]);
    setMessages((ms) => ms.map((m) => (m.request_id === requestId && m.role === "assistant" ? { ...m, status: "pending", content: "", local: "sending" } : m)));
    void flush();
  }, [messages, outbox, setOutbox, flush]);

  const remove = useCallback(async (id: string) => {
    await api(`/api/assistant/sessions/${id}`, { method: "DELETE" });
    setOutbox(outbox().filter((o) => o.sessionId !== id));
    if (sessionRef.current === id) void open(null);
    void refreshSessions();
  }, [outbox, setOutbox, open, refreshSessions]);

  const rename = useCallback(async (id: string, t: string) => {
    await api(`/api/assistant/sessions/${id}/rename`, { method: "POST", body: JSON.stringify({ title: t }) });
    if (sessionRef.current === id) setTitle(t);
    void refreshSessions();
  }, [refreshSessions]);

  // Resume: replay the outbox when the connection comes back, the tab becomes visible, or the page is reopened.
  useEffect(() => {
    if (!userId) return;
    void refreshSessions();
    void flush();
    const wake = () => { backoff.current = 2000; void flush(); };
    const offline = () => setConnection("offline");
    const visible = () => { if (document.visibilityState === "visible") wake(); };
    window.addEventListener("online", wake); window.addEventListener("offline", offline);
    document.addEventListener("visibilitychange", visible);
    return () => { window.removeEventListener("online", wake); window.removeEventListener("offline", offline); document.removeEventListener("visibilitychange", visible); clearTimeout(retryTimer.current); };
  }, [userId, flush, refreshSessions]);

  // An answer that is still being generated on the server (e.g. the first attempt lost its connection): poll until saved.
  const waiting = messages.some((m) => m.role === "assistant" && m.status === "pending" && m.local !== "sending");
  useEffect(() => {
    if (!waiting || !sessionId) return;
    const t = setInterval(() => { void load(sessionId).catch(() => setConnection("offline")); }, 3000);
    return () => clearInterval(t);
  }, [waiting, sessionId, load]);

  return { sessionId, title, messages, sessions, connection, loading, error, lastSessionId: () => read<string | null>(lastKey, null),
           open, send, retry, remove, rename, refreshSessions };
}
