"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { active, count, date, dimensions, errorNames, language, percent, phaseNames, phases, seconds, states,
  type Comparison, type Installation, type Metric, type Quality, type Row, type Run, type TaskDetail, type Totals } from "./api";
import { BudgetFields, JudgeFields, MetricChecks } from "./forms";
import styles from "./benchmark.module.css";

const points = (values: number[]) => {
  const [top, right, bottom, left] = values.map(value => Math.min(1, Math.max(0, value)) * 50);
  return `50% ${50 - top}%, ${50 + right}% 50%, 50% ${50 + bottom}%, ${50 - left}% 50%`;
};

function Radar({ race }: { race: NonNullable<Quality["race"]> }) {
  return <div className={styles.radar}>
    <div className={styles.radarPlot} aria-hidden="true">
      {[1, 0.75, 0.5, 0.25].map(scale => <i key={scale} style={{ clipPath: `polygon(${points([scale, scale, scale, scale])})` }} />)}
      <div className={styles.radarShape} style={{ clipPath: `polygon(${points(dimensions.map(([key]) => race[key]))})` }} />
    </div>
    <dl>{dimensions.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{count(race[key])}</dd></div>)}</dl>
  </div>;
}

function UsageTable({ model }: { model: Totals }) {
  const fields = [["prompt_tokens", "输入 Token"], ["completion_tokens", "输出 Token"], ["total_tokens", "总 Token"],
    ["reasoning_tokens", "推理 Token"], ["cached_tokens", "缓存 Token"], ["cost", "供应商费用"]] as const;
  return <div className={styles.tableWrap}><table>
    <thead><tr><th>服务</th><th>请求</th><th>请求错误</th>{fields.map(([key, title]) => <th key={key}>{title}</th>)}</tr></thead>
    <tbody>{(["agent", "judge", "reader"] as const).map(role => <tr key={role}>
      <th scope="row">{{ agent: "待测模型", judge: "Judge", reader: "正文提取" }[role]}</th>
      <td>{count(model[role].requests)}</td><td>{count(model[role].errors)}</td>{fields.map(([key]) => <td key={key}>
        {count(model[role].usage[key])}{model[role].usage[key] != null && model[role].usage_coverage[key] < model[role].requests &&
          <small> · {model[role].usage_coverage[key]}/{model[role].requests} 次</small>}
      </td>)}
    </tr>)}</tbody>
  </table></div>;
}

function GroupTable({ rows, byLanguage = false }: { rows: Record<string, Quality>; byLanguage?: boolean }) {
  return <div className={styles.tableWrap}><table>
    <thead><tr><th>{byLanguage ? "语言" : "领域"}</th><th>样本</th><th>RACE</th><th>FACT 准确率</th><th>有效引用</th><th>平均研究耗时</th><th>成功率</th><th>RACE / FACT 覆盖</th></tr></thead>
    <tbody>{Object.entries(rows).map(([key, group]) => <tr key={key}>
      <th scope="row">{byLanguage ? language(key) : key}</th><td>{group.samples}</td>
      <td>{count(group.race?.overall_score)}{group.race && <meter min={0} max={1} value={group.race.overall_score} aria-label={`${key} RACE`} />}</td>
      <td>{percent(group.fact?.citation_accuracy)}</td><td>{count(group.fact?.effective_citations)}</td><td>{seconds(group.mean_seconds)}</td>
      <td>{percent(group.success_rate)}</td><td>{percent(group.coverage.race)} / {percent(group.coverage.fact)}</td>
    </tr>)}</tbody>
  </table></div>;
}

const performanceMeasures = {
  elapsed: { label: "研究耗时", get: (task: Row) => task.performance.elapsed, render: seconds },
  tokens: { label: "Agent 总 Token", get: (task: Row) => task.usage.agent.usage.total_tokens, render: count },
  length: { label: "报告字符数", get: (task: Row) => task.performance.report_characters, render: count },
  search: { label: "网页与文献检索次数", get: (task: Row) => (task.performance.retrieval.web_search ?? 0) + (task.performance.retrieval.openalex ?? 0), render: count },
};

