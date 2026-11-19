"use client";

import type { FormEvent } from "react";
import { useState } from "react";
import { count, initialBudget, language, type Budget, type Installation, type Judge, type Metric, type Query, type Row } from "./api";
import styles from "./benchmark.module.css";

const budgetFields: [keyof Budget, string, number, number][] = [
  ["max_tasks", "最多任务", 1, 100], ["task_seconds", "单阶段时限（秒）", 1, 14400],
  ["total_seconds", "累计时间（秒）", 1, 604800], ["max_requests", "最多模型请求", 1, 100000],
  ["max_tokens", "已知 Token 上限", 1, 1e10], ["max_cost", "已知费用上限", 0.0001, 100000],
];

export function BudgetFields({ value, change }: { value: Budget; change: (value: Budget) => void }) {
  return <div className={styles.fields}>{budgetFields.map(([key, label, min, max]) =>
    <label key={key}>{label}<input type="number" min={min} max={max} step={key === "max_cost" ? "any" : 1}
      required={initialBudget[key] != null} placeholder="可选" value={value[key] ?? ""}
      onChange={e => change({ ...value, [key]: e.target.value ? Number(e.target.value) : null })} /></label>
  )}</div>;
}

export function JudgeFields({ value, change, setting }: { value: Judge; change: (value: Judge) => void; setting: Installation }) {
  const fields: [keyof Judge, string, string, string?][] = [
    ["base_url", "Judge API Base URL", "url"], ["api_key", "Judge API Key", "password", setting.judge_key_configured ? "默认地址可使用服务端密钥" : "填写独立密钥"],
    ["race_model", "RACE Judge", "text"], ["clean_model", "报告清洗模型", "text"],
    ["fact_model", "FACT Judge", "text"], ["reader_url", "正文提取服务", "url"],
    ["reader_key", "正文提取 API Key", "password", setting.reader_key_configured ? "默认地址可使用服务端密钥" : "FACT 需要此密钥"],
    ["timeout", "请求超时（秒）", "number"], ["retries", "网络重试次数", "number"],
  ];
  return <div className={styles.fields}>{fields.map(([key, label, type, placeholder]) =>
    <label key={key}>{label}<input type={type} autoComplete={type === "password" ? "new-password" : undefined}
      required={type !== "password"} min={key === "retries" ? 0 : 1} max={key === "retries" ? 5 : 600}
      placeholder={placeholder} value={String(value[key] ?? "")} onChange={e => change({ ...value, [key]: type === "number" ? Number(e.target.value) : e.target.value })} /></label>
  )}</div>;
}

export function MetricChecks({ metrics, change }: { metrics: Metric[]; change: (metrics: Metric[]) => void }) {
  return <div className={styles.row}>
    {([["race", "RACE 报告质量"], ["fact", "FACT 引用核验"]] as const).map(([value, label]) =>
      <label className={styles.check} key={value}><input type="checkbox" checked={metrics.includes(value)}
        onChange={e => change(e.target.checked ? [...metrics, value] : metrics.filter(item => item !== value))} />{label}</label>)}
  </div>;
}

