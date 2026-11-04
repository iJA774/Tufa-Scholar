import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Tufa Scholar",
  description: "本地科研助手",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
