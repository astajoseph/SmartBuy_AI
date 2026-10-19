"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  ["/", "Dashboard"], ["/advisor", "Purchase advisor"], ["/vendors", "Vendors & EMI"], ["/reviews", "Reviews"],
  ["/sales", "Sale intelligence"], ["/settings", "Settings"], ["/admin", "Admin"],
] as const;

export default function Nav() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <Link href="/" className="flex items-center gap-2 font-semibold text-slate-900">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">₹</span>
          SmartBuy AI
        </Link>
        <nav className="flex flex-wrap gap-1 text-sm" aria-label="Main">
          {LINKS.map(([href, label]) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} aria-current={active ? "page" : undefined}
                className={`rounded-md px-3 py-1.5 ${active ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-100"}`}>
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
