import type { Metadata } from "next";
import WorkspacePage from "./WorkspacePage";

export const metadata: Metadata = {
  title: "研究工作台 | TUFA Scholar",
  description: "在本机项目中管理文献、证据与可追溯科研问答。",
};

export default function Page() {
  return <WorkspacePage />;
}
