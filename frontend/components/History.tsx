"use client";
import Link from "next/link";
import { dateTime } from "@/lib/format";
import type { Activity, ActivityKind, ChatMessageRow } from "@/types/api";

export const KIND_LABEL: Record<ActivityKind, [string, string]> = {
  sign_in: ["Sign-in", "bg-slate-100 text-slate-700 ring-slate-200"],
  assistant_chat: ["Assistant", "bg-violet-50 text-violet-700 ring-violet-200"],
  recommendation: ["Advisor", "bg-emerald-50 text-emerald-700 ring-emerald-200"],
  notifications_viewed: ["Notifications", "bg-sky-50 text-sky-700 ring-sky-200"],
  target_saved: ["Price alert", "bg-amber-50 text-amber-800 ring-amber-200"],
  admin_action: ["Admin action", "bg-rose-50 text-rose-700 ring-rose-200"],
};

// One user's (or everyone's, with showUser) history. Assistant entries link to the saved conversation.
export function ActivityList({ items, showUser = false, chatHref }: { items: Activity[]; showUser?: boolean; chatHref?: (sessionId: string) => string }) {
  if (!items.length) return <p className="text-sm text-slate-500">Nothing recorded yet.</p>;
  return (
    <ul className="divide-y divide-slate-100 text-sm">
      {items.map((a) => {
        const [label, cls] = KIND_LABEL[a.kind] ?? [a.kind, "bg-slate-100 text-slate-700 ring-slate-200"];
        const sid = typeof a.payload?.session_id === "string" ? a.payload.session_id : null;
        return (
          <li key={a.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 py-2">
            <span className={`mt-0.5 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ${cls}`}>{label}</span>
            <div className="min-w-0 flex-1">
              <div className="break-words">{a.summary}</div>
              <div className="text-xs text-slate-500">
                {dateTime(a.created_at)}{showUser && <> · {a.display_name || a.email || a.user_id}</>}{a.product_id && <> · {a.product_id}</>}
                {sid && chatHref && <> · <Link className="text-brand-700 hover:underline" href={chatHref(sid)}>open conversation</Link></>}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function Transcript({ messages }: { messages: ChatMessageRow[] }) {
  if (!messages.length) return <p className="text-sm text-slate-500">No messages.</p>;
  return (
    <div className="space-y-3">
      {messages.map((m) => (
        <div key={m.id} className={`max-w-3xl rounded-2xl px-4 py-2 text-sm ${m.role === "user" ? "ml-auto bg-brand-600 text-white" : m.status === "failed" ? "bg-rose-50 text-rose-900" : "bg-slate-100"}`}>
          <div className="whitespace-pre-wrap">{m.content || (m.status === "pending" ? "(answer still being generated)" : "")}</div>
          <div className={`mt-1 text-[11px] ${m.role === "user" ? "text-brand-100" : "text-slate-500"}`}>{dateTime(m.created_at)}{m.status !== "complete" && ` · ${m.status}`}</div>
        </div>
      ))}
    </div>
  );
}
