import type { Metadata } from "next";
import LaunchPage from "./launch/LaunchPage";

export const metadata: Metadata = {
  title: "TUFA Scholar — 学术，触手可及",
  description: "TUFA Scholar — Traceable Unified Fact-finding Agent。学术，触手可及。",
};

export default function Page() {
  return <LaunchPage />;
}
