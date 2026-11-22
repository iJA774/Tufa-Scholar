"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api, messageOf } from "./api";
import { nodeLabels, toolLabels } from "./sourceLabels";
import styles from "./ResearchPanel.module.css";

type DetailEntry = {
  id: number;
  node: string;
  node_id: string;
  call_id: string;
  kind: "node" | "model" | "tool";
  direction: "input" | "output" | "error";
  name: string;
  attempt: number | null;
  phase: string | null;
};

type DetailPage = { entries: DetailEntry[]; status: string; has_more: boolean };
type DetailData = { value: unknown; truncated: boolean };
type Call = { key: string; entries: DetailEntry[] };
type NodeExecution = { key: string; node: string; calls: Map<string, Call> };

const activeStatuses = new Set(["pending", "queued", "running"]);
const directionLabels = { input: "输入", output: "输出", error: "失败信息" };
const phaseLabels: Record<string, string> = { query: "生成检索式", select: "筛选候选", act: "决定下一步", draft: "撰写初稿" };

function groupEntries(entries: DetailEntry[]) {
  const nodes = new Map<string, NodeExecution>();
  for (const entry of entries) {
    let node = nodes.get(entry.node_id);
    if (!node) {
      node = { key: entry.node_id, node: entry.node, calls: new Map() };
      nodes.set(entry.node_id, node);
    }
    const key = entry.attempt === null ? entry.call_id : entry.call_id + ":" + entry.attempt;
    let call = node.calls.get(key);
    if (!call) {
      call = { key, entries: [] };
      node.calls.set(key, call);
    }
    call.entries.push(entry);
  }
  return [...nodes.values()];
}

function formatContent(value: unknown): string {
  if (typeof value === "string") {
    if (/^\s*[\[{]/.test(value)) {
      try { return JSON.stringify(JSON.parse(value), null, 2); } catch { /* Text may contain JSON examples. */ }
    }
    return value;
  }
  return JSON.stringify(value, null, 2) ?? "";
}

function ContentBlock({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined) return null;
  return <div className={styles.detailBlock}>
    <h4>{label}</h4>
    <pre tabIndex={0} aria-label={label}>{formatContent(value)}</pre>
  </div>;
}

function DetailContent({ entry, data }: { entry: DetailEntry; data: DetailData }) {
  const value = data.value;
  if (data.truncated) return <>
    <p className={styles.detailNote}>内容超过记录上限，显示已保存的部分。</p>
    <ContentBlock label={directionLabels[entry.direction]} value={value} />
  </>;

  if (entry.kind === "model" && value && typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    if (entry.direction === "input" && Array.isArray(record.messages)) {
      return <>
        {typeof record.model === "string" && <p className={styles.detailNote}>模型 {record.model}</p>}
        {record.messages.map(({ role, content }, index) => (
          <ContentBlock key={index} label={role === "system" ? "系统提示词" : index === 1 ? "用户输入" : "校验反馈"} value={content} />
        ))}
        <ContentBlock label="可用工具" value={record.tools} />
      </>;
    }
    if (entry.direction === "output" && (record.content != null || record.tool_decision != null)) {
      return <>
        <ContentBlock label="模型输出" value={record.content} />
        <ContentBlock label="工具决策" value={record.tool_decision} />
        <ContentBlock label="修复后的输出" value={record.repaired_content} />
      </>;
    }
  }
  return <ContentBlock label={directionLabels[entry.direction]} value={value} />;
}

function DetailPayload({ runId, entry }: { runId: number; entry: DetailEntry }) {
  const [data, setData] = useState<DetailData>();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    void api<DetailData>("/runs/" + runId + "/details/" + entry.id, { signal: controller.signal, cache: "no-store" })
      .then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(error => { if (!controller.signal.aborted) setError(messageOf(error)); });
    return () => controller.abort();
  }, [entry.id, retry, runId]);
  if (error) return <p className={styles.detailNote} role="alert">{error} <button type="button" onClick={() => setRetry(value => value + 1)}>重试</button></p>;
  return data ? <DetailContent entry={entry} data={data} /> : <p className={styles.detailNote} role="status">正在读取{directionLabels[entry.direction]}…</p>;
}

