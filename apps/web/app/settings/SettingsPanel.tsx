"use client";

import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import ModelControls from "../ModelControls";
import PdfImport from "../workspace/PdfImport";
import PaperLibrary from "../workspace/PaperLibrary";
import { api } from "../workspace/api";
import type { Paper } from "../workspace/types";
import type { WorkspacePreferences } from "../workspace/preferences";
import WebSearchSettings from "./WebSearchSettings";
import RuntimeLogsSettings from "./RuntimeLogsSettings";
import styles from "./SettingsPanel.module.css";

type SettingsPanelProps = {
  onClose: () => void;
  projectId?: number;
  projectName?: string;
  conversationId?: number;
  preferences: WorkspacePreferences;
  onPreferencesChange: (next: WorkspacePreferences) => void;
  ensureProject: () => Promise<number>;
  onNotice: (message: string) => void;
};

type PanelTab = "general" | "research" | "appearance" | "services" | "search" | "files" | "about";
const tabs: { id: PanelTab; label: string }[] = [
  { id: "general", label: "通用" },
  { id: "research", label: "研究偏好" },
  { id: "appearance", label: "外观" },
  { id: "services", label: "服务与模型" },
  { id: "search", label: "网页搜索" },
  { id: "files", label: "本地文件" },
  { id: "about", label: "关于" },
];

export default function SettingsPanel({
  onClose,
  projectId,
  projectName,
  conversationId,
  preferences,
  onPreferencesChange,
  ensureProject,
  onNotice,
}: SettingsPanelProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const previousFocus = useRef<HTMLElement | null>(null);
  const mounted = useRef(false);
  const ensureProjectRef = useRef(ensureProject);
  const pendingProject = useRef<Promise<number> | null>(null);
  const fileRequest = useRef<AbortController | null>(null);
  const [activeTab, setActiveTab] = useState<PanelTab>("general");
  const [notice, setNotice] = useState("");
  const [fileProjectId, setFileProjectId] = useState<number | undefined>(projectId);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [papersScopeId, setPapersScopeId] = useState<number | undefined>();
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState("");

  ensureProjectRef.current = ensureProject;
  const fileScopeId = projectId ?? fileProjectId;
  const fileScopeRef = useRef<number | undefined>(fileScopeId);
  fileScopeRef.current = fileScopeId;

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    mounted.current = true;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!element.open) element.showModal();
    return () => {
      mounted.current = false;
      fileRequest.current?.abort();
      if (element.open) element.close();
      const target = previousFocus.current;
      if (target?.isConnected) requestAnimationFrame(() => target.focus());
    };
  }, []);

  useEffect(() => {
    setFileProjectId(projectId);
    setPapers([]);
    setFilesError("");
  }, [projectId]);

  const refreshFiles = useCallback(async (scopeId: number) => {
    fileRequest.current?.abort();
    const controller = new AbortController();
    fileRequest.current = controller;
    setFilesLoading(true);
    setFilesError("");
    try {
      const data = await api<Paper[]>(`/projects/${scopeId}/papers`, { signal: controller.signal });
      if (!controller.signal.aborted && fileScopeRef.current === scopeId) {
        setPapers(data);
        setPapersScopeId(scopeId);
      }
    } catch (reason) {
      if (!controller.signal.aborted && fileScopeRef.current === scopeId) setFilesError(reason instanceof Error ? reason.message : "无法读取本地文件。");
    } finally {
      if (fileRequest.current === controller) fileRequest.current = null;
      if (!controller.signal.aborted) setFilesLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab !== "files" || !fileScopeId) return;
    void refreshFiles(fileScopeId);
    return () => fileRequest.current?.abort();
  }, [activeTab, fileScopeId, refreshFiles]);

  const notify = (message: string) => {
    setNotice(message);
    onNotice(message);
  };

  const selectTab = (tab: PanelTab) => {
    setActiveTab(tab);
    setNotice("");
    if (tab !== "files" || projectId || fileProjectId) return;
    setFilesLoading(true);
    setFilesError("");
    if (!pendingProject.current) pendingProject.current = Promise.resolve().then(() => ensureProjectRef.current());
    void pendingProject.current.then(id => {
      pendingProject.current = null;
      if (mounted.current) setFileProjectId(id);
    }).catch(reason => {
      pendingProject.current = null;
      if (!mounted.current) return;
      setFilesLoading(false);
      setFilesError(reason instanceof Error ? reason.message : "无法准备本地文件空间。");
    });
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
  const scopeTitle = projectName ? `“${projectName}”` : "当前研究空间";
  const visiblePapers = papersScopeId === fileScopeId ? papers : [];
  const retryFiles = () => {
    if (fileScopeId) {
      void refreshFiles(fileScopeId);
      return;
    }
    selectTab("files");
  };

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="settings-title"
      onCancel={event => { event.preventDefault(); onClose(); }}
    >
      <div className={styles.frame}>
        <header className={styles.header}>
          <div>
            <div className={styles.eyebrow}>TUFA SCHOLAR / PREFERENCES</div>
            <h1 id="settings-title">设置</h1>
          </div>
          <button className={styles.closeButton} type="button" aria-label="关闭设置" onClick={onClose}>×</button>
        </header>

        <div className={styles.layout}>
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
              <div className={styles.sectionLabel}>DEFAULTS</div>
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
              <div className={styles.sectionLabel}>NEXT QUESTION</div>
              <h2>本次提问模型</h2>
              <p>选择会随下一次问题提交；它不会更改保存的服务地址、密钥或其他已提交任务。</p>
              <ModelControls value={preferences.modelChoice} onChange={value => updatePreference("modelChoice", value)} disabled={false} />
            </section>
          </div>}

          {activeTab === "research" && <section className={styles.card}>
            <div className={styles.sectionLabel}>RESEARCH PREFERENCES</div>
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
            <div className={styles.sectionLabel}>WORKSPACE VIEW</div>
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

          {activeTab === "files" && <div className={styles.sectionStack}>
            <section className={styles.card}>
              <div className={styles.sectionLabel}>LOCAL FILES</div>
              <h2>本地 PDF 与文献</h2>
              <p>{conversationId
                ? `当前是历史会话。文件属于${scopeTitle}，同一研究空间中的会话共享这些文献。`
                : `当前是新研究。文件会保存到${scopeTitle}，并供该空间内的会话共享。`}</p>
              {!fileScopeId && <p className={styles.infoNote}>打开此页签时会按需准备默认研究空间；服务设置不需要创建研究空间。</p>}
              {filesError && <div className={styles.error} role="alert">{filesError} <button className={styles.actionButton} type="button" onClick={retryFiles}>重试</button></div>}
              {filesLoading && !visiblePapers.length && <p className={styles.loading} role="status">{fileScopeId ? "正在读取本地文献…" : "正在准备本地文件范围…"}</p>}
              {fileScopeId && <PdfImport projectId={fileScopeId} onImported={() => refreshFiles(fileScopeId)} notify={notify} />}
            </section>
            {fileScopeId && <PaperLibrary papers={visiblePapers} renderPaperAction={() => null} />}
          </div>}

          {activeTab === "about" && <section className={styles.card}>
            <div className={styles.sectionLabel}>ABOUT</div>
            <h2>TUFA Scholar</h2>
            <p>本机科研助手。服务配置和研究资料由本机应用管理；浏览器偏好仅保存在当前浏览器中。</p>
            <div className={styles.infoNote}>OpenAI 兼容模型、OpenAlex 和网页搜索密钥只显示配置状态，不会回显已保存的密钥。</div>
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
    <div className={styles.sectionLabel}>MODEL & API</div>
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
