import type { Metadata } from "next";
import "./globals.css";
import "./studio.css";
import "./research.css";

export const metadata: Metadata = {
  title: "Souvenir Studio — подборка с характером",
  description:
    "Рабочее пространство для подбора сувенирной продукции по вашему запросу.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
