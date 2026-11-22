export const webSourceLabel = (level?: string) => ({
  web_snippet: "网页搜索摘要",
  web_content: "网页正文节选",
  abstract: "仅摘要",
}[level || ""] || "网页信息");

export const nodeLabels: Record<string, string> = {
  dispatcher: "任务分流",
  background_investigator: "背景检索",
  planner: "研究规划",
  supervisor: "研究调度",
  researcher: "搜索与证据整理",
  analyst: "证据分析",
  reporter: "报告整理",
};

export const toolLabels: Record<string, string> = {
  search_web: "网页检索",
  read_web_page: "读取网页",
  search_papers: "学术检索",
  search_local_papers: "检索本地文献",
  select_local_domains: "查看研究领域文献",
  read_local_markdown: "读取本地 Markdown",
  collect_evidence: "提取证据",
};
