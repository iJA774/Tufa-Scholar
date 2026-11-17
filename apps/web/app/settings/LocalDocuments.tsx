"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, json, messageOf } from "../workspace/api";
import panel from "./SettingsPanel.module.css";
import styles from "./LocalDocuments.module.css";

type Item = { id: string; path: string; output: string; state: string; format: string; message?: string; warnings?: string[]; import_error?: string; paper_id?: number; elapsed?: number };
type Batch = { id: string; root_name: string; state: string; revision: number; project_id?: number; files: Item[]; done: number; total: number; counts: Record<string, number> };
type Preview = { markdown: string; truncated: boolean; warnings: string[]; mapping: { page: number; anchor: string }[] };
const storageKey = "tufa-conversion-batch";
const finished = new Set(["completed", "cancelled", "interrupted"]);
const labels: Record<string, string> = { waiting: "等待上传", uploading: "上传中", uploaded: "等待转换", running: "转换中", succeeded: "成功", partial: "部分转换", failed: "失败", skipped: "跳过", cancelled: "已取消" };
const supported = (file: File) => /\.(pdf|docx|doc|txt)$/i.test(file.name);
const relative = (file: File) => file.webkitRelativePath.split("/").slice(1).join("/") || file.name;
const rootName = (file: File) => file.webkitRelativePath.split("/").slice(0, -1)[0] || "Documents";

