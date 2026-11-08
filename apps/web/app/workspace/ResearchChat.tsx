"use client";
import { FormEvent, ReactNode, useEffect, useRef, useState } from "react";
import Link from "next/link";
import ModelControls, { ModelChoice } from "../ModelControls";
import { webSourceLabel } from "../WebSearchPanel";
import { useRunPolling } from "../hooks/useRunPolling";
import { api, json, messageOf } from "./api";
import type { Collection, Conversation, Message, Paper, Run } from "./types";
const statusText: Record<string, string> = { queued: "等待处理", pending: "等待处理", running: "正在处理", succeeded: "回答完成", success: "回答完成", completed: "回答完成", failed: "任务失败", interrupted: "任务已中断" };
const stageText: Record<string, string> = { queued: "等待处理", pending: "等待处理", searching: "检索文献", searching_web: "搜索网页", search: "检索文献", local: "搜索项目文献", openalex: "搜索 OpenAlex", completed: "回答完成", done: "回答完成", retrieving: "检索文献", generating: "生成回答", synthesizing: "整理证据", planning: "规划问题", evidence: "提取证据", assessing: "检查证据缺口", verifying: "核验结论", answering: "生成回答" };
type Props = {
  projectId: number;
  collections: Collection[];
  collectionId: number | undefined;
  onCollectionChange: (id: number | undefined) => void;
  collectionPapers: Paper[];
  folderLoading: boolean;
  onError: (message: string) => void;
  openConversationId?: number;
  library: ReactNode;
  importPanel: ReactNode;
};
export default function ResearchChat({ projectId, collections, collectionId, onCollectionChange, collectionPapers, folderLoading, onError, openConversationId, library, importPanel }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<number>();
  const [conversationName, setConversationName] = useState("");
  const [query, setQuery] = useState("");
  const [modelChoice, setModelChoice] = useState<ModelChoice>({ model_name: "", reasoning_effort: "" });
  const [source, setSource] = useState("auto");
  const [run, setRun] = useState<Run>();
  const [runs, setRuns] = useState<Run[]>([]);
  const [busy, setBusy] = useState("");
  const epoch = useRef(0);
  const currentConversation = useRef<number | undefined>(undefined);
  const selectConversation = (id: number | undefined) => {
    if (currentConversation.current === id) return;
    epoch.current++;
    currentConversation.current = id;
    setConversationId(id);
    setRun(runs.find(r => r.conversation_id === id));
    setMessages([]);
    setBusy("");
  };
  useEffect(() => {
    const marker = ++epoch.current;
    const controller = new AbortController();
    setConversations([]);
    setRuns([]);
    setRun(undefined);
    setMessages([]);
    setConversationId(undefined);
    currentConversation.current = undefined;
    setBusy("");
    Promise.all([api<Conversation[]>(`/projects/${projectId}/conversations`, { signal: controller.signal }), api<Run[]>(`/projects/${projectId}/runs`, { signal: controller.signal })]).then(([cs, rs]) => {
      if (epoch.current !== marker)
        return;
      setConversations(cs);
      setRuns(rs);
      const id = openConversationId ?? cs[0]?.id;
      currentConversation.current = id;
      setConversationId(id);
      setRun(rs.find(r => r.conversation_id === id));
    }).catch(e => {
      if (!controller.signal.aborted && epoch.current === marker)
        onError(messageOf(e));
    });
    return () => { epoch.current++; controller.abort(); };
  }, [projectId]);
  useEffect(() => {
    if (openConversationId !== undefined && conversations.length)
      selectConversation(openConversationId);
  }, [openConversationId]);
  useEffect(() => {
    if (conversationId === undefined)
      return;
    const controller = new AbortController();
    const marker = epoch.current;
    api<Message[]>(`/conversations/${conversationId}/messages`, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted && epoch.current === marker && currentConversation.current === conversationId)
        setMessages(data);
    }).catch(e => {
      if (!controller.signal.aborted && epoch.current === marker)
        onError(messageOf(e));
    });
    return () => controller.abort();
  }, [conversationId, projectId]);
  useEffect(() => {
    if (run?.status !== "succeeded" || conversationId === undefined)
      return;
    const controller = new AbortController();
    const marker = epoch.current;
    api<Message[]>(`/conversations/${conversationId}/messages`, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted && epoch.current === marker && currentConversation.current === conversationId)
        setMessages(data);
    }).catch(e => {
      if (!controller.signal.aborted && epoch.current === marker)
        onError(messageOf(e));
    });
    return () => controller.abort();
  }, [run?.id, run?.status, conversationId, projectId]);
  useRunPolling({
    run, projectId, conversationId,
    fetchRun: (id, signal) => api<Run>(`/runs/${id}`, { signal }),
    onUpdate: next => {
      setRun(next);
      setRuns(previous => previous.map(item => item.id === next.id ? next : item));
    },
    onError: error => onError(messageOf(error)),
  });

  const act = async (key: string, fn: (marker: number) => Promise<void>) => {
    const marker = epoch.current;
    setBusy(key);
    try {
      await fn(marker);
    } catch (error) {
      if (epoch.current === marker) onError(messageOf(error));
    } finally {
      if (epoch.current === marker) setBusy("");
    }
  };

  const createConversation = (event: FormEvent) => {
    event.preventDefault();
    if (!conversationName.trim() || busy) return;
    void act("conversation", async marker => {
      const created = await api<Conversation>(`/projects/${projectId}/conversations`, json({ title: conversationName.trim() }));
      const updated = await api<Conversation[]>(`/projects/${projectId}/conversations`);
      if (marker !== epoch.current) return;
      setConversations(updated);
      setConversationName("");
      selectConversation(created.id);
    });
  };

  const search = (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim() || busy ||
      (source === "collection" && (folderLoading || !collectionId || !collectionPapers.length)) ||
      (run && ["queued", "pending", "running"].includes(run.status))) return;
    void act("search", async marker => {
      const submitted = await api<{ run_id: number; conversation_id: number }>(
        `/projects/${projectId}/questions`, json({
          question: query.trim(),
          model_name: modelChoice.model_name.trim() || undefined,
          reasoning_effort: modelChoice.reasoning_effort || undefined,
          source,
          collection_id: source === "collection" ? collectionId : undefined,
          conversation_id: conversationId ?? null,
        }),
      );
      const [cs, next, rs, ms] = await Promise.all([
        api<Conversation[]>(`/projects/${projectId}/conversations`),
        api<Run>(`/runs/${submitted.run_id}`),
        api<Run[]>(`/projects/${projectId}/runs`),
        api<Message[]>(`/conversations/${submitted.conversation_id}/messages`),
      ]);
      if (marker !== epoch.current) return;
      setQuery("");
      setConversations(cs);
      currentConversation.current = submitted.conversation_id;
      setConversationId(submitted.conversation_id);
      setMessages(ms);
      setRun(next);
      setRuns(rs);
    });
  };
  return <div className="content-grid">
    <div className="primary-column">
      <section className="card chat-card">
        <div className="section-kicker">RESEARCH CONVERSATION</div>
        <div className="section-heading">
          <div>
            <h2>科研问答</h2>
            <p>围绕问题探索文献与证据。</p>
          </div>
        </div>
        <div className="messages">
          {messages.length ? messages.map(m => <article className={`message ${m.role === "user" ? "user-message" : "assistant-message"}`} key={m.id}>
            <div className="message-label">
              {m.role === "user" ? "你" : "TUFA"}
            </div>
            <div className="message-content">
              {m.content}
            </div>
            {m.metadata?.evidence?.map((c, i) => <div className="citation" key={i}>
              {c.version_id ? <a href={`/api/paper-versions/${c.version_id}/file${c.page ? `#page=${c.page}` : ""}`} target="_blank" rel="noreferrer">
                {c.title || `参考文献 ${i + 1}`} ↗</a> : c.original_url ? <a href={c.original_url} target="_blank" rel="noreferrer">
                  {c.title || `参考文献 ${i + 1}`} ↗</a> : <span>
                {c.title || `参考文献 ${i + 1}`}
              </span>}{c.id && ` [${c.id}]`}{c.page && ` · 第 ${c.page} 页`}{c.source_level && ["abstract", "web_snippet", "web_content"].includes(c.source_level) && ` · ${webSourceLabel(c.source_level)}`}{c.provider && ` · ${c.provider}`}{c.retrieved_at && ` · 检索于 ${new Date(c.retrieved_at).toLocaleString("zh-CN")}`}
            </div>)}{m.metadata?.report_id && <div className="paper-links">
              <a href={`/api/reports/${m.metadata.report_id}/markdown`}>下载报告</a>
              <a href={`/api/reports/${m.metadata.report_id}/evidence.csv`}>下载证据表</a>
            </div>}
          </article>) : <div className="chat-empty">
            <span>✳</span>
            <strong>从一个问题开始</strong>
            <p>提出你的研究问题，一起寻找相关证据。</p>
          </div>}
        </div>
        {run && <div className="chat-status" role="status">
          {statusText[run.status] || run.status}{run.stage && (stageText[run.stage] || run.stage) !== (statusText[run.status] || run.status) && ` · ${stageText[run.stage] || run.stage}`}{run.error && <p>
            {run.error} <Link href="/settings">检查服务设置</Link>
          </p>}{["failed", "interrupted"].includes(run.status) && <button className="button secondary" disabled={!!busy} onClick={() => void act("retry", async (marker) => {
            const next = await api<Run>(`/runs/${run.id}/retry`, { method: "POST" }); if (marker !== epoch.current)
              return; setRun(next); setRuns(previous => [next, ...previous]);
          })}>重新执行</button>}
        </div>}
        <form onSubmit={search}>
          <label htmlFor="query">研究问题</label>
          <textarea id="query" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }} placeholder="输入你的研究问题，Shift + Enter 换行" rows={3} />
          <div className="search-actions">
            <div className="select-group">
              <label htmlFor="source">检索范围</label>
              <select disabled={!!busy} id="source" value={source} onChange={e => setSource(e.target.value)}>
                <option value="auto">自动</option>
                <option value="all">优先论文（本地和 OpenAlex）</option>
                <option value="local">仅项目文献</option>
                <option value="openalex">优先 OpenAlex</option>
                <option value="web">优先网页搜索</option>
                <option value="collection">收藏夹</option>
              </select>
            </div>
            <ModelControls value={modelChoice} onChange={setModelChoice} disabled={!!busy} />
            {source === "collection" && <select disabled={!!busy || folderLoading} aria-label="选择问答收藏夹" value={collectionId ?? ""} onChange={e => onCollectionChange(e.target.value ? Number(e.target.value) : undefined)}>
              <option value="">选择收藏夹</option>
              {collections.map(c => <option key={c.id} value={c.id}>
                {c.name}
              </option>)}
            </select>}
            <button className="button primary" disabled={(source === "collection" && (folderLoading || !collectionId || !collectionPapers.length)) || !query.trim() || !!busy || !!(run && ["queued", "pending", "running"].includes(run.status))}>发送 <span>↑</span>
            </button>
          </div>
          {source === "web" && <p className="collection-hint">使用设置页中的搜索引擎，必要时可补充论文。网页证据不等同于论文全文。</p>}{["all", "openalex"].includes(source) && <p className="collection-hint">必要时可补充网页资料。</p>}{source === "collection" && (!collectionId || !collectionPapers.length) && <p className="collection-hint">
            {!collectionId ? "请选择收藏夹。" : "此收藏夹为空，请先加入文献。"}
          </p>}
        </form>
      </section>
      {library}
    </div>
    <div className="secondary-column">
      <section className="card compact">
        <div className="section-kicker">CONVERSATION</div>
        <h2>当前会话</h2>
        <label htmlFor="conversation">选择会话</label>
        <select disabled={!!busy} id="conversation" value={conversationId ?? ""} onChange={e => { const id = e.target.value ? Number(e.target.value) : undefined; selectConversation(id); }}>
          <option value="">新会话</option>
          {conversations.map(c => <option key={c.id} value={c.id}>
            {c.title}
          </option>)}
        </select>
        <form onSubmit={createConversation} className="new-conversation">
          <label htmlFor="conversation-name">新建会话</label>
          <div className="inline-input">
            <input id="conversation-name" value={conversationName} onChange={e => setConversationName(e.target.value)} placeholder="输入会话名称" />
            <button aria-label="创建会话" disabled={!conversationName.trim() || !!busy}>＋</button>
          </div>
        </form>
      </section>
      {importPanel}
    </div>
  </div>;
}
