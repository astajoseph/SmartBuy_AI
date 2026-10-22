"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Activity } from "@/types/api";

// Newest-first activity history with "load more" (keyset paging on the activity id).
export function useActivityPages(base: string) {
  const [items, setItems] = useState<Activity[]>([]);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const more = useCallback(async (reset = false) => {
    setError(null);
    try {
      const before = !reset && items.length ? `&before=${items[items.length - 1].id}` : "";
      const page = await api<Activity[]>(`${base}${base.includes("?") ? "&" : "?"}limit=50${before}`);
      setItems((x) => (reset ? page : [...x, ...page])); setDone(page.length < 50);
    } catch (e) { setError((e as Error).message); }
  }, [base, items]);
  useEffect(() => { void more(true); }, [base]); // eslint-disable-line react-hooks/exhaustive-deps
  return { items, done, error, more };
}
