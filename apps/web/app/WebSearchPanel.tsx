"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";

type WebResult = { title: string; url: string; content: string; provider: string; source_level: string; retrieved_at: string };
export const webSourceLabel = (level?: string) => ({ web_snippet: "网页搜索摘要", web_content: "网页正文节选", abstract: "仅摘要" })[level || ""] || "网页信息";

export default function WebSearchPanel({ projectId }: { projectId: number }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<WebResult[]>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const search = async (event: FormEvent) => {
    event.preventDefault(); if (!query.trim()) return;
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setBusy(true); setError(""); setResults(undefined);
    try {
      const response = await fetch(`/api/projects/${projectId}/web-search?q=${encodeURIComponent(query.trim())}`, { signal: controller.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "网页搜索失败，请重试。");
      if (!controller.signal.aborted) setResults(data.results);
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "网页搜索失败。"); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <div>
    <form className="history-form" onSubmit={search}><label htmlFor="web-query">搜索网页</label><div><input id="web-query" maxLength={2000} value={query} onChange={e => { request.current?.abort(); setBusy(false); setResults(undefined); setError(""); setQuery(e.target.value); }} placeholder="输入关键词" /><button className="button primary" disabled={busy || !query.trim()}>{busy ? "搜索中…" : "搜索"}</button></div></form>
    <p className="metadata">使用已保存的搜索引擎，结果不会自动加入项目文献。<Link href="/settings">配置搜索引擎 ↗</Link></p>
    {error && <p className="settings-alert error" role="alert">{error}</p>}
    {results?.length === 0 && <p role="status">没有找到匹配网页，请尝试其他关键词。</p>}
    <div className="paper-list">{results?.map(result => <article className="paper-row" key={result.url}><div className="paper-body"><h3><a href={result.url} target="_blank" rel="noreferrer">{result.title} ↗</a></h3><p className="metadata">{result.provider} · {webSourceLabel(result.source_level)} · 检索于 {new Date(result.retrieved_at).toLocaleString("zh-CN")}</p><p className="abstract">{result.content}</p></div></article>)}</div>
  </div>;
}
