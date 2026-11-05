import type { Metadata } from "next";
import "./globals.css";
import "./studio.css";
import "./research.css";
import "./presentation.css";
import "./themes.css";
import "./dropdowns.css";
import { THEME_INIT_SCRIPT } from "@/lib/site-theme";

export const metadata: Metadata = {
  title: "Souvenir Studio — подборка с характером",
  description:
    "Рабочее пространство для подбора сувенирной продукции по вашему запросу.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru" data-site-theme="current" suppressHydrationWarning>
      <head><script id="site-theme-init" dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} /></head>
      <body>{children}</body>
    </html>
  );
}