export function Questions({ rows, tasks }: { rows: Query[]; tasks: Row[] }) {
  const [filter, setFilter] = useState({ id: "", language: "", topic: "", status: "" });
  const latest = new Map(tasks.map(task => [task.task_id, task.generate]));
  const visible = rows.filter(q => (!filter.id || String(q.id).includes(filter.id)) &&
    (!filter.language || q.language === filter.language) && (!filter.topic || q.topic === filter.topic) &&
    (!filter.status || (latest.get(q.id) ?? "unselected") === filter.status));
  const labels: Record<string, string> = { unselected: "未选入批次", pending: "等待", running: "进行中", succeeded: "成功", skipped: "已跳过", failed: "失败" };
  return <details className={styles.inset}>
    <summary>预览题库</summary>
    <div className={styles.fields}>
      <label>任务 ID<input value={filter.id} onChange={e => setFilter({ ...filter, id: e.target.value })} /></label>
      <label>语言<select value={filter.language} onChange={e => setFilter({ ...filter, language: e.target.value })}>
        <option value="">全部</option><option value="zh">中文</option><option value="en">英文</option>
      </select></label>
      <label>领域<select value={filter.topic} onChange={e => setFilter({ ...filter, topic: e.target.value })}>
        <option value="">全部</option>{[...new Set(rows.map(row => row.topic))].sort().map(topic => <option key={topic}>{topic}</option>)}
      </select></label>
      <label>当前批次的研究状态<select value={filter.status} onChange={e => setFilter({ ...filter, status: e.target.value })}>
        <option value="">全部</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
    </div>
    <div className={styles.questionList}>
      {visible.map(q => <details key={q.id}><summary>#{q.id} · {language(q.language)} · {q.topic} · {labels[latest.get(q.id) ?? "unselected"]}</summary><p>{q.prompt}</p></details>)}
      {!visible.length && <p>没有符合条件的题目。</p>}
    </div>
  </details>;
}

export function NewRun({ setting, queries, busy, submit }: { setting: Installation; queries: Query[]; busy: boolean; submit: (run: object) => Promise<boolean> }) {
  const [name, setName] = useState("DeepResearch Bench");
  const [scope, setScope] = useState("1");
  const [filter, setFilter] = useState({ language: "", topic: "", ids: "", seed: 42 });
  const [research, setResearch] = useState({ model_name: "", search_engine: "", research_mode: "deep", research_depth: "standard",
    source: "auto", network_search: true, prefer_primary_evidence: true, relevance_filter: "standard" });
  const [budget, setBudget] = useState(initialBudget);
  const [judge, setJudge] = useState(setting.judge);
  const [metrics, setMetrics] = useState<Metric[]>(["race", "fact"]);
  const [consent, setConsent] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);
  const ids = scope === "ids" ? [...new Set(filter.ids.split(/[,，\s]+/).filter(Boolean).map(Number))] : [];
  const eligible = queries.filter(q => (!filter.language || q.language === filter.language) && (!filter.topic || q.topic === filter.topic));
  const sample = scope === "all" || scope === "ids" ? null : Number(scope);
  const selected = scope === "ids" ? ids.length : sample ?? eligible.length;
  const valid = selected > 0 && selected <= budget.max_tasks && selected <= eligible.length &&
    ids.every(id => eligible.some(q => q.id === id));

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const scoring = (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value") === "full";
    if (!consent || !valid || (scoring && (!setting.scorer_ready || !metrics.length))) return;
    const id = requestId ?? crypto.randomUUID();
    setRequestId(id);
    const created = await submit({
      request_id: id, dataset_id: setting.dataset.id, name, language: filter.language || null, topic: filter.topic || null, ids, sample, seed: filter.seed,
      ...research, model_name: research.model_name || null, search_engine: research.search_engine || null,
      metrics: scoring ? metrics : [], judge, budget, authorize: true,
    });
    if (created) {
      setConsent(false);
      setRequestId(null);
      setJudge(current => ({ ...current, api_key: undefined, reader_key: undefined }));
    }
  }

  return <form className={styles.stack} onSubmit={start}>
    <div className={styles.fields}>
      <label>批次名称<input value={name} maxLength={120} required onChange={e => setName(e.target.value)} /></label>
      <label>待测模型<input placeholder={setting.model.model || "在服务与模型中配置"} value={research.model_name}
        onChange={e => setResearch({ ...research, model_name: e.target.value })} /></label>
      <label>研究模式<select value={research.research_mode} onChange={e => setResearch({ ...research, research_mode: e.target.value })}>
        <option value="deep">深度研究</option><option value="auto">自动</option>
      </select></label>
      <label>研究深度<select value={research.research_depth} onChange={e => setResearch({ ...research, research_depth: e.target.value })}>
        <option value="light">轻量</option><option value="standard">标准</option><option value="deep">深入</option>
      </select></label>
      <label>搜索引擎<select value={research.search_engine} onChange={e => setResearch({ ...research, search_engine: e.target.value })}>
        <option value="">当前配置 · {setting.search.providers.find(p => p.id === setting.search.engine)?.name}</option>
        {setting.search.providers.map(p => <option value={p.id} key={p.id}>{p.name}</option>)}
      </select></label>
      <label>研究来源<select value={research.source} onChange={e => setResearch({ ...research, source: e.target.value })}>
        <option value="auto">自动</option><option value="web">网页</option><option value="openalex">OpenAlex</option><option value="all">全部</option><option value="local">空白本地项目</option>
      </select></label>
      <label>测试规模<select value={scope} onChange={e => setScope(e.target.value)}>
        <option value="1">连通性 · 1 道</option><option value="5">快速 · 5 道</option><option value="10">小规模 · 10 道</option>
        <option value="20">中规模 · 20 道</option><option value="all">全部匹配的题目</option><option value="ids">指定任务 ID</option>
      </select></label>
      <label>语言<select value={filter.language} onChange={e => setFilter({ ...filter, language: e.target.value })}>
        <option value="">全部</option><option value="zh">中文</option><option value="en">英文</option>
      </select></label>
      <label>研究领域<select value={filter.topic} onChange={e => setFilter({ ...filter, topic: e.target.value })}>
        <option value="">全部</option>{Object.keys(setting.dataset.domains ?? {}).sort().map(topic => <option key={topic}>{topic}</option>)}
      </select></label>
      {scope === "ids" ? <label>任务 ID（逗号分隔）<input required value={filter.ids} onChange={e => setFilter({ ...filter, ids: e.target.value })} /></label> :
        sample != null && <label>随机种子<input type="number" value={filter.seed} onChange={e => setFilter({ ...filter, seed: Number(e.target.value) })} /></label>}
    </div>
    <details className={styles.inset}><summary>研究偏好与预算</summary>
      <div className={styles.row}>
        <label className={styles.check}><input type="checkbox" checked={research.network_search}
          onChange={e => setResearch({ ...research, network_search: e.target.checked })} />网络搜索</label>
        <label className={styles.check}><input type="checkbox" checked={research.prefer_primary_evidence}
          onChange={e => setResearch({ ...research, prefer_primary_evidence: e.target.checked })} />优先原始证据</label>
        <label>相关性筛选<select value={research.relevance_filter} onChange={e => setResearch({ ...research, relevance_filter: e.target.value })}>
          <option value="relaxed">宽松</option><option value="standard">标准</option><option value="strict">严格</option>
        </select></label>
      </div>
      <BudgetFields value={budget} change={setBudget} />
      <p className={styles.muted}>Token 和费用预算按已返回的用量结算。单次调用可能超过剩余额度。</p>
    </details>
    <details className={styles.inset}><summary>独立 Judge 和正文提取服务</summary>
      <MetricChecks metrics={metrics} change={setMetrics} />
      <JudgeFields value={judge} change={setJudge} setting={setting} />
      <p className={styles.muted}>自定义 Judge 或正文提取服务会在历史结果中单独标记。评分只在评分器安装后可用。</p>
    </details>
    <p className={styles.selection}>本次选择 <strong>{count(selected)}</strong> 道题，任务上限 {count(budget.max_tasks)} 道。
      {selected > budget.max_tasks ? "请提高任务上限或缩小范围。" : selected > eligible.length || !valid ? "请检查任务 ID 与筛选条件。" : selected > 5 ? "首次建议运行 1～5 道题。" : ""}</p>
    <label className={styles.check}><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />授权按预算调用配置的外部服务，调用可能产生费用</label>
    <div className={styles.row}>
      <button disabled={!consent || !valid || busy} type="submit" value="reports">仅生成报告</button>
      <button disabled={!consent || !valid || busy || !setting.scorer_ready || !metrics.length} type="submit" value="full" className={styles.primary}>开始完整评测</button>
    </div>
  </form>;
}
