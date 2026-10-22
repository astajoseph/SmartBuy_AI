"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import { authConfigured } from "@/lib/supabase";

const LINKS = [
  ["/", "Dashboard"], ["/advisor", "Purchase advisor"], ["/vendors", "Vendors & EMI"], ["/reviews", "Reviews"],
  ["/sales", "Sale intelligence"], ["/settings", "Settings"],
] as const;

export default function Nav() {
  const path = usePathname();
  const { session, profile, isAdmin, signOut } = useAuth();
  const links: (readonly [string, string])[] = isAdmin || !authConfigured ? [...LINKS, ["/admin", "Admin"]] : [...LINKS];
  const item = (href: string, label: string) => {
    const active = href === "/" ? path === "/" : path.startsWith(href);
    return (
      <Link key={href} href={href} aria-current={active ? "page" : undefined}
        className={`rounded-md px-3 py-1.5 ${active ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-100"}`}>
        {label}
      </Link>
    );
  };
  return (
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <Link href="/" className="flex items-center gap-2 font-semibold text-slate-900">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">₹</span>
          SmartBuy AI
        </Link>
        <nav className="flex flex-wrap gap-1 text-sm" aria-label="Main">{links.map(([href, label]) => item(href, label))}</nav>
        {authConfigured && (
          <div className="ml-auto flex items-center gap-2 text-sm">
            {session ? (
              <>
                <Link href="/account" className={`max-w-[14rem] truncate rounded-md px-3 py-1.5 ${path.startsWith("/account") ? "bg-brand-50 font-medium text-brand-700" : "text-slate-700 hover:bg-slate-100"}`}
                  title={session.user.email}>
                  {profile?.display_name || session.user.email}{isAdmin && <span className="ml-1.5 rounded bg-brand-600 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-white">Admin</span>}
                </Link>
                <button className="btn-secondary px-3 py-1.5" onClick={() => void signOut()}>Sign out</button>
              </>
            ) : (
              <Link href="/login" className="btn-primary px-3 py-1.5">Sign in</Link>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
