"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, json, messageOf } from "../workspace/api";
import panel from "./SettingsPanel.module.css";
import styles from "./LocalDocuments.module.css";

type Item = { id: string; path: string; output: string; state: string; format: string; message?: string; warnings?: string[]; import_error?: string; paper_id?: number; elapsed?: number; classification?: { domains: string[]; tags: string[]; description: string } };
type Batch = { kind?: string; id: string; root_name: string; state: string; revision: number; project_id?: number; files: Item[]; done: number; total: number; counts: Record<string, number> };
type Preview = { markdown: string; truncated: boolean; warnings: string[]; mapping: { page: number; anchor: string }[] };
const storageKey = "tufa-conversion-batch";
const batchStartedEvent = "tufa-conversion-batch-started";
const finished = new Set(["completed", "cancelled", "interrupted"]);
const labels: Record<string, string> = { waiting: "等待上传", uploading: "上传中", uploaded: "等待转换", running: "转换中", succeeded: "成功", partial: "部分转换", failed: "失败", skipped: "跳过", cancelled: "已取消" };
export default function LocalDocuments({ projectId }: { projectId?: number }) {
  const previewAbort = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<{ name: string; result: Preview } | null>(null);
  const [previewLoading, setPreviewLoading] = useState("");
  const [pollFailed, setPollFailed] = useState(false);
  const [pollAttempt, setPollAttempt] = useState(0);
  const active = busy || Boolean(batch && !finished.has(batch.state));
  const acceptBatch = useCallback((value: Batch) => setBatch(previous => previous?.id === value.id && previous.revision > value.revision ? previous : value), []);

  useEffect(() => {
    mounted.current = true;
    try { setBatchId(localStorage.getItem(storageKey)); } catch { /* Storage may be disabled. */ }
    const onBatchStarted = (event: Event) => {
      const created = (event as CustomEvent<Batch>).detail;
      acceptBatch(created); setBatchId(created.id); setPollAttempt(value => value + 1);
    };
    window.addEventListener(batchStartedEvent, onBatchStarted);
    return () => {
      mounted.current = false;
      window.removeEventListener(batchStartedEvent, onBatchStarted);
      previewAbort.current?.abort();
    };
  }, [acceptBatch]);

  useEffect(() => {
    if (!batchId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const poll = async () => {
      try {
        const value = await api<Batch>(`/conversion-batches/${batchId}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        failures = 0;
        setPollFailed(false);
        acceptBatch(value);
        if (!finished.has(value.state)) timer = setTimeout(poll, 900);
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (++failures >= 3) { setPollFailed(true); setError(`暂时无法读取批次状态：${messageOf(cause)}`); }
        else timer = setTimeout(poll, 1500);
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [batchId, pollAttempt, acceptBatch]);

  const openDirectory = async () => {
    setError("");
    try { await api("/conversion-batches/open-originals", { method: "POST" }); }
    catch (cause) { if (mounted.current) setError(messageOf(cause)); }
  };

  const convert = async () => {
    if (active) return;
    setBusy(true); setError(""); setPreview(null); setPreviewLoading("");
    previewAbort.current?.abort();
    try {
      const created = await api<Batch>("/conversion-batches/prepare", json({ project_id: projectId ?? null }));
      try { localStorage.setItem(storageKey, created.id); } catch { /* Session still works without storage. */ }
      window.dispatchEvent(new CustomEvent(batchStartedEvent, { detail: created }));
    } catch (cause) { if (mounted.current) setError(messageOf(cause)); }
    finally { if (mounted.current) setBusy(false); }
  };

  const clear = async () => {
    if (active || !window.confirm("清除所有项目中转换生成的 Markdown、批次和本地文档分类及索引？原始文件和 PDF 文献库会保留。")) return;
    setBusy(true); setError(""); setNotice("");
    previewAbort.current?.abort();
    try {
      const result = await api<{ files: number; cleanup_pending: boolean }>("/conversion-batches/clear", json({ confirm: true }));
      try { localStorage.removeItem(storageKey); } catch { /* Storage may be disabled. */ }
      if (mounted.current) {
        setBatchId(null); setBatch(null); setPreview(null); setPreviewLoading(""); setPollFailed(false);
        setNotice(result.cleanup_pending ? "清理记录已完成，部分临时文件尚待删除。" : `清理完成，移除了 ${result.files} 个转换文件。`);
      }
    } catch (cause) { if (mounted.current) setError(messageOf(cause)); }
    finally { if (mounted.current) setBusy(false); }
  };

  const cancel = async () => {
    if (!batchId) return;
    try {
      const result = await api<Batch>(`/conversion-batches/${batchId}/cancel`, { method: "POST" });
      if (mounted.current) setBatch(previous => previous?.id === result.id && previous.revision <= result.revision ? result : previous);
    } catch (cause) { if (mounted.current) setError(messageOf(cause)); }
  };
  const showPreview = async (item: Item) => {
    previewAbort.current?.abort();
    const controller = new AbortController(); previewAbort.current = controller;
    setPreviewLoading(item.id); setPreview(null);
    try {
      const result = await api<Preview>(`/conversion-batches/${batchId}/files/${item.id}/preview`, { signal: controller.signal });
      if (!controller.signal.aborted) setPreview({ name: item.output, result });
    } catch (cause) { if (!controller.signal.aborted) setError(messageOf(cause)); }
    finally { if (!controller.signal.aborted) setPreviewLoading(""); }
  };
  const ready = batch?.files.some(item => item.format !== "md" && ["succeeded", "partial"].includes(item.state));

  return <section className={`${panel.card} ${styles.documents}`}>
    <h2>本地文档</h2>
    <div className={styles.toolbar}>
      <button className={panel.actionButton} type="button" onClick={() => void openDirectory()}>打开目录</button>
    </div>
    <div className={styles.conversion}>
      <h3>本地文献预处理</h3>
      <p>目录中的文件预处理后可加入当前项目。旧版 DOC 请先另存为 DOCX。</p>
      <p>分析与分类会将 Markdown 内容发送至配置的模型。</p>
      <div className={styles.toolbar}>
        <button className={styles.convertButton} type="button" disabled={active} onClick={() => void convert()}>预处理</button>
        <button className={panel.actionButton} type="button" disabled={active} onClick={() => void clear()}>清除已保存的文档</button>
        {batch && !finished.has(batch.state) && <button className={panel.actionButton} type="button" onClick={() => void cancel()}>取消批次</button>}
      </div>
    </div>
    {notice && <p role="status">{notice}</p>}
    {error && <p className={styles.error} role="alert">{error}</p>}
    {pollFailed && <button className={panel.actionButton} type="button" onClick={() => { setError(""); setPollAttempt(value => value+1); }}>重新读取状态</button>}
    {batch && <div className={styles.results}>
      <h3>{batch.root_name} · {(batch.kind === "preprocess" || batch.files.some(item => item.format === "md" && item.state === "running")) && !finished.has(batch.state) ? "分析中" : ({ uploading: "上传中", queued: "等待转换", running: "转换中", completed: "已结束", cancelled: "已取消", interrupted: "已中断" } as Record<string, string>)[batch.state]}</h3>
      <div role="status" aria-live="polite">{batch.kind === "combined" ? "预处理进度" : batch.kind === "preprocess" ? "分析进度" : "转换进度"}：{batch.done} / {batch.total}{batch.project_id ? ` · 导入项目 #${batch.project_id}` : ""}</div>
      <progress aria-label={batch.kind === "combined" ? "文档预处理进度" : batch.kind === "preprocess" ? "文档分析进度" : "文档转换进度"} value={batch.done} max={batch.total} />
      <p>成功 {batch.counts.succeeded || 0}{batch.kind !== "preprocess" && ` · 部分转换 ${batch.counts.partial || 0}`} · 失败 {batch.counts.failed || 0} · 跳过 {batch.counts.skipped || 0} · 取消 {batch.counts.cancelled || 0}</p>
      <ul className={styles.resultList} aria-label={batch.kind === "combined" ? "预处理结果" : batch.kind === "preprocess" ? "分析结果" : "转换结果"}>{batch.files.map(item => <li key={item.id} data-state={item.state}>
        <div className={styles.resultTitle}><span>{item.path}</span><strong>{(batch.kind === "preprocess" || item.format === "md") && item.state === "running" ? "分析中" : (batch.kind === "preprocess" || item.format === "md") && ["waiting", "uploaded"].includes(item.state) ? "等待分析" : labels[item.state] || item.state}</strong></div>
        {item.classification && <p>领域：{item.classification.domains.join("、")} · 标签：{item.classification.tags.join("、")}<br />{item.classification.description}</p>}
        {item.message && <p>{item.message}</p>}
        {item.import_error && <p className={styles.error}>{item.import_error}</p>}
        {item.paper_id && <p>已导入当前批次项目 · 文献 #{item.paper_id}</p>}
        {item.warnings && <details><summary>解析局限{item.elapsed ? ` · ${item.elapsed} 秒` : ""}</summary>{item.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</details>}
        {batch.kind !== "preprocess" && item.format !== "md" && ["succeeded", "partial"].includes(item.state) && <div className={styles.toolbar}><button className={panel.actionButton} type="button" onClick={() => void showPreview(item)} disabled={previewLoading === item.id}>{previewLoading === item.id ? "读取中…" : "预览 Markdown"}</button><a href={`/api/conversion-batches/${batch.id}/files/${item.id}/markdown`} download>下载 Markdown</a></div>}
      </li>)}</ul>
      {batch.kind !== "preprocess" && ready && finished.has(batch.state) && <a className={styles.downloadZip} href={`/api/conversion-batches/${batch.id}/zip`} download>下载 Markdown ZIP</a>}
      {preview && <div className={styles.preview}><div className={styles.resultTitle}><h3>{preview.name}</h3><button className={panel.actionButton} type="button" onClick={() => setPreview(null)}>关闭预览</button></div>{preview.result.truncated && <p>预览仅显示前 64,000 字符，请下载完整 Markdown。</p>}<pre tabIndex={0} aria-label="Markdown 内容">{preview.result.markdown}</pre></div>}
    </div>}
  </section>;
}
