"use client";

import { FormEvent, useEffect, useState } from "react";

type Source = "environment" | "local" | "none" | "default";
type Provider = { id: string; name: string; requires_key: boolean; key_configured: boolean; key_source: Source };
type SearchSettings = { engine: string; engine_source: Source; providers: Provider[]; searx_base_url: string | null; searx_base_url_source: Source; wikipedia_lang: string; wikipedia_lang_source: Source };
const sources: Record<Source, string> = { environment: "环境变量", local: "本地设置", none: "未配置", default: "默认" };
const keyFields: Record<string, string> = { tavily: "tavily_api_key", infoquest: "infoquest_api_key", brave_search: "brave_search_api_key", serper: "serper_api_key" };

export default function WebSearchSettings() {
  const [settings, setSettings] = useState<SearchSettings>();
  const [engine, setEngine] = useState("duckduckgo");
  const [key, setKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [url, setUrl] = useState("");
  const [language, setLanguage] = useState("zh");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const apply = (data: SearchSettings) => { setSettings(data); setEngine(data.engine); setUrl(data.searx_base_url || ""); setLanguage(data.wikipedia_lang); };
  useEffect(() => {
    const controller = new AbortController();
    setLoadError("");
    fetch("/api/settings", { signal: controller.signal }).then(async r => {
      if (!r.ok) throw new Error("无法读取网页搜索设置。");
      const data = await r.json();
      if (!controller.signal.aborted) apply(data.web_search);
    }).catch(e => { if (!controller.signal.aborted) setLoadError(e instanceof Error ? e.message : "无法读取网页搜索设置。"); });
    return () => controller.abort();
  }, [reload]);
  const selected = settings?.providers.find(p => p.id === engine);
  const save = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    const body: Record<string, string | boolean> = {};
    if (settings?.engine_source !== "environment") body.web_search_engine = engine;
    if (selected?.requires_key && keyFields[engine]) {
      if (selected.key_source !== "environment") body[keyFields[engine]] = key;
      body[`clear_${keyFields[engine]}`] = clearKey;
    }
    if (engine === "searx" && settings?.searx_base_url_source !== "environment") body.searx_base_url = url;
    if (engine === "wikipedia" && settings?.wikipedia_lang_source !== "environment") body.wikipedia_lang = language;
    try {
      const response = await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "保存失败，请检查设置。");
      apply(data.web_search); setKey(""); setClearKey(false); setNotice("网页搜索设置已保存。");
    } catch (e) { setError(e instanceof Error ? e.message : "保存失败，请重试。"); }
    finally { setBusy(false); }
  };
  return <form className="settings-form web-search-settings" onSubmit={save}>
    <section className="card settings-card">
      <div className="section-kicker">WEB SEARCH</div><h2>网页搜索</h2>
      <p>深度研究可按研究计划使用已保存的网页搜索引擎；快速探索不进行检索。搜索服务配置在这里保存。</p>
      {loadError && <p className="settings-alert error" role="alert">{loadError} <button className="model-action" type="button" onClick={() => setReload(value => value + 1)}>重试读取</button></p>}
      {error && <p className="settings-alert error" role="alert">{error}</p>}
      {notice && <p className="settings-alert" role="status">{notice}</p>}
      {!settings ? loadError ? null : <p role="status">正在读取配置…</p> : <>
        <label htmlFor="web-engine">搜索引擎</label>
        <select id="web-engine" disabled={busy || settings.engine_source === "environment"} value={engine} onChange={e => { setEngine(e.target.value); setKey(""); setClearKey(false); setNotice(""); }}>
          {settings.providers.map(p => <option key={p.id} value={p.id}>{p.name}{p.requires_key ? " · 需要密钥" : p.id === "searx" ? " · 需要实例地址" : " · 无需密钥"}</option>)}
        </select><small>当前来源：{sources[settings.engine_source]}</small>
        {selected?.requires_key && <>
          <label htmlFor="web-key">{selected.name} API 密钥</label>
          <input id="web-key" type="password" autoComplete="new-password" disabled={busy || clearKey || selected.key_source === "environment"} value={key} onChange={e => setKey(e.target.value)} placeholder={selected.key_configured ? "已配置，留空保持不变" : "输入密钥"} />
          <small>{selected.key_configured ? `已配置 · ${sources[selected.key_source]}` : "未配置"}</small>
          <label className="check-label"><input type="checkbox" checked={clearKey} disabled={busy || selected.key_source === "environment"} onChange={e => setClearKey(e.target.checked)} />删除此引擎的本地密钥</label>
        </>}
        {engine === "searx" && <><label htmlFor="searx-url">Searx / SearXNG 实例地址</label><input id="searx-url" type="url" required value={url} disabled={busy || settings.searx_base_url_source === "environment"} onChange={e => setUrl(e.target.value)} placeholder="https://search.example.org" /><small>实例需启用 JSON 搜索格式。来源：{sources[settings.searx_base_url_source]}</small></>}
        {engine === "wikipedia" && <><label htmlFor="wiki-language">Wikipedia 语言</label><input id="wiki-language" required value={language} disabled={busy || settings.wikipedia_lang_source === "environment"} onChange={e => setLanguage(e.target.value)} placeholder="zh / en" /><small>来源：{sources[settings.wikipedia_lang_source]}</small></>}
        {engine === "duckduckgo" && <small>无需 API 密钥；可用性受网络连接和搜索服务限流影响。</small>}
        {engine === "arxiv" && <small>搜索 arXiv 论文摘要，不代表已读取论文全文。</small>}
        <div><button className="button primary" disabled={busy}>{busy ? "保存中…" : "保存网页搜索设置"}</button></div>
      </>}
    </section>
  </form>;
}
