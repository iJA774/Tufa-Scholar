"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

type Project = { id: number; name: string };
type Conversation = { id: number; title: string };
type Paper = { id?: number; title: string; authors?: string[]; year?: number | null; doi?: string | null; arxiv_id?: string | null; abstract?: string | null; source?: string; source_level?: string; original_url?: string | null; pdf_url?: string | null; versions?: { id: number; filename?: string; file_url?: string; text_status?: string }[] };
type Run = { id: number; query?: string; status: string; stage?: string | null; error?: string | null; result?: Paper[] | { papers?: Paper[]; results?: Paper[]; warning?: string } | null };

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try { const body = await response.json(); message = typeof body.detail === "string" ? body.detail : message; } catch { /* use status */ }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
const json = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const messageOf = (error: unknown) => error instanceof Error ? error.message : "操作失败，请重试";
const statusText: Record<string, string> = { queued: "等待处理", pending: "等待处理", running: "正在检索", succeeded: "检索完成", success: "检索完成", completed: "检索完成", failed: "检索失败", interrupted: "任务已中断" };
const stageText: Record<string, string> = { queued: "等待处理", pending: "等待处理", searching: "检索文献", search: "检索文献", local: "搜索项目文献", openalex: "搜索 OpenAlex", completed: "检索完成", done: "检索完成" };

