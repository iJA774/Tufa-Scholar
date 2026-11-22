const legacyReasons = new Set([
  "报告生成超时，未能完成终稿。", "无法连接报告模型服务，未能完成终稿。",
  "报告模型服务触发限流，可能是请求过于频繁或额度不足，未能完成终稿。",
  "报告模型服务的 API 密钥或访问权限不可用，未能完成终稿。", "报告模型不存在或无法访问，未能完成终稿。",
  "报告模型服务地址或模型不可用，未能完成终稿。", "报告模型服务无法处理本次请求，请检查模型设置。",
  "报告内容超出模型的输入长度限制，未能完成终稿。", "报告模型服务暂时不可用，未能完成终稿。",
  "报告生成达到长度限制，未能完成终稿。", "报告内容受到模型服务限制，未能完成终稿。",
  "报告模型返回了空内容，未能完成终稿。", "报告模型返回的内容无法使用，未能完成终稿。",
  "终稿没有可保留的正文。",
]);

/** Only generated local PDF paths may pass through the API proxy. */
export function reportLink(value?: string | null): string | undefined {
  if (value && /^\/paper-versions\/[1-9]\d*\/file(?:#page=[1-9]\d*)?$/.test(value)) return `/api${value}`;
  try {
    const url = new URL(value || "");
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined;
  } catch {
    return undefined;
  }
}

/** Recognize evidence identifiers only in generated numbered reference entries. */
export function reportEvidenceIds(content: string): Set<string> {
  const ids = new Set<string>();
  const section = content.split(/^## 参考文献\s*$/m).at(-1);
  if (!section || section === content) return ids;
  for (const line of section.split(/\r?\n/)) {
    if (!/^- \\\[\d+\\\] /.test(line)) continue;
    for (const match of line.matchAll(/(?: · |；)证据 (.*?)(?= · |；|$)/g)) ids.add(match[1]);
  }
  return ids;
}

/** New reports contain only cited evidence; older entries include evidence IDs. */
export function hasTitleOnlyReferences(content: string): boolean {
  const section = content.split(/^## 参考文献\s*$/m).at(-1);
  if (!section || section === content) return false;
  return /^- \\\[\d+\\\] /m.test(section) && reportEvidenceIds(content).size === 0;
}

/** Remove only a known legacy generated preface; never modify the stored report. */
export function displayReport(content: string, hasFallbackEvent: boolean): string {
  const draft = /^> 终稿未能完成，以下为最近一份通过校验的研究草稿。\r?\n> [^\r\n]+\r?\n(?:\r?\n)?/;
  if (draft.test(content)) return content.replace(draft, "");
  const single = /(^|\r?\n\r?\n)> 终稿未完成：([^\r\n]+)(?:\r?\n\r?\n> 未找到通过校验且包含有效引用的研究草稿。)?(?=\r?\n|$)/g;
  return content.replace(single, (full, boundary: string, reason: string) => {
    if (!hasFallbackEvent && !legacyReasons.has(reason)) return full;
    return boundary;
  });
}

export function safeRunError(error?: string | null): { message: string; suggestion: string } {
  const value = error || "";
  if (/model_not_found|模型不存在|模型.*无法访问/i.test(value)) return { message: "模型不存在或无法访问。", suggestion: "请检查手动填写的模型 ID 及访问权限，或选择其他模型。" };
  if (/HTTP 429|限流|额度/.test(value)) return { message: "模型服务触发限流或额度不足。", suggestion: "请检查服务额度，等待一段时间后重试。" };
  if (/HTTP 401|HTTP 403|密钥|权限/.test(value)) return { message: "模型服务的密钥或访问权限不可用。", suggestion: "请检查 API 密钥和模型访问权限后重试。" };
  if (/HTTP 404/.test(value)) return { message: "模型服务地址或模型不可用。", suggestion: "请检查模型服务地址、模型 ID 及访问权限。" };
  if (/OpenAlex|检索/.test(value)) return { message: "文献检索服务未能完成请求。", suggestion: "请检查网络和检索服务设置或额度后重试。" };
  if (/超时|连接|网络/.test(value)) return { message: "无法连接模型服务或请求超时。", suggestion: "请检查模型服务地址和网络连接，或稍后重试。" };
  if (/结构化/.test(value)) return { message: "模型返回的结构化结果无效。", suggestion: "请更换支持结构化输出的模型，或降低研究深度后重试。" };
  return { message: "研究任务未能完成。", suggestion: "请检查模型服务设置和网络，或稍后重试。" };
}
