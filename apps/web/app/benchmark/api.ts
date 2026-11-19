export type Metric = "race" | "fact";
export type Phase = "generate" | Metric;
export type Query = { id: number; prompt: string; language: string; topic: string };
export type Dataset = { id: string; name: string; version: string; commit: string; source: string; count: number | null; languages?: Record<string, number>; domains?: Record<string, number> };
export type Judge = { base_url: string; race_model: string; fact_model: string; clean_model: string; reader_url: string; timeout: number; retries: number; api_key?: string; reader_key?: string; official_defaults?: boolean };
export type Budget = { max_tasks: number; task_seconds: number; total_seconds: number; max_requests: number; max_tokens: number | null; max_cost: number | null };
export type Installation = { dataset: Dataset; scorer_ready: boolean; model: { model: string | null }; search: { engine: string; providers: { id: string; name: string }[] }; judge: Judge; judge_key_configured: boolean; reader_key_configured: boolean };
export type Usage = { requests: number; usage: Record<string, number | null>; usage_coverage: Record<string, number>; errors: number };
export type Totals = Record<"agent" | "judge" | "reader", Usage>;
export type Quality = {
  samples: number; success: number; failed: number; skipped: number; success_rate: number; failure_rate: number;
  race: Record<string, number> | null;
  fact: { citation_accuracy: number | null; effective_citations: number | null } | null;
  coverage: Record<Metric, number>; mean_seconds: number | null; median_seconds: number | null; p95_seconds: number | null;
};
export type Summary = Quality & { elapsed_seconds: number; timeout_rate: number; fallback_rate: number; retries: number; model: Totals; search_errors: number;
  node_mean_seconds: Record<string, number>; language: Record<string, Quality>; topic: Record<string, Quality>; failures: Record<string, number> };
export type Row = { task_id: number; prompt: string; language: string; topic: string;
  performance: { elapsed: number | null; fallback: unknown; report_characters: number | null; source_count: number | null; stage: string | null; tools: Record<string, number>; retrieval: Record<string, number>; rounds: number | null; };
  scores: Partial<Record<Metric, Record<string, number>>>; usage: Totals; errors: Partial<Record<Phase, string | null>>;
} & Record<Phase, string>;
export type Activity = { task_id: number; phase: Phase; status: string; stage: string | null; started: number; ended: number | null; error: string | null };
export type Run = { id: string; name: string; status: string; reason: string | null; created: number;
  config: { research: { model_name: string }; judge: Judge; budget: Budget; metrics: Metric[]; task_ids: number[]; dataset: Dataset; software: { commit: string | null; dirty: boolean | null } };
  summary: Summary; tasks?: Row[]; activity?: Activity[];
};
export type TaskDetail = { task: Row; report: { content: string; adapted: string; evidence: unknown[]; citation_diagnostics: unknown } | null;
  attempts: { phase: Metric; status: string; error: string | null; result?: { metrics?: Record<string, number>;
    details?: { status: string; fact: string; url: string }[] } }[];
  research_attempts: unknown[]; model_calls: unknown[] };
export type Comparison = { runs: Run[]; warnings: string[] };

export const phases: Phase[] = ["generate", "race", "fact"];
export const phaseNames: Record<Phase, string> = { generate: "研究", race: "RACE", fact: "FACT" };
export const states: Record<string, string> = {
  pending: "等待执行", generating: "生成报告", evaluating: "正在评分", pausing: "等待暂停", paused: "已暂停",
  completed: "已完成", partial: "部分完成", failed: "失败", interrupted: "已中断", cancelled: "已停止",
  running: "进行中", succeeded: "成功", disabled: "未启用", skipped: "已跳过",
};
export const active = (status: string) => ["pending", "generating", "evaluating", "pausing"].includes(status);
export const initialBudget: Budget = { max_tasks: 5, task_seconds: 1800, total_seconds: 7200, max_requests: 500, max_tokens: null, max_cost: null };
export const count = (value?: number | null) => value == null ? "—" : Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 }).format(value);
export const percent = (value?: number | null) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
export const seconds = (value?: number | null) => value == null ? "—" : `${count(Math.round(value))} 秒`;
export const date = (value: number) => new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
export const language = (value: string) => ({ zh: "中文", en: "英文" }[value] ?? value);
export const dimensions = [
  ["comprehensiveness", "全面性"], ["insight", "深度与洞察"],
  ["instruction_following", "指令遵循"], ["readability", "可读性"],
] as const;
export const errorNames: Record<string, string> = {
  timeout: "超过阶段时限", research_failed: "研究失败", worker_interrupted: "Worker 中断",
  fact_service_failed: "部分引用核验服务失败", judge_service_failed: "Judge 请求失败",
  reader_service_failed: "正文提取服务失败", race_response_invalid: "RACE 响应无效",
  fact_extraction_failed: "事实提取失败", scorer_missing_or_invalid: "评分器文件缺失或校验失败",
  max_requests: "已达模型请求预算", max_tokens: "已达已知 Token 预算", max_cost: "已达已知费用预算",
  total_seconds: "已达累计时间预算", selected_scores_finished: "选定评分结束，可继续剩余阶段", budget_reached: "已达预算",
};

export function feedback(error: unknown) {
  return error instanceof Error ? error.message : "请求失败";
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/benchmark${path}`, { cache: "no-store", ...options });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `请求失败（${response.status}）`);
  }
  return response.status === 204 ? undefined as T : await response.json();
}

export function post<T>(path: string, body?: unknown) {
  return request<T>(path, { method: "POST", ...(body == null ? {} : {
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }) });
}
