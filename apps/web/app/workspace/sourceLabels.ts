export const webSourceLabel = (level?: string) => ({
  web_snippet: "网页搜索摘要",
  web_content: "网页正文节选",
  abstract: "仅摘要",
}[level || ""] || "网页信息");
