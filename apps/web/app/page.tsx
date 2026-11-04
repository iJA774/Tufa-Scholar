"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

type Project = { id: number; name: string };
type Conversation = { id: number; title: string };
type Paper = { id?: number; title: string; authors?: string[]; year?: number | null; doi?: string | null; arxiv_id?: string | null; abstract?: string | null; source?: string; source_level?: string; original_url?: string | null; pdf_url?: string | null; versions?: { id: number; filename?: string; file_url?: string; text_status?: string }[] };
type Message = { id: number; role: string; content: string; metadata?: { evidence?: Citation[]; report_id?: number } };
type Citation = { id?: string; title?: string; version_id?: number; page?: number; original_url?: string; source_level?: string };
type Run = { id: number; conversation_id?: number; query?: string; status: string; stage?: string | null; error?: string | null; result?: Paper[] | { papers?: Paper[]; results?: Paper[]; warning?: string } | null };

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
const statusText: Record<string, string> = { queued: "等待处理", pending: "等待处理", running: "正在处理", succeeded: "回答完成", success: "回答完成", completed: "回答完成", failed: "任务失败", interrupted: "任务已中断" };
const stageText: Record<string, string> = { queued: "等待处理", pending: "等待处理", searching: "检索文献", search: "检索文献", local: "搜索项目文献", openalex: "搜索 OpenAlex", completed: "回答完成", done: "回答完成", retrieving: "检索文献", generating: "生成回答", synthesizing: "整理证据", planning: "规划问题", evidence: "读取原文", assessing: "检查证据缺口", verifying: "核验结论", answering: "生成回答" };

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchRef = useRef<HTMLDialogElement>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [hits, setHits] = useState<{conversations: Conversation[]; papers: Paper[]}>();
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
  useEffect(() => { if (searchOpen) searchRef.current?.showModal(); }, [searchOpen]);
  useEffect(() => { setMessages([]); if (!conversationId) return; let current = true; api<Message[]>(`/conversations/${conversationId}/messages`).then(data => { if (current) setMessages(data); }).catch(e => { if (current) setError(messageOf(e)); }); return () => { current = false; }; }, [conversationId, projectId]);
  useEffect(() => { if (run?.status !== "succeeded" || !conversationId) return; let current = true; api<Message[]>(`/conversations/${conversationId}/messages`).then(data => { if (current) setMessages(data); }).catch(e => { if (current) setError(messageOf(e)); }); return () => { current = false; }; }, [run?.status, conversationId]);
  useEffect(() => { setHits(undefined); }, [projectId]);
  useEffect(() => { if (deleteTarget) dialogRef.current?.showModal(); }, [deleteTarget]);
  const refreshProjects = useCallback(async () => { const data = await api<Project[]>("/projects"); setProjects(data); return data; }, []);
  const refreshPapers = useCallback(async (id: number) => setPapers(await api<Paper[]>(`/projects/${id}/papers`)), []);
  useEffect(() => { refreshProjects().then(data => { if (data.length) setProjectId(data[0].id); }).catch(e => setError(messageOf(e))); }, [refreshProjects]);
  useEffect(() => {
    if (!projectId) return;
    setRun(undefined); setRuns([]); setConversationId(undefined); setConversations([]); setPapers([]);
    let current = true;
    Promise.all([api<Conversation[]>(`/projects/${projectId}/conversations`), api<Paper[]>(`/projects/${projectId}/papers`), api<Run[]>(`/projects/${projectId}/runs`)]).then(([cs, ps, rs]) => { if (!current) return; setConversations(cs); setPapers(ps); setRuns(rs); if (cs.length) { setConversationId(cs[0].id); setRun(rs.find(r => r.conversation_id === cs[0].id)); } }).catch(e => { if (current) setError(messageOf(e)); });
    return () => { current = false; };
  }, [projectId]);
  useEffect(() => {
    if (!run || !["queued", "pending", "running"].includes(run.status)) return;
    let current = true;
    const timer = window.setTimeout(() => api<Run>(`/runs/${run.id}`).then(next => { if (current) { setRun(next); setRuns(previous => previous.map(item => item.id === next.id ? next : item)); } }).catch(e => { if (current) setError(messageOf(e)); }), 1200);
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
  const createConversation = (event: FormEvent) => { event.preventDefault(); if (!projectId || !conversationName.trim()) return; void act("conversation", async () => { const created = await api<Conversation>(`/projects/${projectId}/conversations`, json({ title: conversationName.trim() })); setConversations(await api<Conversation[]>(`/projects/${projectId}/conversations`)); setRun(undefined); setConversationId(created.id); setConversationName(""); }); };
  const search = (event: FormEvent) => { event.preventDefault(); if (!projectId || !query.trim() || busy || (run && ["pending", "running"].includes(run.status))) return; void act("search", async () => { const submitted = await api<{ run_id: number; conversation_id: number }>(`/projects/${projectId}/questions`, json({ question: query.trim(), source, conversation_id: conversationId ?? null })); setQuery(""); setConversations(await api<Conversation[]>(`/projects/${projectId}/conversations`)); setConversationId(submitted.conversation_id); setMessages(await api<Message[]>(`/conversations/${submitted.conversation_id}/messages`)); setRun(await api<Run>(`/runs/${submitted.run_id}`)); setRuns(await api<Run[]>(`/projects/${projectId}/runs`)); }); };
  const findHistory = (event: FormEvent) => { event.preventDefault(); if (!projectId || !searchQuery.trim()) return; void act("history", async () => setHits(await api(`/projects/${projectId}/search?q=${encodeURIComponent(searchQuery.trim())}`))); };
  const upload = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (!projectId || !file) return; const form = event.currentTarget; void act("upload", async () => { const data = new FormData(); data.append("file", file); if (uploadTitle.trim()) data.append("title", uploadTitle.trim()); if (uploadDoi.trim()) data.append("doi", uploadDoi.trim()); if (uploadArxiv.trim()) data.append("arxiv_id", uploadArxiv.trim()); const result = await api<{ duplicate?: boolean; imported_text_status?: string }>(`/projects/${projectId}/papers/import`, { method: "POST", body: data }); await refreshPapers(projectId); setNotice(result.duplicate ? (result.imported_text_status === "no_extractable_text" ? "这份 PDF 已在项目中，原文未提取到文字。" : "这份 PDF 已在项目中，无需重复导入。") : (result.imported_text_status === "no_extractable_text" ? "已保存原文，未提取到文字。" : "PDF 已导入项目。")); setFile(undefined); setUploadTitle(""); setUploadDoi(""); setUploadArxiv(""); form.reset(); }); };
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
    <dialog ref={searchRef} className="confirm-dialog history-dialog" aria-labelledby="history-title" onClose={() => setSearchOpen(false)}><div className="section-heading"><h2 id="history-title">搜索空间</h2><button className="history-close" aria-label="关闭搜索" onClick={() => searchRef.current?.close()}>×</button></div><form onSubmit={findHistory} className="history-form"><label htmlFor="history-query">搜索会话、消息和文献</label><div><input id="history-query" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder="输入关键词" /><button className="button primary" disabled={!!busy || !searchQuery.trim()}>搜索</button></div></form>{error && <p role="alert">{error}</p>}{hits && <div className="history-results">{!hits.conversations.length && !hits.papers.length && <p>没有找到匹配结果。</p>}{hits.conversations.map(c => <button key={c.id} disabled={!!busy} onClick={() => { setConversationId(c.id); setRun(runs.find(r => r.conversation_id === c.id)); searchRef.current?.close(); }}>会话 · {c.title}</button>)}{hits.papers.map((p, i) => <PaperRow key={p.id ?? i} paper={p} />)}</div>}</dialog>
    <main className="main">
      <header className="topbar"><span>研究工作台 <span className="slash">/</span> {currentProject?.name || "开始使用"}</span><button className="history-trigger" aria-label="搜索空间历史" disabled={!projectId} onClick={() => setSearchOpen(true)}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg></button></header>
      {error && <div className="alert error" role="alert">{error}<button onClick={() => setError("")} aria-label="关闭提示">×</button></div>}
      {notice && <div className="alert" role="status">{notice}<button onClick={() => setNotice("")} aria-label="关闭提示">×</button></div>}
      {!projectId ? <div className="welcome"><div className="eyebrow">YOUR RESEARCH, ROOTED HERE</div><h1>让每一次发现，<br />都有迹可循<span className="accent">。</span></h1><p>为你的研究创建一个项目，开始检索和收藏文献。</p><div className="welcome-line" /></div> : <div className="workspace">
        <div className="page-heading"><div><div className="eyebrow">RESEARCH WORKSPACE</div><h1>{currentProject?.name}</h1><p>检索、导入并整理你的研究文献。</p></div><div className="paper-count"><strong>{String(papers.length).padStart(2, "0")}</strong><span>项目文献</span></div></div>
        <div className="content-grid"><div className="primary-column">
          <section className="card chat-card"><div className="section-kicker">RESEARCH CONVERSATION</div><div className="section-heading"><div><h2>科研问答</h2><p>围绕问题探索文献与证据。</p></div></div><div className="messages">{messages.length ? messages.map(m => <article className={`message ${m.role === "user" ? "user-message" : "assistant-message"}`} key={m.id}><div className="message-label">{m.role === "user" ? "你" : "TUFA"}</div><div className="message-content">{m.content}</div>{m.metadata?.evidence?.map((c, i) => <div className="citation" key={i}>{c.version_id ? <a href={`/api/paper-versions/${c.version_id}/file${c.page ? `#page=${c.page}` : ""}`} target="_blank" rel="noreferrer">{c.title || `参考文献 ${i + 1}`} ↗</a> : c.original_url ? <a href={c.original_url} target="_blank" rel="noreferrer">{c.title || `参考文献 ${i + 1}`} ↗</a> : <span>{c.title || `参考文献 ${i + 1}`}</span>}{c.id && ` [${c.id}]`}{c.page && ` · 第 ${c.page} 页`}{c.source_level === "abstract" && " · 仅摘要"}</div>)}{m.metadata?.report_id && <div className="paper-links"><a href={`/api/reports/${m.metadata.report_id}/markdown`}>下载报告</a><a href={`/api/reports/${m.metadata.report_id}/evidence.csv`}>下载证据表</a></div>}</article>) : <div className="chat-empty"><span>✳</span><strong>从一个问题开始</strong><p>提出你的研究问题，一起寻找相关证据。</p></div>}</div>{run && <div className="chat-status" role="status">{statusText[run.status] || run.status}{run.stage && (stageText[run.stage] || run.stage) !== (statusText[run.status] || run.status) && ` · ${stageText[run.stage] || run.stage}`}{run.error && <p>{run.error} <Link href="/settings">检查模型设置</Link></p>}{["failed", "interrupted"].includes(run.status) && <button className="button secondary" disabled={!!busy} onClick={() => void act("retry", async () => { const next = await api<Run>(`/runs/${run.id}/retry`, { method: "POST" }); setRun(next); setRuns(previous => [next, ...previous]); })}>重新执行</button>}</div>}<form onSubmit={search}><label htmlFor="query">研究问题</label><textarea id="query" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} placeholder="输入你的研究问题，Shift + Enter 换行" rows={3} /><div className="search-actions"><div className="select-group"><label htmlFor="source">文献范围</label><select id="source" value={source} onChange={e => setSource(e.target.value)}><option value="all">全部来源</option><option value="local">项目文献</option><option value="openalex">OpenAlex</option></select></div><button className="button primary" disabled={!query.trim() || !!busy || !!(run && ["queued", "pending", "running"].includes(run.status))}>发送 <span>↑</span></button></div></form></section>
          <section className="card library-card"><div className="section-kicker">02 / LIBRARY</div><div className="section-heading"><div><h2>项目文献 <span className="count">{papers.length}</span></h2><p>项目中的文献在所有会话间共享。</p></div></div>{papers.length ? <div className="paper-list">{papers.map((paper, i) => <PaperRow key={paper.id ?? i} paper={paper} />)}</div> : <div className="empty-state"><span className="empty-icon">▤</span><strong>还没有文献</strong><span>检索并保存结果，或导入一份 PDF。</span></div>}</section>
        </div><div className="secondary-column">
          <section className="card compact"><div className="section-kicker">CONVERSATION</div><h2>当前会话</h2><label htmlFor="conversation">选择会话</label><select disabled={!!busy} id="conversation" value={conversationId ?? ""} onChange={e => { const id = e.target.value ? Number(e.target.value) : undefined; setRun(runs.find(r => r.conversation_id === id)); setConversationId(id); }}><option value="">新会话</option>{conversations.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select><form onSubmit={createConversation} className="new-conversation"><label htmlFor="conversation-name">新建会话</label><div className="inline-input"><input id="conversation-name" value={conversationName} onChange={e => setConversationName(e.target.value)} placeholder="输入会话名称" /><button aria-label="创建会话" disabled={!conversationName.trim() || !!busy}>＋</button></div></form></section>
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
