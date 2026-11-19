"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { active, count, date, feedback, post, request, states, type Comparison, type Installation, type Query, type Run, type TaskDetail } from "./api";
import { NewRun, Questions } from "./forms";
import { ComparisonPanel, RunPanel, TaskPanel } from "./results";
import styles from "./benchmark.module.css";

export default function BenchmarkPage() {
  const [setting, setSetting] = useState<Installation | null>(null);
  const [queries, setQueries] = useState<Query[]>([]);
  const [history, setHistory] = useState<Run[]>([]);
  const [runId, setRunId] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [taskId, setTaskId] = useState<number | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [revision, refresh] = useState(0);
  const file = useRef<HTMLInputElement>(null);
  const mutating = useRef(false);

  useEffect(() => {
    const abort = new AbortController();
    const { signal } = abort;
    async function load() {
      try {
        const [info, runs] = await Promise.all([
          request<Installation>("", { signal }), request<Run[]>("/runs", { signal }),
        ]);
        if (signal.aborted) return;
        setSetting(info);
        setHistory(runs);
        setRunId(current => current || runs.find(saved => active(saved.status))?.id || runs[0]?.id || "");
        if (info.dataset.count) {
          const questions = await request<Query[]>("/dataset/tasks", { signal });
          if (!signal.aborted) setQueries(questions);
        } else {
          setQueries([]);
        }
      } catch (err) {
        if (!signal.aborted) setError(feedback(err));
      }
    }
    void load();
    return () => abort.abort();
  }, [revision]);

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const current = await request<Run>(`/runs/${runId}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setRun(current);
        setHistory(runs => runs.map(saved => saved.id === current.id ? current : saved));
        if (active(current.status) || current.activity?.some(event => event.status === "running")) timer = setTimeout(poll, 1800);
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(feedback(err));
          timer = setTimeout(poll, 4000);
        }
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [runId, revision]);

  useEffect(() => {
    if (!runId || taskId == null) return;
    const controller = new AbortController();
    request<TaskDetail>(`/runs/${runId}/tasks/${taskId}`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setDetail(data); })
      .catch(err => { if (!controller.signal.aborted) setError(feedback(err)); });
    return () => controller.abort();
  }, [runId, taskId, revision]);

  function open(id: string) {
    setRunId(id);
    setRun(current => current?.id === id ? current : null);
    setTaskId(null);
    setDetail(null);
  }

  async function mutate(label: string, op: () => Promise<void>) {
    if (mutating.current) return false;
    mutating.current = true;
    setBusy(label);
    setError("");
    try {
      await op();
      refresh(value => value + 1);
      return true;
    } catch (err) {
      setError(feedback(err));
      return false;
    } finally {
      mutating.current = false;
      setBusy("");
    }
  }

  return <main className={styles.shell}>
    <header className={styles.top}><Link href="/workspace">← 研究工作台</Link><span>Tufa Scholar</span></header>
    <div className={styles.content}>
      <div className={styles.hero}><h1>Benchmark 评测</h1><nav aria-label="工作台导航">
        <a href="#new-run">配置评测</a><a href="#history">历史记录</a>{runId && <a href="#run">批次详情</a>}
      </nav></div>
      {error && <div className={styles.warning} role="alert">{error} <button type="button" onClick={() => { setError(""); refresh(value => value + 1); }}>刷新</button></div>}
      {busy && <p className={styles.notice} role="status">{busy}…</p>}
      <div className={styles.columns}>
        <section className={styles.card} aria-label="配置评测" id="new-run">
          <div className={styles.sectionHeader}><h2>配置评测</h2></div>
          {!setting ? <p role="status">正在读取本机配置…</p> : <>
            <div className={styles.dataset}>
              <div className={styles.sectionHeader}>
                <div><h3>{setting.dataset.name}</h3><p>{setting.dataset.version} · {setting.dataset.commit.slice(0, 12)}</p></div>
                <span className={styles.status} data-status={setting.dataset.count ? "completed" : "pending"}>{setting.dataset.count ? "数据集已就绪" : "待下载"}</span>
              </div>
              {setting.dataset.count && <p className={styles.meta}><span>{setting.dataset.count} 道题</span><span>中文 {setting.dataset.languages?.zh ?? "—"}</span>
                <span>英文 {setting.dataset.languages?.en ?? "—"}</span><span>{Object.keys(setting.dataset.domains ?? {}).length} 个领域</span></p>}
              <div className={styles.row}>
                <button type="button" disabled={!!busy} onClick={() => void mutate("下载官方题库", async () => { await post("/dataset/download"); })}>下载数据集</button>
                <button type="button" disabled={!!busy} onClick={() => file.current?.click()}>导入 JSONL</button>
                <input hidden type="file" ref={file} accept=".jsonl" onChange={event => {
                  const input = event.currentTarget;
                  const item = input.files?.[0];
                  if (!item) return;
                  const body = new FormData();
                  body.append("file", item);
                  input.value = "";
                  void mutate("导入题库", async () => { await request("/dataset/import", { method: "POST", body }); });
                }} />
                {setting.scorer_ready ? <span className={styles.muted}>官方评分器已安装</span> :
                  <button type="button" disabled={!!busy} onClick={() => void mutate("安装官方评分器", async () => { await post("/scorer/install"); })}>安装官方评分器</button>}
              </div>
              {setting.dataset.count && <Questions rows={queries} tasks={run?.tasks ?? []} />}
            </div>
            {setting.dataset.count != null && <NewRun setting={setting} queries={queries} busy={!!busy}
              submit={payload => mutate("创建批次", async () => { const item = await post<Run>("/runs", payload); open(item.id); })} />}
          </>}
        </section>
        <section className={styles.card} aria-label="历史记录" id="history">
          <div className={styles.sectionHeader}><h2>历史记录</h2><button type="button" disabled={!!busy || selected.length < 2}
            onClick={() => void mutate("读取对比结果", async () => { setComparison(await post<Comparison>("/compare", { ids: selected })); })}>对比所选</button></div>
          {!history.length && <p className={styles.muted}>{setting ? "暂无评测批次。" : "正在读取历史记录…"}</p>}
          <ul className={styles.history}>
            {history.map(item => <li key={item.id} data-selected={runId === item.id}>
              <input type="checkbox" checked={selected.includes(item.id)} aria-label={`选择对比 ${item.name} ${date(item.created)}`}
                disabled={!selected.includes(item.id) && selected.length >= 10}
                onChange={e => setSelected(e.target.checked ? [...selected, item.id] : selected.filter(id => id !== item.id))} />
              <button type="button" onClick={() => open(item.id)}>
                <strong>{item.name}</strong><small>{date(item.created)} · {item.config.research.model_name}</small>
                <small>{item.summary.samples} 道题 · RACE {count(item.summary.race?.overall_score)} · FACT {count(item.summary.fact?.citation_accuracy)}</small>
              </button>
              <span className={styles.status} data-status={item.status}>{states[item.status] ?? item.status}</span>
            </li>)}
          </ul>
        </section>
      </div>
      {comparison && <ComparisonPanel result={comparison} />}
      {runId && setting && <div id="run">
        {run ? <RunPanel key={runId} run={run} setting={setting} busy={!!busy}
          action={(action, opts) => mutate("更新批次", async () => { await post(`/runs/${runId}/actions`, { action, ...opts }); })}
          detail={id => { if (id !== taskId) { setTaskId(id); setDetail(null); } }}
          remove={() => void mutate("删除批次", async () => {
            await request(`/runs/${runId}`, { method: "DELETE" });
            setSelected(items => items.filter(id => id !== runId));
            setComparison(null);
            open("");
          })} /> : <p role="status">正在读取批次…</p>}
      </div>}
      {taskId != null && <TaskPanel id={taskId} data={detail} close={() => { setTaskId(null); setDetail(null); }} />}
    </div>
  </main>;
}
