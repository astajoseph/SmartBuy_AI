const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function money(v: number | string | null | undefined, exact = false): string {
  if (v === null || v === undefined || v === "") return "Unknown";
  const n = typeof v === "string" ? Number(v) : v;
  if (Number.isNaN(n)) return "Unknown";
  return exact ? inr2.format(n) : inr.format(n);
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "Unknown";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "Never";
  return new Date(v).toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) + " IST";
}

export function date(v: string | null | undefined): string {
  if (!v) return "Unknown";
  return new Date(v.length === 10 ? v + "T00:00:00+05:30" : v).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
}

export function ago(v: string | null | undefined): string {
  if (!v) return "never";
  const h = (Date.now() - new Date(v).getTime()) / 3_600_000;
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min ago`;
  if (h < 48) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export const aspectLabel = (a: string) => a.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
