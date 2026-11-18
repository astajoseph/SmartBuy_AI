import type { Metadata } from "next";
import { Rethink_Sans } from "next/font/google";
import { AuthProvider } from "@/components/AuthProvider";
import Nav from "@/components/Nav";
import "./globals.css";

const sans = Rethink_Sans({ subsets: ["latin"], variable: "--font-sans", display: "swap" });

export const metadata: Metadata = {
  title: "SmartBuy: buy at the right price",
  description: "Price history, sale-event analysis, store comparison and an explainable buy-or-wait advisor for Indian shoppers.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-IN" className={sans.variable}>
      <body className="font-sans">
        <AuthProvider>
          <Nav />
          <main className="mx-auto max-w-[1320px] px-4 py-5 md:px-8">{children}</main>
        </AuthProvider>
        <footer className="mt-10 border-t border-slate-200 bg-white">
          <div className="mx-auto flex max-w-[1320px] flex-wrap justify-between gap-4 px-4 py-6 text-xs text-slate-500 md:px-8">
            <span>Past prices come from price-history sites; current prices come from SmartBuy's own daily store checks.</span>
            <span>SmartBuy explains price data; it is not financial advice.</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
