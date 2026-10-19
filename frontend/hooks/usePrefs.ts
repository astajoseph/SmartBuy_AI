"use client";
import { useEffect, useState } from "react";

// Per-browser preferences (no account needed). Never store card numbers or other credentials here.
export interface Prefs {
  maxBudget?: number; maxMonthlyEmi?: number; preferredTenure?: number; banks: string[];
  objective: "lowest_total_cost" | "lowest_monthly_payment" | "shortest_tenure" | "lowest_interest" | "balanced";
  targets: Record<string, number>; alerts: { targetReached: boolean; priceDrop: boolean; sales: boolean };
}
const DEFAULT: Prefs = { banks: [], objective: "lowest_total_cost", targets: {}, alerts: { targetReached: true, priceDrop: true, sales: true } };
const KEY = "smartbuy.prefs.v1";

export function usePrefs() {
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT);
  useEffect(() => {
    try { const raw = localStorage.getItem(KEY); if (raw) setPrefs({ ...DEFAULT, ...JSON.parse(raw) }); } catch { /* storage unavailable */ }
  }, []);
  const save = (p: Prefs) => { setPrefs(p); try { localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* ignore */ } };
  return { prefs, save };
}
