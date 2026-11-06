"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api, messageOf } from "./api";

export default function PdfImport({ projectId, onImported, notify }: { projectId: number; onImported: () => Promise<void>; notify: (message: string) => void }) {
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => { request.current?.abort(); request.current = null; }, [projectId]);
  const [file, setFile] = useState<File>();
  const [title, setTitle] = useState("");
  const [doi, setDoi] = useState("");
  const [arxiv, setArxiv] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const upload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file || busy) return;
    const form = event.currentTarget;
    const data = new FormData();
    data.append("file", file);
    if (title.trim()) data.append("title", title.trim());
    if (doi.trim()) data.append("doi", doi.trim());
    if (arxiv.trim()) data.append("arxiv_id", arxiv.trim());
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError("");
    try {
      const result = await api<{ duplicate?: boolean; imported_text_status?: string }>(`/projects/${projectId}/papers/import`, { method: "POST", body: data, signal: controller.signal });
      if (controller.signal.aborted) return;
      await onImported();
      if (controller.signal.aborted) return;
      notify(result.duplicate ? (result.imported_text_status === "no_extractable_text" ? "这份 PDF 已在项目中，原文未提取到文字。" : "这份 PDF 已在项目中，无需重复导入。") : (result.imported_text_status === "no_extractable_text" ? "已保存原文，未提取到文字。" : "PDF 已导入项目。"));
      setFile(undefined); setTitle(""); setDoi(""); setArxiv(""); form.reset();
    } catch (e) {
      if (!controller.signal.aborted) setError(messageOf(e));
    } finally {
      if (request.current === controller) { request.current = null; setBusy(false); }
    }
  };
  return (
    <section className="card compact">
      <div className="section-kicker">ADD TO LIBRARY</div>
      <h2>导入 PDF</h2>
      <p className="compact-intro">将本地文献收入当前项目。</p>
      {error && <p className="run-error" role="alert">{error}</p>}
      <form onSubmit={upload} className="upload-form">
        <label htmlFor="pdf">PDF 文件</label>
        <input id="pdf" type="file" accept="application/pdf,.pdf" required onChange={e => setFile(e.target.files?.[0])} />
        <label htmlFor="title">标题 <span>选填</span></label>
        <input id="title" value={title} onChange={e => setTitle(e.target.value)} placeholder="文献标题" />
        <label htmlFor="doi">DOI <span>选填</span></label>
        <input id="doi" value={doi} onChange={e => setDoi(e.target.value)} placeholder="10. ..." />
        <label htmlFor="arxiv">arXiv ID <span>选填</span></label>
        <input id="arxiv" value={arxiv} onChange={e => setArxiv(e.target.value)} placeholder="例如 2301.00001" />
        <button className="button secondary full" disabled={!file || busy}>
          {busy ? "导入中…" : "导入文献"} <span>＋</span>
        </button>
      </form>
    </section>
  );
}
