"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/components/AuthProvider";
import { authConfigured } from "@/lib/supabase";

const CATS = [
  ["/", "Today's best buys"], ["/advisor", "Should I buy?"], ["/vendors", "Compare stores & EMI"],
  ["/reviews", "Reviews"], ["/sales", "Sale calendar"], ["/settings", "Price alerts"],
] as const;

export default function Nav() {
  const path = usePathname();
  const router = useRouter();
  const { session, profile, isAdmin, signOut } = useAuth();
  const [q, setQ] = useState("");
  useEffect(() => {   // keep the box in sync when arriving on /?q=...
    if (path === "/") setQ(new URLSearchParams(window.location.search).get("q") ?? "");
  }, [path]);
  const cats: (readonly [string, string])[] = isAdmin || !authConfigured ? [...CATS, ["/admin", "Admin"]] : [...CATS];
  const active = (href: string) => (href === "/" ? path === "/" || path.startsWith("/products") : path.startsWith(href));
  const search = (e: FormEvent) => {
    e.preventDefault();
    router.push(q.trim() ? `/?q=${encodeURIComponent(q.trim())}` : "/");
  };
  const name = profile?.display_name || session?.user.email;

  return (
    <header className="bg-white">
      <div className="bg-brand-600 text-[12.5px] text-brand-100">
        <div className="mx-auto flex h-8 max-w-[1320px] items-center justify-between px-4 md:px-8">
          <span>Store prices checked every day</span>
          <div className="hidden gap-5 sm:flex">
            <Link href="/sales" className="hover:text-white">Sale calendar</Link>
            <Link href="/advisor" className="hover:text-white">How the advice works</Link>
          </div>
        </div>
      </div>

      <div className="mx-auto flex max-w-[1320px] flex-wrap items-center gap-x-7 gap-y-3 px-4 py-4 md:px-8">
        <Link href="/" className="text-2xl font-bold tracking-tight text-brand-600" aria-label="SmartBuy home">
          Smart<span className="text-gold-500">Buy</span>
        </Link>
        <form onSubmit={search} role="search" className="order-last flex h-11 w-full overflow-hidden rounded-md border-2 border-brand-600 md:order-none md:w-auto md:flex-1">
          <label htmlFor="site-search" className="sr-only">Search tracked products</label>
          <input id="site-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search tracked products by name or brand"
            className="min-w-0 flex-1 px-4 text-sm outline-none placeholder:text-slate-400" />
          <button type="submit" className="bg-brand-600 px-6 text-sm font-semibold text-white hover:bg-brand-700">Search</button>
        </form>
        <div className="ml-auto flex items-center gap-6 text-[13px] md:ml-0">
          {authConfigured && (session ? (
            <>
              <Link href="/account" className="max-w-[12rem] leading-tight" title={session.user.email}>
                <span className="block text-slate-500">Hello,</span>
                <span className="block truncate font-semibold text-slate-900">{name}{isAdmin && <span className="ml-1.5 rounded bg-brand-600 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-white">Admin</span>}</span>
              </Link>
              <button className="text-slate-600 underline-offset-4 hover:text-brand-600 hover:underline" onClick={() => void signOut()}>Sign out</button>
            </>
          ) : (
            <Link href="/login" className="leading-tight">
              <span className="block text-slate-500">Hello, sign in</span>
              <span className="block font-semibold text-slate-900">Account</span>
            </Link>
          ))}
          <Link href="/settings" className="leading-tight">
            <span className="block text-slate-500">Your</span>
            <span className="block font-semibold text-slate-900">Price alerts</span>
          </Link>
        </div>
      </div>

      <nav aria-label="Main" className="border-y border-slate-200">
        <div className="mx-auto flex max-w-[1320px] gap-7 overflow-x-auto px-4 text-sm font-medium md:px-8">
          {cats.map(([href, label]) => (
            <Link key={href} href={href} aria-current={active(href) ? "page" : undefined}
              className={`flex h-11 flex-none items-center whitespace-nowrap ${active(href) ? "text-brand-600 shadow-[inset_0_-3px_0_theme(colors.gold.500)]" : "text-slate-700 hover:text-brand-600"}`}>
              {label}
            </Link>
          ))}
        </div>
      </nav>
    </header>
  );
}