function CallDetails({ runId, call }: { runId: number; call: Call }) {
  const [expanded, setExpanded] = useState(false);
  const first = call.entries[0];
  const label = first.kind === "node" ? "节点执行"
    : first.kind === "tool" ? toolLabels[first.name] || first.name
    : (first.phase && phaseLabels[first.phase]) || "模型调用";
  const toolName = first.kind === "tool" && label !== first.name ? first.name : undefined;
  const failed = call.entries.some(entry => entry.direction === "error");
  const finished = call.entries.some(entry => entry.direction === "output");
  return <details className={styles.detailCall} onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>
      <span className={styles.detailCallTitle}>{label}{toolName && <small>{toolName}</small>}</span>
      <span className={failed ? styles.detailFailed : styles.detailNote}>
        {first.attempt !== null && first.attempt > 1 && "第 " + first.attempt + " 次 · "}
        {failed ? "失败" : finished ? "输入 / 输出" : "输入"}
      </span>
    </summary>
    {expanded && <div className={styles.detailPayloads}>
      {call.entries.map(entry => <DetailPayload key={entry.id} runId={runId} entry={entry} />)}
    </div>}
  </details>;
}

function NodeDetails({ node, runId, index, active }: { node: NodeExecution; runId: number; index: number; active: boolean }) {
  const [expanded, setExpanded] = useState(index === 0);
  const calls = [...node.calls.values()];
  const execution = calls.find(call => call.entries[0].kind === "node");
  const failed = execution?.entries.some(entry => entry.direction === "error");
  const finished = execution?.entries.some(entry => entry.direction === "output");
  return <details className={styles.detailNode} open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>
      <span className={styles.detailNodeIndex}>{String(index + 1).padStart(2, "0")}</span>
      <strong>{nodeLabels[node.node] || node.node}</strong>
      <span className={failed ? styles.detailFailed : styles.detailNote}>{failed ? "中断" : finished ? "已完成" : active ? "进行中" : "未完成"}</span>
    </summary>
    {expanded && <div className={styles.detailCalls}>
      {calls.map(call => <CallDetails key={call.key} runId={runId} call={call} />)}
    </div>}
  </details>;
}

export default function ResearchDetails({ runId, enabled, active }: { runId: number; enabled: boolean; active: boolean }) {
  const [entries, setEntries] = useState<DetailEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const cursor = useRef(0);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let timer: number | undefined;
    async function readPage() {
      try {
        const page = await api<DetailPage>("/runs/" + runId + "/details?after=" + cursor.current, { signal: controller.signal, cache: "no-store" });
        if (controller.signal.aborted) return;
        if (page.entries.length) {
          cursor.current = page.entries[page.entries.length - 1].id;
          setEntries(current => [...current, ...page.entries]);
        }
        setLoaded(true);
        if (page.has_more) void readPage();
        else if (activeStatuses.has(page.status)) timer = window.setTimeout(readPage, 1200);
      } catch (error) {
        if (!controller.signal.aborted) setError(messageOf(error));
      }
    }
    setError("");
    void readPage();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [enabled, retry, runId]);

  const nodes = useMemo(() => groupEntries(entries), [entries]);
  return <div className={styles.detailsTimeline}>
    {error && <p className={styles.detailNote} role="alert">{error} <button type="button" onClick={() => setRetry(value => value + 1)}>重试</button></p>}
    {!nodes.length && !error && <p className={styles.emptyState} role="status">
      {!loaded ? "正在读取研究细节…" : active ? "节点开始执行后，细节会显示在这里。" : "这次任务没有已保存的研究细节。"}
    </p>}
    {nodes.map((node, index) => <NodeDetails key={node.key} node={node} index={index} runId={runId} active={active} />)}
  </div>;
}
