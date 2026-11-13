"use client";

import { type ChangeEvent, type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import ModelControls from "../ModelControls";
import { api } from "../workspace/api";
import type { WorkspacePreferences } from "../workspace/preferences";
import WebSearchSettings from "./WebSearchSettings";
import RuntimeLogsSettings from "./RuntimeLogsSettings";
import styles from "./SettingsPanel.module.css";

type SettingsPanelProps = {
  onClose: () => void;
  preferences: WorkspacePreferences;
  onPreferencesChange: (next: WorkspacePreferences) => void;
  onNotice: (message: string) => void;
};

type PanelTab = "general" | "research" | "appearance" | "services" | "search" | "files";
const tabs: { id: PanelTab; label: string }[] = [
  { id: "general", label: "通用" },
  { id: "research", label: "研究偏好" },
  { id: "appearance", label: "外观" },
  { id: "services", label: "服务与模型" },
  { id: "search", label: "网页搜索" },
  { id: "files", label: "本地文件" },
];

export default function SettingsPanel({
  onClose,
  preferences,
  onPreferencesChange,
  onNotice,
}: SettingsPanelProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const previousFocus = useRef<HTMLElement | null>(null);
  const directoryInput = useRef<HTMLInputElement>(null);
  const [activeTab, setActiveTab] = useState<PanelTab>("general");
  const [notice, setNotice] = useState("");
  const [fileNames, setFileNames] = useState<string[]>([]);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!element.open) element.showModal();
    return () => {
      if (element.open) element.close();
      const target = previousFocus.current;
      if (target?.isConnected) requestAnimationFrame(() => target.focus());
    };
  }, []);

  const notify = (message: string) => {
    setNotice(message);
    onNotice(message);
  };

  const selectTab = (tab: PanelTab) => {
    setActiveTab(tab);
    setNotice("");
  };

  const chooseDirectory = (event: ChangeEvent<HTMLInputElement>) => {
    const names = Array.from(event.currentTarget.files ?? [])
      .map(file => file.webkitRelativePath || file.name)
      .sort((left, right) => left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" }));
    setFileNames(names);
  };

  const updatePreference = <K extends keyof WorkspacePreferences>(key: K, value: WorkspacePreferences[K]) => {
    onPreferencesChange({ ...preferences, [key]: value });
  };

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    const tab = tabs[next];
    setActiveTab(tab.id);
    tabButtons.current[next]?.focus();
    if (tab.id === "files") selectTab("files");
  };

  const currentTab = tabs.find(tab => tab.id === activeTab)!;
  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="settings-title"
      onCancel={event => { event.preventDefault(); onClose(); }}
      onClick={event => { if (event.target === dialog.current) onClose(); }}
    >
      <div className={styles.frame}>
        <header className={styles.header}>
          <div>
            <h1 id="settings-title">设置</h1>
          </div>
          <button className={styles.closeButton} type="button" aria-label="关闭设置" onClick={onClose}>×</button>
        </header>

        <div className={styles.layout}>
          <div className={styles.sidebar}>
            <nav className={styles.tabs} role="tablist" aria-label="设置类别" aria-orientation="vertical">
              {tabs.map((tab, index) => (
                <button
                  key={tab.id}
                  ref={element => { tabButtons.current[index] = element; }}
                  id={`settings-tab-${tab.id}`}
                  className={`${styles.tab} ${activeTab === tab.id ? styles.activeTab : ""}`}
                  type="button"
                  role="tab"
                  aria-selected={activeTab === tab.id}
                  aria-controls="settings-content"
                  tabIndex={activeTab === tab.id ? 0 : -1}
                  onClick={() => selectTab(tab.id)}
                  onKeyDown={event => handleTabKeyDown(event, index)}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
            <a className={styles.aboutLink} href="https://github.com/iJA774/Tufa-Scholar" target="_blank" rel="noopener noreferrer">
              关于 <span aria-hidden="true">↗</span>
            </a>
          </div>

          <main
            className={styles.content}
            id="settings-content"
            role="tabpanel"
            aria-labelledby={`settings-tab-${activeTab}`}
            tabIndex={0}
          >
          {notice && <p className={styles.notice} role="status">{notice}</p>}

          {activeTab === "general" && <div className={styles.sectionStack}>
            <section className={styles.card}>
              <h2>新问题的默认模式</h2>
              <p>可在每次提问时切换。快速探索由分流模型直接回答；深度研究会检索并整理证据。</p>
              <div className={styles.modeChoices} role="radiogroup" aria-label="新问题的默认模式">
                <label className={preferences.mode === "quick" ? styles.choiceSelected : styles.choice}>
                  <input type="radio" name="default-mode" value="quick" checked={preferences.mode === "quick"} onChange={() => updatePreference("mode", "quick")} />
                  <span><strong>快速探索</strong><small>直接回答，不启动检索。</small></span>
                </label>
                <label className={preferences.mode === "deep" ? styles.choiceSelected : styles.choice}>
                  <input type="radio" name="default-mode" value="deep" checked={preferences.mode === "deep"} onChange={() => updatePreference("mode", "deep")} />
                  <span><strong>深度研究</strong><small>检索、分析并生成报告。</small></span>
                </label>
              </div>
              <label className={styles.toggleRow}>
                <span><strong>Enter 发送消息</strong><small>关闭后，按 Enter 换行；使用 Ctrl+Enter 或发送按钮提交。</small></span>
                <input type="checkbox" checked={preferences.enterToSend} onChange={event => updatePreference("enterToSend", event.target.checked)} />
              </label>
            </section>

            <section className={styles.card}>
              <h2>本次提问模型</h2>
              <p>选择会随下一次问题提交；它不会更改保存的服务地址、密钥或其他已提交任务。</p>
              <ModelControls value={preferences.modelChoice} onChange={value => updatePreference("modelChoice", value)} disabled={false} />
            </section>
          </div>}

          {activeTab === "research" && <section className={styles.card}>
            <h2>研究偏好</h2>
            <p>以下偏好控件暂未接入后端，不会影响检索或研究预算。当前生效的模式和本次模型请在“通用”中设置。</p>
            <label className={styles.placeholderRow}>
              <span><strong>搜索深度</strong><small>暂不生效 · 深度研究的预算由工作流固定控制。</small></span>
              <input type="range" min="3" max="12" defaultValue="6" disabled aria-label="搜索深度，暂不生效" />
            </label>
            <label className={styles.toggleRow}>
              <span><strong>优先展示可追溯证据</strong><small>暂不生效 · 报告引用仍由当前证据校验规则处理。</small></span>
              <input type="checkbox" defaultChecked disabled aria-label="优先展示可追溯证据，暂不生效" />
            </label>
            <label className={styles.toggleRow}>
              <span><strong>相关性过滤</strong><small>暂不生效 · 该选项不会更改候选筛选。</small></span>
              <input type="checkbox" defaultChecked disabled aria-label="相关性过滤，暂不生效" />
            </label>
            <label className={styles.placeholderRow}>
              <span><strong>报告格式</strong><small>暂不生效 · 终稿按当前工作流生成。</small></span>
              <select defaultValue="academic" disabled aria-label="报告格式，暂不生效"><option value="academic">学术综述</option><option value="structured">结构化报告</option><option value="brief">研究摘要</option></select>
            </label>
          </section>}

          {activeTab === "appearance" && <section className={styles.card}>
            <h2>外观</h2>
            <p>这些偏好保存在此浏览器中，并立即应用到研究工作台。</p>
            <label className={styles.toggleRow}>
              <span><strong>环境光效</strong><small>显示工作台的柔和边缘光。</small></span>
              <input type="checkbox" checked={preferences.glow} onChange={event => updatePreference("glow", event.target.checked)} />
            </label>
            <label className={styles.toggleRow}>
              <span><strong>背景网格</strong><small>显示低对比度的工作区网格。</small></span>
              <input type="checkbox" checked={preferences.grid} onChange={event => updatePreference("grid", event.target.checked)} />
            </label>
            <fieldset className={styles.densityGroup}>
              <legend>内容密度</legend>
              <label className={preferences.density === "default" ? styles.choiceSelected : styles.choice}>
                <input type="radio" name="density" value="default" checked={preferences.density === "default"} onChange={() => updatePreference("density", "default")} />
                <span><strong>标准</strong><small>保留默认阅读间距。</small></span>
              </label>
              <label className={preferences.density === "compact" ? styles.choiceSelected : styles.choice}>
                <input type="radio" name="density" value="compact" checked={preferences.density === "compact"} onChange={() => updatePreference("density", "compact")} />
                <span><strong>紧凑</strong><small>减少列表与面板留白。</small></span>
              </label>
            </fieldset>
          </section>}

          {activeTab === "services" && <div className={styles.sectionStack}>
            <ApiSettings />
            <RuntimeLogsSettings />
          </div>}

          {activeTab === "search" && <WebSearchSettings />}

          {activeTab === "files" && <section className={styles.card}>
            <h2>本地文件</h2>
            <p>浏览器需支持文件夹选择；仅列出文件路径，不读取或上传内容。刷新后需重新选择。</p>
            <input
              ref={element => {
                directoryInput.current = element;
                if (element) element.webkitdirectory = true;
              }}
              className={styles.directoryInput}
              type="file"
              multiple
              aria-label="选择本地目录"
              onChange={chooseDirectory}
            />
            <button className={styles.actionButton} type="button" onClick={() => directoryInput.current?.click()}>
              打开目录
            </button>
            {fileNames.length > 0
              ? <ul className={styles.fileList} aria-label="目录文件名">
                {fileNames.map(name => <li key={name}>{name}</li>)}
              </ul>
              : <p className={styles.fileEmpty} role="status">尚未选择目录。</p>}
          </section>}
          </main>
        </div>
        <footer className={styles.footer}>
          <span>{currentTab.label}</span>
          <button className={styles.doneButton} type="button" onClick={onClose}>完成</button>
        </footer>
      </div>
    </dialog>
  );
}

type Source = "environment" | "local" | "none";
type PublicSettings = {
  openalex: { key_configured: boolean; key_source: Source };
  model: {
    base_url: string | null;
    base_url_source: Source;
    model: string | null;
    model_source: Source;
    key_configured: boolean;
    key_source: Source;
  };
};
const sourceLabel: Record<Source, string> = { environment: "环境变量", local: "本地设置", none: "未配置" };
const originOf = (value: string) => { try { return new URL(value).origin; } catch { return null; } };

function ApiSettings() {
  const [settings, setSettings] = useState<PublicSettings>();
  const [openAlexKey, setOpenAlexKey] = useState("");
  const [modelKey, setModelKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const keyOrigin = useRef<string | null>(null);
  const [clearOpenAlex, setClearOpenAlex] = useState(false);
  const [clearModelKey, setClearModelKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loadVersion, setLoadVersion] = useState(0);
  const mounted = useRef(false);
  const saveRequest = useRef<AbortController | null>(null);

  const apply = useCallback((data: PublicSettings) => {
    setSettings(data);
    setBaseUrl(data.model.base_url || "");
    setDefaultModel(data.model.model || "");
  }, []);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    setLoadError("");
    api<PublicSettings>("/settings", { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted && mounted.current) apply(data); })
      .catch(reason => { if (!controller.signal.aborted && mounted.current) setLoadError(reason instanceof Error ? reason.message : "无法读取设置。"); });
    return () => {
      mounted.current = false;
      controller.abort();
      saveRequest.current?.abort();
    };
  }, [apply, loadVersion]);

  const save = async () => {
    if (!settings || busy || saveRequest.current) return;
    const controller = new AbortController();
    saveRequest.current = controller;
    setBusy(true); setError(""); setNotice("");
    try {
      const data = await api<PublicSettings>("/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          openalex_api_key: settings.openalex.key_source === "environment" ? null : openAlexKey,
          clear_openalex_api_key: settings.openalex.key_source !== "environment" && clearOpenAlex,
          openai_api_key: settings.model.key_source === "environment" ? null : modelKey,
          clear_openai_api_key: settings.model.key_source !== "environment" && clearModelKey,
          openai_base_url: settings.model.base_url_source === "environment" ? null : baseUrl,
          openai_model: settings.model.model_source === "environment" ? null : defaultModel,
        }),
      });
      if (!mounted.current || controller.signal.aborted) return;
      apply(data);
      setOpenAlexKey(""); setModelKey(""); keyOrigin.current = null;
      setClearOpenAlex(false); setClearModelKey(false);
      setNotice("服务设置已保存。");
    } catch (reason) {
      if (mounted.current && !controller.signal.aborted) setError(reason instanceof Error ? reason.message : "保存失败，请检查设置后重试。");
    } finally {
      if (saveRequest.current === controller) saveRequest.current = null;
      if (mounted.current && !controller.signal.aborted) setBusy(false);
    }
  };

  return <section className={`card settings-card ${styles.card}`} aria-busy={busy}>
    <h2>模型与论文服务</h2>
    <p>模型和 OpenAlex 密钥只在显式保存后写入本机设置。已保存的密钥不会回显；环境变量来源只读。</p>
    {loadError && <p className={styles.error} role="alert">{loadError}</p>}
    {error && <p className={styles.error} role="alert">{error}</p>}
    {notice && <p className={styles.notice} role="status">{notice}</p>}
    {!settings ? loadError ? <button className={styles.actionButton} type="button" onClick={() => setLoadVersion(value => value + 1)}>重新读取设置</button> : <p className={styles.loading} role="status">正在读取配置…</p> : <div className={styles.formGrid}>
      <label htmlFor="settings-openalex-key">OpenAlex API 密钥</label>
      <input
        id="settings-openalex-key"
        type="password"
        autoComplete="new-password"
        value={openAlexKey}
        onChange={event => setOpenAlexKey(event.target.value)}
        placeholder={settings.openalex.key_configured ? "已配置，留空保持不变" : "输入密钥"}
        disabled={busy || clearOpenAlex || settings.openalex.key_source === "environment"}
      />
      <small>{settings.openalex.key_configured ? `已配置 · ${sourceLabel[settings.openalex.key_source]}` : "未配置"}</small>
      <label className={styles.inlineCheck}>
        <input type="checkbox" checked={clearOpenAlex} disabled={busy || settings.openalex.key_source === "environment"} onChange={event => setClearOpenAlex(event.target.checked)} />
        删除本地保存的 OpenAlex 密钥
      </label>

      <label htmlFor="settings-model-url">模型服务 Base URL</label>
      <input
        id="settings-model-url"
        type="url"
        value={baseUrl}
        placeholder="https://.../v1"
        disabled={busy || settings.model.base_url_source === "environment" || (!!settings.model.base_url && settings.model.key_source === "environment")}
        onChange={event => {
          const next = event.target.value;
          const nextOrigin = originOf(next);
          if (nextOrigin && keyOrigin.current && nextOrigin !== keyOrigin.current) {
            setModelKey("");
            keyOrigin.current = null;
          }
          setBaseUrl(next);
        }}
      />
      <small>来源：{sourceLabel[settings.model.base_url_source]}{settings.model.base_url_source === "environment" ? " · 由环境变量锁定" : settings.model.base_url && settings.model.key_source === "environment" ? " · 环境变量密钥锁定当前服务来源" : ""}。更换服务来源时需重新输入该来源的密钥。</small>

      <label htmlFor="settings-default-model">服务默认模型</label>
      <input
        id="settings-default-model"
        value={defaultModel}
        placeholder="例如供应商提供的模型 ID"
        disabled={busy || settings.model.model_source === "environment"}
        onChange={event => setDefaultModel(event.target.value)}
      />
      <small>来源：{sourceLabel[settings.model.model_source]}{settings.model.model_source === "environment" ? " · 由环境变量锁定" : ""}。通用页中的“本次提问模型”可单独覆盖本次提交。</small>

      <label htmlFor="settings-model-key">模型服务 API 密钥</label>
      <input
        id="settings-model-key"
        type="password"
        autoComplete="new-password"
        value={modelKey}
        placeholder={settings.model.key_configured ? "已配置，留空保持不变" : "输入密钥"}
        disabled={busy || clearModelKey || settings.model.key_source === "environment"}
        onChange={event => { setModelKey(event.target.value); keyOrigin.current = originOf(baseUrl); }}
      />
      <small>{settings.model.key_configured ? `已配置 · ${sourceLabel[settings.model.key_source]}` : "未配置"}</small>
      <label className={styles.inlineCheck}>
        <input type="checkbox" checked={clearModelKey} disabled={busy || settings.model.key_source === "environment"} onChange={event => setClearModelKey(event.target.checked)} />
        删除本地保存的模型密钥
      </label>
      <div className={styles.formActions}>
        <button className={styles.actionButton} type="button" disabled={busy || !settings} onClick={() => void save()}>{busy ? "保存中…" : "保存服务设置"}</button>
      </div>
    </div>}
  </section>;
}
