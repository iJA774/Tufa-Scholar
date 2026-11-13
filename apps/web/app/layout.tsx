import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TUFA Scholar",
  description: "Traceable Unified Fact-finding Agent。学术，触手可及。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
