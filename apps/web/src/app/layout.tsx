import type { Metadata } from "next";
import { Geist } from "next/font/google";
import "./globals.css";
import "./studio.css";
import "./research.css";
import "./presentation.css";
import "./themes.css";
import "./dropdowns.css";
import "./typography.css";
import "./commercial-offer.css";
import "./scrollbars.css";
import "./client-feedback.css";
import { THEME_INIT_SCRIPT } from "@/lib/site-theme";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin", "cyrillic", "cyrillic-ext"],
  weight: "variable",
  style: "normal",
  display: "swap",
  fallback: ["Segoe UI", "Arial", "sans-serif"],
});

export const metadata: Metadata = {
  title: "Souvenir Studio — подборка с характером",
  description:
    "Рабочее пространство для подбора сувенирной продукции по вашему запросу.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru" className={geistSans.variable} data-site-theme="current" suppressHydrationWarning>
      <head><script id="site-theme-init" dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} /></head>
      <body>{children}</body>
    </html>
  );
}