export default function Home() {
  const [menuId, setMenuId] = useState<number>();
  const [deleteTarget, setDeleteTarget] = useState<Project>();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number>();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<number>();
  const [papers, setPapers] = useState<Paper[]>([]);
  const [projectName, setProjectName] = useState("");
  const [conversationName, setConversationName] = useState("");
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");
  const [run, setRun] = useState<Run>();
  const [runs, setRuns] = useState<Run[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [file, setFile] = useState<File>();
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadDoi, setUploadDoi] = useState("");
  const [uploadArxiv, setUploadArxiv] = useState("");

  useEffect(() => {
    if (menuId === undefined) return;
    const outside = (event: PointerEvent) => { if (!(event.target as Element).closest(".project-menu-wrap")) setMenuId(undefined); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuId(undefined); };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [menuId]);
  useEffect(() => { if (deleteTarget) dialogRef.current?.showModal(); }, [deleteTarget]);
  const refreshProjects = useCallback(async () => { const data = await api<Project[]>("/projects"); setProjects(data); return data; }, []);
  const refreshPapers = useCallback(async (id: number) => setPapers(await api<Paper[]>(`/projects/${id}/papers`)), []);
  useEffect(() => { refreshProjects().then(data => { if (data.length) setProjectId(data[0].id); }).catch(e => setError(messageOf(e))); }, [refreshProjects]);
  useEffect(() => {
    if (!projectId) return;
    setRun(undefined); setRuns([]); setConversationId(undefined); setConversations([]); setPapers([]);
    let current = true;
    Promise.all([api<Conversation[]>(`/projects/${projectId}/conversations`), api<Paper[]>(`/projects/${projectId}/papers`), api<Run[]>(`/projects/${projectId}/runs`)]).then(([cs, ps, rs]) => { if (!current) return; setConversations(cs); setPapers(ps); setRuns(rs); if (rs.length) setRun(rs[0]); if (cs.length) setConversationId(cs[0].id); }).catch(e => { if (current) setError(messageOf(e)); });
    return () => { current = false; };
  }, [projectId]);
  useEffect(() => {
    if (!run || !["queued", "pending", "running"].includes(run.status)) return;
    let current = true;
    const timer = window.setTimeout(() => api<Run>(`/runs/${run.id}`).then(next => { if (current) setRun(next); }).catch(e => { if (current) setError(messageOf(e)); }), 1200);
    return () => { current = false; window.clearTimeout(timer); };
  }, [run]);
  const act = async (key: string, fn: () => Promise<void>) => { setBusy(key); setError(""); setNotice(""); try { await fn(); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const deleteProject = () => { if (!deleteTarget) return; void act("delete", async () => {
    await api(`/projects/${deleteTarget.id}`, { method: "DELETE" });
    const remaining = projects.filter(p => p.id !== deleteTarget.id);
    setProjects(remaining);
    if (deleteTarget.id === projectId) { setRun(undefined); setRuns([]); setConversations([]); setConversationId(undefined); setPapers([]); setProjectId(remaining[0]?.id); }
    dialogRef.current?.close(); setDeleteTarget(undefined); setNotice("研究空间已删除。");
  }); };
  const createProject = (event: FormEvent) => { event.preventDefault(); if (!projectName.trim()) return; void act("project", async () => { const created = await api<Project>("/projects", json({ name: projectName.trim() })); await refreshProjects(); setProjectId(created.id); setProjectName(""); }); };
  const createConversation = (event: FormEvent) => { event.preventDefault(); if (!projectId || !conversationName.trim()) return; void act("conversation", async () => { const created = await api<Conversation>(`/projects/${projectId}/conversations`, json({ title: conversationName.trim() })); setConversations(await api<Conversation[]>(`/projects/${projectId}/conversations`)); setConversationId(created.id); setConversationName(""); }); };
  const search = (event: FormEvent) => { event.preventDefault(); if (!projectId || !query.trim()) return; void act("search", async () => { const submitted = await api<{ run_id: number }>(`/projects/${projectId}/runs`, json({ query: query.trim(), source, conversation_id: conversationId ?? null })); setRun(await api<Run>(`/runs/${submitted.run_id}`)); setRuns(await api<Run[]>(`/projects/${projectId}/runs`)); }); };
  const upload = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (!projectId || !file) return; const form = event.currentTarget; void act("upload", async () => { const data = new FormData(); data.append("file", file); if (uploadTitle.trim()) data.append("title", uploadTitle.trim()); if (uploadDoi.trim()) data.append("doi", uploadDoi.trim()); if (uploadArxiv.trim()) data.append("arxiv_id", uploadArxiv.trim()); const result = await api<{ duplicate?: boolean; imported_text_status?: string }>(`/projects/${projectId}/papers/import`, { method: "POST", body: data }); await refreshPapers(projectId); setNotice(result.duplicate ? (result.imported_text_status === "no_extractable_text" ? "这份 PDF 已在项目中，原文未提取到文字。" : "这份 PDF 已在项目中，无需重复导入。") : (result.imported_text_status === "no_extractable_text" ? "已保存原文，未提取到文字。" : "PDF 已导入项目。")); setFile(undefined); setUploadTitle(""); setUploadDoi(""); setUploadArxiv(""); form.reset(); }); };
  const resultPapers = Array.isArray(run?.result) ? run.result : run?.result?.papers ?? run?.result?.results ?? [];
  const currentProject = projects.find(p => p.id === projectId);

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">✳</span><div><strong>TUFA</strong><small>SCHOLAR</small></div></div>
      <div className="side-heading"><span>研究空间</span><span>{String(projects.length).padStart(2, "0")}</span></div>
      <nav aria-label="项目" className="project-list">{projects.map(p => <div className={p.id === projectId ? "project-row active" : "project-row"} key={p.id}><button className="project" disabled={!!busy} onClick={() => setProjectId(p.id)}><span className="project-icon">▤</span><span>{p.name}</span></button><div className="project-menu-wrap"><button className="project-menu-trigger" type="button" disabled={!!busy} aria-label={`${p.name}的操作`} aria-expanded={menuId === p.id} aria-haspopup="menu" onClick={() => setMenuId(menuId === p.id ? undefined : p.id)}>⋯</button>{menuId === p.id && <div className="project-menu" role="menu"><button role="menuitem" onClick={() => { setMenuId(undefined); setError(""); setDeleteTarget(p); }}>删除空间</button></div>}</div></div>)}{!projects.length && <p className="side-empty">创建一个项目，开始整理研究。</p>}</nav>
      <form onSubmit={createProject} className="side-form"><label htmlFor="project-name">新建项目</label><div className="inline-input"><input id="project-name" value={projectName} onChange={e => setProjectName(e.target.value)} placeholder="输入项目名称" /><button aria-label="创建项目" disabled={!projectName.trim() || !!busy}>＋</button></div></form>
      <div className="side-bottom"><Link className="settings-link" href="/settings">⚙ <span>设置</span></Link><div className="side-foot"><span className="status-dot" /> 本地研究空间</div></div>
    </aside>
    <dialog ref={dialogRef} className="confirm-dialog" aria-labelledby="delete-title" aria-describedby="delete-description" onClose={() => setDeleteTarget(undefined)} onCancel={e => { if (busy) e.preventDefault(); }}><h2 id="delete-title">删除“{deleteTarget?.name}”？</h2><p id="delete-description">删除空间、会话及检索记录，原文保留，其他空间不受影响。</p>{error && <p className="run-error" role="alert">{error}</p>}<div className="dialog-actions"><button className="button secondary" disabled={!!busy} onClick={() => dialogRef.current?.close()}>取消</button><button className="button danger" disabled={!!busy} onClick={deleteProject}>{busy === "delete" ? "删除中…" : "确认删除"}</button></div></dialog>
    <main className="main">
      <header className="topbar"><span>研究工作台 <span className="slash">/</span> {currentProject?.name || "开始使用"}</span><span className="top-label">LITERATURE WORKSPACE</span></header>
      {error && <div className="alert error" role="alert">{error}<button onClick={() => setError("")} aria-label="关闭提示">×</button></div>}
      {notice && <div className="alert" role="status">{notice}<button onClick={() => setNotice("")} aria-label="关闭提示">×</button></div>}
      {!projectId ? <div className="welcome"><div className="eyebrow">YOUR RESEARCH, ROOTED HERE</div><h1>让每一次发现，<br />都有迹可循<span className="accent">。</span></h1><p>为你的研究创建一个项目，开始检索和收藏文献。</p><div className="welcome-line" /></div> : <div className="workspace">
        <div className="page-heading"><div><div className="eyebrow">RESEARCH WORKSPACE</div><h1>{currentProject?.name}</h1><p>检索、导入并整理你的研究文献。</p></div><div className="paper-count"><strong>{String(papers.length).padStart(2, "0")}</strong><span>项目文献</span></div></div>
        <div className="content-grid"><div className="primary-column">
          <section className="card search-card"><div className="section-kicker">01 / DISCOVER</div><div className="section-heading"><div><h2>发现文献</h2><p>从问题或关键词开始探索。</p></div><span className="heading-symbol">↗</span></div>
            <form onSubmit={search}><label htmlFor="query">研究问题或关键词</label><textarea id="query" value={query} onChange={e => setQuery(e.target.value)} placeholder="例如：大语言模型在科学文献综述中的应用" rows={3} /><div className="search-actions"><div className="select-group"><label htmlFor="source">检索范围</label><select id="source" value={source} onChange={e => setSource(e.target.value)}><option value="all">全部来源</option><option value="local">项目文献</option><option value="openalex">OpenAlex</option></select></div><button className="button primary" disabled={!query.trim() || !!busy || !!(run && ["queued", "pending", "running"].includes(run.status))}>{busy === "search" ? "正在提交…" : "开始检索"} <span>↗</span></button></div></form>
          </section>
          {run && <section className="card results-card"><div className="section-kicker">SEARCH RESULTS</div><div className="section-heading"><div><h2>检索结果</h2><p>{statusText[run.status] || run.status}{run.stage && (stageText[run.stage] || run.stage) !== (statusText[run.status] || run.status) ? ` · ${stageText[run.stage] || run.stage}` : ""}</p></div>{["failed", "interrupted"].includes(run.status) && <button className="button secondary" disabled={!!busy} onClick={() => void act("retry", async () => { setRun(await api<Run>(`/runs/${run.id}/retry`, { method: "POST" })); setRuns(await api<Run[]>(`/projects/${projectId}/runs`)); })}>重新执行</button>}</div>{run.error && <p className="run-error">{run.error}</p>}{run.result && !Array.isArray(run.result) && run.result.warning && <p className="run-error">{run.result.warning}</p>}
            {resultPapers.length > 0 ? <div className="paper-list">{resultPapers.map((paper, index) => <PaperRow key={`${paper.doi || paper.arxiv_id || paper.title}-${index}`} paper={paper} action={<button className="text-button" disabled={!!busy} onClick={() => void act(`save-${index}`, async () => { await api(`/projects/${projectId}/papers`, json(paper)); await refreshPapers(projectId); setNotice("文献已保存到项目。"); })}>{busy === `save-${index}` ? "保存中…" : "＋ 保存"}</button>} />)}</div> : !["queued", "pending", "running"].includes(run.status) && !run.error ? <div className="empty-state">没有找到匹配的文献，试试调整检索词。</div> : null}
          </section>}
          <section className="card library-card"><div className="section-kicker">02 / LIBRARY</div><div className="section-heading"><div><h2>项目文献 <span className="count">{papers.length}</span></h2><p>项目中的文献在所有会话间共享。</p></div></div>{papers.length ? <div className="paper-list">{papers.map((paper, i) => <PaperRow key={paper.id ?? i} paper={paper} />)}</div> : <div className="empty-state"><span className="empty-icon">▤</span><strong>还没有文献</strong><span>检索并保存结果，或导入一份 PDF。</span></div>}</section>
        </div><div className="secondary-column">
          <section className="card compact"><div className="section-kicker">CONVERSATION</div><h2>当前会话</h2><label htmlFor="conversation">选择会话</label><select id="conversation" value={conversationId ?? ""} onChange={e => setConversationId(e.target.value ? Number(e.target.value) : undefined)}><option value="">不指定会话</option>{conversations.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select><form onSubmit={createConversation} className="new-conversation"><label htmlFor="conversation-name">新建会话</label><div className="inline-input"><input id="conversation-name" value={conversationName} onChange={e => setConversationName(e.target.value)} placeholder="输入会话名称" /><button aria-label="创建会话" disabled={!conversationName.trim() || !!busy}>＋</button></div></form></section>
          <section className="card compact"><div className="section-kicker">RECENT SEARCHES</div><h2>最近检索</h2>{runs.length ? <div className="recent-list">{runs.map(item => <button key={item.id} className={item.id === run?.id ? "recent-item selected" : "recent-item"} disabled={!!busy} onClick={() => void act("load-run", async () => setRun(await api<Run>(`/runs/${item.id}`)))}><span>{item.query || `检索 ${item.id}`}</span><small>{item.id === run?.id ? statusText[run.status] || run.status : statusText[item.status] || item.status}</small></button>)}</div> : <p className="compact-intro recent-empty">还没有检索记录。</p>}</section>
          <section className="card compact"><div className="section-kicker">ADD TO LIBRARY</div><h2>导入 PDF</h2><p className="compact-intro">将本地文献收入当前项目。</p><form onSubmit={upload} className="upload-form"><label htmlFor="pdf">PDF 文件</label><input id="pdf" type="file" accept="application/pdf,.pdf" required onChange={e => setFile(e.target.files?.[0])} /><label htmlFor="title">标题 <span>选填</span></label><input id="title" value={uploadTitle} onChange={e => setUploadTitle(e.target.value)} placeholder="文献标题" /><label htmlFor="doi">DOI <span>选填</span></label><input id="doi" value={uploadDoi} onChange={e => setUploadDoi(e.target.value)} placeholder="10. ..." /><label htmlFor="arxiv">arXiv ID <span>选填</span></label><input id="arxiv" value={uploadArxiv} onChange={e => setUploadArxiv(e.target.value)} placeholder="例如 2301.00001" /><button className="button secondary full" disabled={!file || !!busy}>{busy === "upload" ? "导入中…" : "导入文献"} <span>＋</span></button></form></section>
        </div></div>
      </div>}
    </main>
  </div>;
}

function PaperRow({ paper, action }: { paper: Paper; action?: React.ReactNode }) {
  const versions = paper.versions ?? [];
  const sourceLevel: Record<string, string> = { full_text: "已提取正文", abstract: "仅摘要", metadata_only: "仅文献信息", metadata: "仅文献信息" };
  return <article className="paper-row"><div className="paper-index">◇</div><div className="paper-body"><h3>{paper.title}</h3><p className="metadata">{[paper.authors?.join(", "), paper.year, paper.source].filter(Boolean).join(" · ") || "文献信息待补充"}</p>{paper.source_level && sourceLevel[paper.source_level] && <span className="source-level">{sourceLevel[paper.source_level]}</span>}{paper.abstract && <p className="abstract">{paper.abstract}</p>}<div className="paper-links">{versions.map((version, index) => <span key={version.id}><a href={`/api/paper-versions/${version.id}/file`} target="_blank" rel="noreferrer">{versions.length > 1 ? `PDF ${index + 1}${version.filename ? ` · ${version.filename}` : ""}` : "打开 PDF"} ↗</a>{version.text_status === "no_extractable_text" && <span className="text-unavailable"> · 未提取到文字</span>}</span>)}{paper.original_url && <a href={paper.original_url} target="_blank" rel="noreferrer">原文 ↗</a>}{!versions.length && paper.pdf_url && <a href={paper.pdf_url} target="_blank" rel="noreferrer">PDF ↗</a>}{paper.doi && <span>DOI: {paper.doi}</span>}</div></div>{action}</article>;
}