function TaskPerformance({ tasks }: { tasks: Row[] }) {
  const [key, select] = useState<keyof typeof performanceMeasures>("elapsed");
  const { get, label, render } = performanceMeasures[key];
  const values = tasks.filter(task => key !== "search" || task.generate === "succeeded")
    .flatMap(task => { const value = get(task); return value == null ? [] : [{ id: task.task_id, value }]; });
  const upper = Math.max(1, ...values.map(row => row.value));
  return <>
    <label>单题分布<select value={key} onChange={e => select(e.target.value as keyof typeof performanceMeasures)}>
      {Object.entries(performanceMeasures).map(([id, item]) => <option key={id} value={id}>{item.label}</option>)}
    </select></label>
    {values.length ? <div className={styles.events}>{values.map(row => <div className={styles.barRow} key={row.id}>
      <span>#{row.id}</span><meter min={0} max={upper} value={row.value} aria-label={`#${row.id} ${label}`} /><span>{render(row.value)}</span>
    </div>)}</div> : <p className={styles.muted}>暂时没有可绘制的数据。</p>}
  </>;
}

function Performance({ run }: { run: Run }) {
  const summary = run.summary;
  const maximum = Math.max(1, ...Object.values(summary.node_mean_seconds ?? {}));
  const labels = [["失败率", percent(summary.failure_rate)], ["研究超时率", percent(summary.timeout_rate)],
    ["报告降级率", percent(summary.fallback_rate)], ["重试", count(summary.retries)], ["检索与读取错误", count(summary.search_errors)],
    ["中位研究耗时", seconds(summary.median_seconds)], ["P95 研究耗时", seconds(summary.p95_seconds)]];
  return <details className={styles.inset}><summary>稳定性与资源</summary>
    <div className={styles.metricGrid}>{labels.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
    <UsageTable model={summary.model} />
    <TaskPerformance tasks={run.tasks ?? []} />
    <h3>Agent 节点平均耗时</h3>
    {!Object.keys(summary.node_mean_seconds ?? {}).length && <p className={styles.muted}>暂无节点耗时。</p>}
    {Object.entries(summary.node_mean_seconds ?? {}).map(([key, value]) => <div className={styles.barRow} key={key}>
      <span>{key}</span><meter min={0} max={maximum} value={value} aria-label={key} /><span>{seconds(value)}</span>
    </div>)}
    {Object.keys(summary.failures ?? {}).length > 0 && <><h3>失败原因</h3><ul>{Object.entries(summary.failures).map(([key, value]) =>
      <li key={key}>{errorNames[key] ?? key} · {count(value)}</li>)}</ul></>}
  </details>;
}

type SendAction = (action: string, opts?: { task_id?: number; metrics?: Metric[]; judge?: Run["config"]["judge"]; budget?: Run["config"]["budget"]; authorize?: boolean }) => Promise<boolean>;

export function RunPanel({ run, setting, busy, action, detail, remove }: {
  run: Run; setting: Installation; busy: boolean; action: SendAction; detail: (id: number) => void; remove: () => void;
}) {
  const [consent, setConsent] = useState(false);
  const [metrics, setMetrics] = useState<Metric[]>(["race", "fact"]);
  const [retry, setRetry] = useState("all");
  const { official_defaults: _, ...profile } = run.config.judge;
  const [judge, setJudge] = useState(profile);
  const [budget, setBudget] = useState(run.config.budget);
  const [changeBudget, setChangeBudget] = useState(false);
  const [format, setFormat] = useState("zip");
  const [retryTask, setRetryTask] = useState("");
  const [removeConfirm, setRemoveConfirm] = useState(false);
  const summary = run.summary;
  const tasks = run.tasks ?? [];
  const finished = summary.success + summary.failed + summary.skipped;
  const running = run.activity?.find(row => row.status === "running");
  const isActive = active(run.status);
  const done = ["completed", "partial", "failed", "cancelled"].includes(run.status);
  const canResume = ["paused", "interrupted"].includes(run.status);
  const failedTasks = tasks.filter(task => phases.some(phase => task[phase] === "failed"));
  const hasErrors = ["partial", "failed"].includes(run.status) && failedTasks.length > 0;
  const canScore = !isActive && !running && summary.success > 0 && setting.scorer_ready;
  const labels = [
    ["RACE Overall", count(summary.race?.overall_score)], ["FACT 引用准确率", percent(summary.fact?.citation_accuracy)],
    ["有效引用数", count(summary.fact?.effective_citations)], ["任务成功率", percent(summary.success_rate)],
    ["平均研究耗时", seconds(summary.mean_seconds)],
  ];

  async function send(name: string, options?: Parameters<SendAction>[1]) {
    if (await action(name, { ...options, authorize: consent, ...(changeBudget && ["evaluate", "retry", "resume"].includes(name) ? { budget } : {}) })) {
      setConsent(false);
      if (name === "evaluate") setJudge(current => ({ ...current, api_key: undefined, reader_key: undefined }));
    }
  }

  return <section className={styles.card} aria-label="批次详情">
    <div className={styles.sectionHeader}>
      <div><h2>{run.name}</h2><p>{run.config.research.model_name} · {run.config.dataset.version} · {date(run.created)}</p></div>
      <span className={styles.status} data-status={run.status}>{states[run.status] ?? run.status}</span>
    </div>
    <div className={styles.progress}>
      <label htmlFor="benchmark-progress"><strong>研究进度</strong><span>{finished} / {summary.samples}</span></label>
      <progress id="benchmark-progress" value={finished} max={summary.samples} />
      <div className={styles.meta}><span>成功 {summary.success}</span><span>失败 {summary.failed}</span><span>跳过 {summary.skipped}</span>
        <span>剩余 {summary.samples - finished}</span><span>累计 {seconds(summary.elapsed_seconds)}</span></div>
      {running && <p role="status">当前任务 #{running.task_id} · {phaseNames[running.phase]} {running.stage && `/ ${running.stage}`}</p>}
      {run.reason && <p role="status">{errorNames[run.reason] ?? run.reason}</p>}
    </div>
    <div className={styles.row}>
      {isActive && <button type="button" disabled={busy || run.status === "pausing"} onClick={() => void send("pause")}>暂停</button>}
      {!done && <button type="button" disabled={busy} onClick={() => void send("stop")}>停止派发</button>}
      <label>导出格式<select value={format} onChange={e => setFormat(e.target.value)}>
        {["zip", "jsonl", "json", "csv", "md"].map(item => <option key={item} value={item}>{item.toUpperCase()}</option>)}
      </select></label>
      <a className={styles.button} href={`/api/benchmark/runs/${run.id}/export?format=${format}`}>导出结果</a>
    </div>
    {(!isActive && !running && (canResume || hasErrors || canScore)) && <details className={styles.inset} open={canResume || undefined}>
      <summary>继续、评分与重试</summary>
      <label className={styles.check}><input type="checkbox" checked={changeBudget} onChange={e => setChangeBudget(e.target.checked)} />调整累计预算</label>
      {changeBudget && <BudgetFields value={budget} change={setBudget} />}
      <label className={styles.check}><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />授权按预算继续调用外部服务</label>
      <div className={styles.row}>
        {canResume && <button type="button" disabled={!consent || busy} onClick={() => void send("resume")}>继续批次</button>}
        {hasErrors && <>
          <label>重试范围<select value={retry} onChange={e => setRetry(e.target.value)}>
            <option value="all">全部失败阶段</option><option value="race">失败的 RACE</option><option value="fact">失败的 FACT</option>
          </select></label>
          <label>重试题目<select value={retryTask} onChange={e => setRetryTask(e.target.value)}>
            <option value="">全部失败题目</option>{failedTasks.map(task => <option key={task.task_id} value={task.task_id}>#{task.task_id}</option>)}
          </select></label>
          <button type="button" disabled={!consent || busy} onClick={() => void send("retry", {
            metrics: retry === "all" ? [] : [retry as Metric], task_id: retryTask ? Number(retryTask) : undefined,
          })}>重试失败阶段</button>
        </>}
      </div>
      {canScore && <details className={styles.inset}><summary>仅对已有报告评分</summary>
        <MetricChecks metrics={metrics} change={setMetrics} />
        <JudgeFields value={judge} change={setJudge} setting={setting} />
        <button type="button" disabled={!consent || busy || !metrics.length} onClick={() => void send("evaluate", { metrics, judge })}>开始评分</button>
      </details>}
    </details>}
    <div className={styles.metricGrid}>{labels.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
    <div className={styles.meta}><span>RACE 覆盖 {percent(summary.coverage.race)}</span><span>FACT 覆盖 {percent(summary.coverage.fact)}</span>
      <span>{run.config.judge.official_defaults ? "评分器默认模型与服务" : "自定义 Judge 配置"}</span></div>
    {summary.race && <Radar race={summary.race} />}
    <details className={styles.inset}><summary>语言与研究领域</summary>
      <GroupTable rows={summary.language ?? {}} byLanguage />
      <GroupTable rows={summary.topic ?? {}} />
    </details>
    <Performance run={run} />
    <JsonDetail title="配置与版本快照" value={run.config} />
    <details className={styles.inset}><summary>运行日志</summary>
      {run.activity?.length ? <ul className={styles.events}>{run.activity.map((row, i) => <li key={i}>
        {date(row.started)} · #{row.task_id} · {phaseNames[row.phase]} · {states[row.status]}
        {row.error && ` · ${errorNames[row.error] ?? row.error}`}
      </li>)}</ul> : <p className={styles.muted}>等待 worker 领取。</p>}
    </details>
    <details className={styles.inset} open><summary>任务详情</summary>
      <div className={styles.tableWrap}><table>
        <thead><tr><th>ID</th><th>语言与领域</th><th>研究</th><th>RACE</th><th>FACT</th><th>耗时</th><th>Agent Token</th><th>网页检索 / 文献 / 正文读取</th><th>工具</th><th>报告字符</th><th>操作</th></tr></thead>
        <tbody>{tasks.map(task => <tr key={task.task_id}>
          <th scope="row">#{task.task_id}</th><td>{language(task.language)} · {task.topic}</td>
          {phases.map(phase => <td key={phase} title={errorNames[task.errors[phase] ?? ""] ?? task.errors[phase] ?? undefined}>{states[task[phase]]}{phase === "generate" && !!task.performance.fallback ? "（降级）" : ""}</td>)}
          <td>{seconds(task.performance.elapsed)}</td><td>{count(task.usage.agent.usage.total_tokens)}</td>
          <td>{["web_search", "openalex", "web_page"].map(key => task.performance.retrieval[key] ?? 0).join(" / ")}</td>
          <td>{count(Object.values(task.performance.tools).reduce((a, b) => a + b, 0))}</td><td>{count(task.performance.report_characters)}</td>
          <td className={styles.row}><button type="button" onClick={() => detail(task.task_id)}>详情</button>
            {!done && !phases.some(phase => task[phase] === "running") && phases.some(phase => task[phase] === "pending") &&
              <button type="button" disabled={busy} onClick={() => void send("skip", { task_id: task.task_id })}>跳过</button>}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
    {done && !running && <details className={styles.inset}><summary>删除批次</summary>
      <label className={styles.check}><input type="checkbox" checked={removeConfirm} onChange={e => setRemoveConfirm(e.target.checked)} />确认删除此批次及其报告、证据和所有评分</label>
      <button type="button" disabled={!removeConfirm || busy} onClick={remove}>删除批次</button>
    </details>}
  </section>;
}

function JsonDetail({ title, value }: { title: string; value: unknown }) {
  return <details className={styles.inset}><summary>{title}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}

const factLabels: Record<string, string> = {
  supported: "支持", unsupported: "不支持", unknown: "无法判定", inaccessible: "来源无法访问",
  invalid_url: "引用格式异常", unmatched_citation: "未匹配报告中的引用", judge_validation_failed: "Judge 核验失败",
  reader_service_failed: "正文提取服务失败",
};

function TaskScores({ attempts }: { attempts: TaskDetail["attempts"] }) {
  const latestRace = [...attempts].reverse().find(attempt => attempt.phase === "race" && attempt.status === "succeeded");
  const latestFact = [...attempts].reverse().find(attempt => attempt.phase === "fact" && attempt.result?.details);
  return <>
    {latestRace?.result?.metrics && <Radar race={latestRace.result.metrics} />}
    {latestFact?.result?.details && <details className={styles.inset}><summary>FACT 引用核验</summary>
      {latestFact.result.details.length ? <ul className={styles.events}>
        {latestFact.result.details.map((item, i) => <li key={i}><strong>{factLabels[item.status] ?? item.status}</strong> · {item.fact}<br />
          <small>{item.url}</small></li>)}
      </ul> : <p className={styles.muted}>本次评测没有提取到可核验的引用配对。</p>}
    </details>}
  </>;
}

export function TaskPanel({ data, id, close }: { data: TaskDetail | null; id: number; close: () => void }) {
  return <section className={styles.card} aria-label="单项任务详情">
    <div className={styles.sectionHeader}><h2>题目 #{id}</h2><button onClick={close} type="button">关闭详情</button></div>
    {!data ? <p role="status">正在读取任务详情…</p> : <>
      <p className={styles.prompt}>{data.task.prompt}</p>
      {data.report ? <>
        <div className={styles.markdown}><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
          img: () => null, a: ({ children, href }) => href?.startsWith("http") ? <a href={href} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>,
        }}>{data.report.content}</ReactMarkdown></div>
        <details className={styles.inset}><summary>DRB 适配后正文</summary><pre>{data.report.adapted}</pre></details>
        <JsonDetail title="引用证据" value={data.report.evidence} />
        <JsonDetail title="引用格式诊断" value={data.report.citation_diagnostics} />
      </> : <p className={styles.muted}>此任务暂无正式报告。</p>}
      <TaskScores attempts={data.attempts} />
      <JsonDetail title="研究活动与失败记录" value={data.research_attempts} />
      <JsonDetail title="RACE 与 FACT 核验详情" value={data.attempts} />
      <JsonDetail title="模型与正文提取请求" value={data.model_calls} />
    </>}
  </section>;
}

export function ComparisonPanel({ result }: { result: Comparison }) {
  const metrics: [string, (run: Run) => number | null | undefined][] = [
    ["RACE Overall", run => run.summary.race?.overall_score],
    ...dimensions.map(([key, label]): [string, (run: Run) => number | undefined] => [label, run => run.summary.race?.[key]]),
    ["FACT 引用准确率", run => run.summary.fact?.citation_accuracy],
    ["有效引用数", run => run.summary.fact?.effective_citations],
    ["平均研究耗时（秒）", run => run.summary.mean_seconds],
    ["Agent 总 Token", run => run.summary.model.agent.usage.total_tokens],
    ["成功率", run => run.summary.success_rate],
    ["样本数", run => run.summary.samples], ["RACE 覆盖率", run => run.summary.coverage.race], ["FACT 覆盖率", run => run.summary.coverage.fact],
  ];
  return <section className={styles.card}><h2>批次对比</h2>
    {result.warnings.length > 0 && <p className={styles.warning} role="status">可比性提示 · {result.warnings.join("；")}</p>}
    <div className={styles.tableWrap}><table>
      <thead><tr><th>指标</th>{result.runs.map(run => <th key={run.id}>{run.name}<small> · {date(run.created)}</small></th>)}
        {result.runs.length === 2 && !result.warnings.length && <th>后一批减前一批</th>}</tr></thead>
      <tbody>{metrics.map(([label, get]) => {
        const values = result.runs.map(get);
        return <tr key={label}><th scope="row">{label}</th>{values.map((value, i) => <td key={result.runs[i].id}>{count(value)}</td>)}
          {values.length === 2 && !result.warnings.length && <td>{values[0] != null && values[1] != null ? count(values[1] - values[0]) : "—"}</td>}</tr>;
      })}</tbody>
    </table></div>
  </section>;
}
