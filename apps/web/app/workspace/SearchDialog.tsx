"use client";

import { FormEvent, ReactNode, useEffect, useRef, useState } from "react";
import WebSearchPanel from "../WebSearchPanel";
import PaperRow from "./PaperRow";
import { api, messageOf } from "./api";
import type { Conversation, Paper } from "./types";

export default function SearchDialog({ projectId, onClose, onOpenConversation, renderPaperAction }: {
  projectId: number; onClose: () => void; onOpenConversation: (id: number) => void;
  renderPaperAction: (paper: Paper) => ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const request = useRef<AbortController | null>(null);
  const [tab, setTab] = useState<"papers" | "history" | "web">("papers");
  const [source, setSource] = useState("all");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<{ conversations: Conversation[]; papers: Paper[] }>();
  const [paperHits, setPaperHits] = useState<{ results: Paper[]; warnings: string[] }>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (dialog.current && !dialog.current.open) dialog.current.showModal(); return () => { request.current?.abort(); request.current = null; }; }, []);
  useEffect(() => { request.current?.abort(); setLoading(false); setHits(undefined); setPaperHits(undefined); setError(""); }, [projectId, tab]);
  const find = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim() || loading) return;
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    let timedOut = false;
    const timer = window.setTimeout(() => { timedOut = true; controller.abort(); }, 30000);
    setLoading(true); setError("");
    try {
      const encoded = encodeURIComponent(query.trim());
      if (tab === "history") {
        const result = await api<{ conversations: Conversation[]; papers: Paper[] }>(`/projects/${projectId}/search?q=${encoded}`, { signal: controller.signal });
        if (!controller.signal.aborted) setHits(result);
      } else {
        const result = await api<{ results: Paper[]; warnings: string[] }>(`/projects/${projectId}/paper-search?q=${encoded}&source=${source}`, { signal: controller.signal });
        if (!controller.signal.aborted) setPaperHits(result);
      }
    } catch (e) { if (request.current === controller && (timedOut || !controller.signal.aborted)) setError(timedOut ? "搜索超时，请重试。" : messageOf(e)); }
    finally { window.clearTimeout(timer); if (request.current === controller) { request.current = null; setLoading(false); } }
  };
  const changeQuery = (value: string) => {
    request.current?.abort();
    request.current = null;
    setLoading(false);
    setHits(undefined);
    setPaperHits(undefined);
    setError("");
    setQuery(value);
  };
  return (
    <dialog ref={dialog} className="confirm-dialog history-dialog" aria-labelledby="history-title" onClose={onClose}>
      <div className="section-heading">
        <h2 id="history-title">搜索空间</h2>
        <button className="history-close" aria-label="关闭搜索" onClick={() => dialog.current?.close()}>×</button>
      </div>
      <div className="search-tabs">
        <button className={tab === "papers" ? "selected" : ""} onClick={() => setTab("papers")}>搜索文献</button>
        <button className={tab === "history" ? "selected" : ""} onClick={() => setTab("history")}>历史对话</button>
        <button className={tab === "web" ? "selected" : ""} onClick={() => setTab("web")}>网页搜索</button>
      </div>
      {tab === "web" ? (
        <WebSearchPanel key={projectId} projectId={projectId} />
      ) : (
        <form onSubmit={find} className="history-form">
          <label htmlFor="history-query">{tab === "papers" ? "文献关键词" : "搜索会话与消息"}</label>
          <div>
            <input id="history-query" value={query} onChange={e => changeQuery(e.target.value)} placeholder="输入关键词" />
            {tab === "papers" && (
              <select disabled={loading} aria-label="文献搜索范围" value={source} onChange={e => setSource(e.target.value)}>
                <option value="all">全部来源</option>
                <option value="local">项目文献</option>
                <option value="openalex">OpenAlex</option>
              </select>
            )}
            <button className="button primary" disabled={loading || !query.trim()}>
              {loading ? "搜索中…" : "搜索"}
            </button>
          </div>
        </form>
      )}
      {error && <p role="alert">{error}</p>}
      {tab === "history" && hits && (
        <div className="history-results">
          {!hits.conversations.length && <p>没有找到匹配会话。</p>}
          {hits.conversations.map(c => (
            <button key={c.id} disabled={loading} onClick={() => { onOpenConversation(c.id); dialog.current?.close(); }}>
              会话 · {c.title}
            </button>
          ))}
        </div>
      )}
      {tab === "papers" && paperHits && (
        <div className="history-results">
          {paperHits.warnings?.map((warning, i) => <p className="run-error" key={i}>{warning}</p>)}
          {!paperHits.results.length && (
            <p>{paperHits.warnings?.length ? "外部检索未完成，可重试或改查本地。" : "没有找到匹配文献。"}</p>
          )}
          {paperHits.results.map((paper, i) => (
            <PaperRow key={paper.id ?? i} paper={paper} action={renderPaperAction(paper)} />
          ))}
        </div>
      )}
    </dialog>
  );
}
