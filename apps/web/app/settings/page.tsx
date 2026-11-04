"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

type Source = "environment" | "local" | "none";
type Settings = { openalex: { key_configured: boolean; key_source: Source }; model: { base_url: string | null; base_url_source: Source; model: string | null; model_source: Source; key_configured: boolean; key_source: Source } };
const sourceText: Record<Source, string> = { environment: "环境变量", local: "本地设置", none: "未配置" };

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings>();
  const [openalexKey, setOpenalexKey] = useState("");
  const [modelKey, setModelKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [clearOpenalex, setClearOpenalex] = useState(false);
  const [clearModel, setClearModel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const apply = (data: Settings) => { setSettings(data); setBaseUrl(data.model.base_url || ""); setModel(data.model.model || ""); };
  useEffect(() => { let current = true; fetch("/api/settings").then(response => { if (!response.ok) throw new Error(); return response.json() as Promise<Settings>; }).then(data => { if (current) apply(data); }).catch(() => { if (current) setError("无法读取设置，请检查服务后重试。"); }); return () => { current = false; }; }, []);
  const save = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ openalex_api_key: openalexKey, openai_api_key: modelKey, clear_openalex_api_key: clearOpenalex, clear_openai_api_key: clearModel, openai_base_url: settings?.model.base_url_source === "environment" && baseUrl === settings.model.base_url ? null : baseUrl, openai_model: settings?.model.model_source === "environment" && model === settings.model.model ? null : model }) });
      if (!response.ok) throw new Error();
      apply(await response.json() as Settings); setNotice("设置已保存。");
    } catch { setError("保存失败，请检查设置后重试。"); }
    finally { setOpenalexKey(""); setModelKey(""); setClearOpenalex(false); setClearModel(false); setBusy(false); }
  };
  return <div className="shell"><aside className="sidebar"><div className="brand"><span className="brand-mark">✳</span><div><strong>TUFA</strong><small>SCHOLAR</small></div></div><nav className="settings-nav" aria-label="导航"><Link href="/">← 返回研究空间</Link><span aria-current="page">⚙　设置</span></nav><div className="side-bottom"><div className="side-foot"><span className="status-dot" /> 本地研究空间</div></div></aside><main className="main"><header className="topbar"><span>研究工作台 <span className="slash">/</span> 设置</span><span className="top-label">SETTINGS</span></header><div className="workspace settings-page"><div className="page-heading"><div><div className="eyebrow">YOUR PREFERENCES</div><h1>设置</h1><p>管理检索与模型配置。</p></div></div>{error && <div className="settings-alert error" role="alert">{error}</div>}{notice && <div className="settings-alert" role="status">{notice}</div>}{settings && <form onSubmit={save} className="settings-form"><section className="card settings-card"><div className="section-kicker">LITERATURE SEARCH</div><h2>OpenAlex</h2><p>用于文献检索的 API 密钥。</p><label htmlFor="openalex-key">API 密钥</label><input id="openalex-key" type="password" autoComplete="new-password" value={openalexKey} onChange={e => setOpenalexKey(e.target.value)} placeholder={settings.openalex.key_configured ? "已配置，留空保持不变" : "输入密钥"} disabled={clearOpenalex} /><small>{settings.openalex.key_configured ? `已配置 · ${sourceText[settings.openalex.key_source]}` : "未配置"}{settings.openalex.key_source === "environment" && "（优先使用）"}</small><label className="check-label"><input type="checkbox" checked={clearOpenalex} onChange={e => setClearOpenalex(e.target.checked)} /> 删除本地保存的密钥</label></section><section className="card settings-card"><div className="section-kicker">MODEL</div><h2>模型配置</h2><p>配置用于科研问答的 OpenAI 兼容服务。</p><label htmlFor="base-url">Base URL</label><input id="base-url" type="url" value={baseUrl} onChange={e => setBaseUrl(e.target.value)} placeholder="https://.../v1" /><small>当前来源：{sourceText[settings.model.base_url_source]}{settings.model.base_url_source === "environment" && "（优先使用）"}</small><label htmlFor="model-name">模型名称</label><input id="model-name" value={model} onChange={e => setModel(e.target.value)} placeholder="模型名称" /><small>当前来源：{sourceText[settings.model.model_source]}{settings.model.model_source === "environment" && "（优先使用）"}</small><label htmlFor="model-key">API 密钥</label><input id="model-key" type="password" autoComplete="new-password" value={modelKey} onChange={e => setModelKey(e.target.value)} placeholder={settings.model.key_configured ? "已配置，留空保持不变" : "输入密钥"} disabled={clearModel} /><small>{settings.model.key_configured ? `已配置 · ${sourceText[settings.model.key_source]}` : "未配置"}{settings.model.key_source === "environment" && "（优先使用）"}</small><label className="check-label"><input type="checkbox" checked={clearModel} onChange={e => setClearModel(e.target.checked)} /> 删除本地保存的密钥</label></section><div><button className="button primary" disabled={busy}>{busy ? "保存中…" : "保存设置"} <span>↗</span></button></div></form>}</div></main></div>;
}
