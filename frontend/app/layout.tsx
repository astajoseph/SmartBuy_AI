import type { Metadata } from "next";
import Nav from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "SmartBuy AI — Indian purchase intelligence",
  description: "Price history, sale-event analysis, EMI comparison and an explainable purchase advisor.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-IN">
      <body>
        <Nav />
        <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
        <footer className="mx-auto max-w-7xl px-4 pb-8 text-xs text-slate-500">
          Prices come from registered price-history sources; vendor pages are only checked when you ask for EMI, vendor or review analysis.
        </footer>
      </body>
    </html>
  );
}