export default function LocalDocuments({ projectId }: { projectId?: number }) {
  const input = useRef<HTMLInputElement>(null);
  const upload = useRef<{ controller: AbortController; batchId?: string; submitted: boolean } | null>(null);
  const previewAbort = useRef<AbortController | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [includeChildren, setIncludeChildren] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importPdf, setImportPdf] = useState(false);
  const [encoding, setEncoding] = useState("auto");
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<{ name: string; result: Preview } | null>(null);
  const [previewLoading, setPreviewLoading] = useState("");
  const [pollFailed, setPollFailed] = useState(false);
  const [pollAttempt, setPollAttempt] = useState(0);
  const eligible = files.filter(file => supported(file) && (includeChildren || !relative(file).includes("/")));
  const chosen = eligible.filter(file => selected.has(relative(file)));
  const active = busy || Boolean(batch && !finished.has(batch.state));
  const acceptBatch = useCallback((value: Batch) => setBatch(previous => previous?.id === value.id && previous.revision > value.revision ? previous : value), []);

  useEffect(() => {
    try { setBatchId(localStorage.getItem(storageKey)); } catch { /* Storage may be disabled. */ }
    return () => {
      const current = upload.current;
      current?.controller.abort();
      if (current?.batchId && !current.submitted) {
        void fetch(`/api/conversion-batches/${current.batchId}/cancel`, { method: "POST", keepalive: true }).catch(() => {});
      }
      previewAbort.current?.abort();
    };
  }, []);

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
        if (++failures >= 3) { setPollFailed(true); setError(`暂时无法读取转换状态：${messageOf(cause)}`); }
        else timer = setTimeout(poll, 1500);
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [batchId, pollAttempt, acceptBatch]);

  const chooseFolder = (chosenFiles: File[]) => {
    const sorted = chosenFiles.sort((a, b) => relative(a).localeCompare(relative(b), undefined, { numeric: true }));
    setFiles(sorted);
    setSelected(new Set(sorted.filter(file => supported(file) && (includeChildren || !relative(file).includes("/"))).slice(0, 100).map(relative)));
    setError("");
  };

  const convert = async () => {
    if (!chosen.length || active) return;
    if (chosen.length > 100) { setError("单批最多 100 个文件，请减少选择。"); return; }
    if (chosen.reduce((sum, file) => sum + file.size, 0) > 200 * 1024 * 1024) { setError("单批总大小不能超过 200 MiB。"); return; }
    const current = { controller: new AbortController(), submitted: false, batchId: undefined as string | undefined };
    upload.current = current;
    setBusy(true); setError(""); setPreview(null);
    previewAbort.current?.abort();
    try {
      const created = await api<Batch>("/conversion-batches", { ...json({ root_name: rootName(chosen[0]), files: chosen.map(file => ({ path: relative(file), size: file.size })), encoding, project_id: importPdf ? projectId : null }), signal: current.controller.signal });
      current.batchId = created.id;
      try { localStorage.setItem(storageKey, created.id); } catch { /* Session still works without storage. */ }
      acceptBatch(created); setBatchId(created.id);
      for (const item of created.files) {
        if (current.controller.signal.aborted) break;
        if (item.state !== "waiting") continue;
        const file = chosen.find(value => relative(value).normalize("NFC") === item.path);
        if (!file) continue;
        const body = new FormData(); body.append("file", file, file.name);
        try {
          await api(`/conversion-batches/${created.id}/files/${item.id}`, { method: "POST", body, signal: current.controller.signal });
        } catch (cause) {
          if (current.controller.signal.aborted) throw cause;
          // Finalization marks missing uploads as failed; continue other files.
          setError(`有文件上传失败：${messageOf(cause)}`);
        }
      }
      if (current.controller.signal.aborted) return;
      acceptBatch(await api<Batch>(`/conversion-batches/${created.id}/start`, { method: "POST", signal: current.controller.signal }));
      current.submitted = true;
      setPollAttempt(value => value + 1);
    } catch (cause) {
      if (!current.controller.signal.aborted) setError(messageOf(cause));
      if (current.batchId) {
        try { acceptBatch(await api<Batch>(`/conversion-batches/${current.batchId}/cancel`, { method: "POST" })); } catch { /* Expired or unavailable service; server upload deadline applies. */ }
      }
    } finally {
      if (!current.controller.signal.aborted) setBusy(false);
      if (upload.current === current) upload.current = null;
    }
  };

  const cancel = async () => {
    upload.current?.controller.abort();
    setBusy(false);
    if (!batchId) return;
    try { acceptBatch(await api<Batch>(`/conversion-batches/${batchId}/cancel`, { method: "POST" })); }
    catch (cause) { setError(messageOf(cause)); }
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
  const ready = batch?.files.some(item => ["succeeded", "partial"].includes(item.state));

  return <section className={`${panel.card} ${styles.documents}`}>
    <h2>本地文档</h2>
    <input ref={element => { input.current = element; if (element) element.webkitdirectory = true; }} className={panel.directoryInput} type="file" multiple aria-label="选择本地目录" disabled={active} onChange={event => chooseFolder(Array.from(event.currentTarget.files ?? []))} />
    <div className={styles.toolbar}>
      <button className={panel.actionButton} type="button" disabled={active} onClick={() => input.current?.click()}>选择文件夹</button>
      <span>{files.length ? `已选择：${rootName(files[0])} · ${files.length} 个文件` : "尚未选择目录"}</span>
    </div>
    {files.length > 0 && <details><summary>目录文件名（{files.length}）</summary><ul className={panel.fileList} aria-label="目录文件名">{files.slice(0, 200).map(file => <li key={file.webkitRelativePath || file.name}>{file.webkitRelativePath || file.name}</li>)}</ul>{files.length > 200 && <p>仅展示前 200 个文件名。</p>}</details>}
    <div className={styles.conversion}>
      <h3>Markdown 转换</h3>
      <p>PDF、DOCX、TXT 在本机离线处理。旧版 DOC 请先另存为 DOCX；扫描 PDF 暂无 OCR。</p>
      <div className={styles.options}>
        <label><input type="checkbox" checked={includeChildren} disabled={active} onChange={event => { setIncludeChildren(event.target.checked); setSelected(new Set(files.filter(file => supported(file) && (event.target.checked || !relative(file).includes("/"))).slice(0, 100).map(relative))); }} />包含子文件夹</label>
        <label><input type="checkbox" checked={importPdf && Boolean(projectId)} disabled={active || !projectId} onChange={event => setImportPdf(event.target.checked)} />转换成功后导入当前项目文献库（仅 PDF）</label>
        <label>TXT 编码<select aria-label="TXT 编码" value={encoding} disabled={active} onChange={event => setEncoding(event.target.value)}><option value="auto">自动识别</option><option value="utf-8-sig">UTF-8 / BOM</option><option value="gb18030">GB18030 / GBK</option><option value="utf-16">UTF-16</option><option value="big5">Big5</option><option value="cp1252">Windows-1252</option></select></label>
      </div>
      {importPdf && <p>导入提交时的当前项目；同一原始 PDF 不重复建论文。DOCX、TXT 仅转换下载，暂不接入论文检索。</p>}
      <p>已发现 {eligible.length} 个候选：PDF {eligible.filter(file => /\.pdf$/i.test(file.name)).length} · DOCX {eligible.filter(file => /\.docx$/i.test(file.name)).length} · TXT {eligible.filter(file => /\.txt$/i.test(file.name)).length} · DOC {eligible.filter(file => /\.doc$/i.test(file.name)).length}（跳过）</p>
      {files.length > 0 && !eligible.length && <p role="status">此范围内没有可转换的 PDF、DOCX 或 TXT 文件。</p>}
      {eligible.length > 0 && <>
        <label className={styles.selectAll}><input type="checkbox" disabled={active || eligible.length > 100} checked={chosen.length === eligible.length} onChange={event => setSelected(new Set(event.target.checked ? eligible.map(relative) : []))} />全选 · 已选 {chosen.length} 个{eligible.length > 100 ? "（单批最多 100 个，默认选择前 100 个）" : ""}</label>
        <ul className={styles.selection} aria-label="待转换文档">{eligible.map(file => <li key={relative(file)}><label><input type="checkbox" disabled={active || (!selected.has(relative(file)) && chosen.length >= 100)} checked={selected.has(relative(file))} onChange={event => setSelected(previous => { const next = new Set(previous); if (event.target.checked) next.add(relative(file)); else next.delete(relative(file)); return next; })} /><span>{relative(file)}</span><small>{(file.size/1024).toFixed(1)} KiB</small></label></li>)}</ul>
      </>}
      <div className={styles.toolbar}>
        <button className={styles.convertButton} type="button" disabled={!chosen.length || active} onClick={() => void convert()}>一键转换 Markdown</button>
        {active && <button className={panel.actionButton} type="button" onClick={() => void cancel()}>取消批次</button>}
      </div>
      <p>每批最多 100 个 / 200 MiB，单文件 20 MiB、PDF 200 页、转换限时 45 秒。上传期间关闭设置会取消；开始转换后关闭设置会继续运行。</p>
    </div>
    {error && <p className={styles.error} role="alert">{error}</p>}
    {pollFailed && <button className={panel.actionButton} type="button" onClick={() => { setError(""); setPollAttempt(value => value+1); }}>重新读取状态</button>}
    {batch && <div className={styles.results}>
      <h3>{batch.root_name} · {({ uploading: "上传中", queued: "等待转换", running: "转换中", completed: "已结束", cancelled: "已取消", interrupted: "已中断" } as Record<string, string>)[batch.state]}</h3>
      <div role="status" aria-live="polite">转换进度：{batch.done} / {batch.total}{batch.project_id ? ` · 导入项目 #${batch.project_id}` : ""}</div>
      <progress aria-label="文档转换进度" value={batch.done} max={batch.total} />
      <p>成功 {batch.counts.succeeded || 0} · 部分转换 {batch.counts.partial || 0} · 失败 {batch.counts.failed || 0} · 跳过 {batch.counts.skipped || 0} · 取消 {batch.counts.cancelled || 0}</p>
      <ul className={styles.resultList} aria-label="转换结果">{batch.files.map(item => <li key={item.id} data-state={item.state}>
        <div className={styles.resultTitle}><span>{item.path}</span><strong>{labels[item.state] || item.state}</strong></div>
        {item.message && <p>{item.message}</p>}
        {item.import_error && <p className={styles.error}>{item.import_error}</p>}
        {item.paper_id && <p>已导入当前批次项目 · 文献 #{item.paper_id}</p>}
        {item.warnings && <details><summary>解析局限{item.elapsed ? ` · ${item.elapsed} 秒` : ""}</summary>{item.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</details>}
        {["succeeded", "partial"].includes(item.state) && <div className={styles.toolbar}><button className={panel.actionButton} type="button" onClick={() => void showPreview(item)} disabled={previewLoading === item.id}>{previewLoading === item.id ? "读取中…" : "预览 Markdown"}</button><a href={`/api/conversion-batches/${batch.id}/files/${item.id}/markdown`} download>下载 Markdown</a></div>}
      </li>)}</ul>
      {ready && finished.has(batch.state) && <a className={styles.downloadZip} href={`/api/conversion-batches/${batch.id}/zip`} download>下载 Markdown ZIP</a>}
      {preview && <div className={styles.preview}><div className={styles.resultTitle}><h3>{preview.name}</h3><button className={panel.actionButton} type="button" onClick={() => setPreview(null)}>关闭预览</button></div>{preview.result.truncated && <p>预览仅显示前 64,000 字符，请下载完整 Markdown。</p>}<pre tabIndex={0} aria-label="Markdown 内容">{preview.result.markdown}</pre></div>}
    </div>}
  </section>;
}
