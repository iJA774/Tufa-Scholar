"use client";

import { type CSSProperties, type KeyboardEvent, type ReactNode, useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import ModelControls from "../ModelControls";
import { api, messageOf } from "../workspace/api";
import { DEFAULT_PREFERENCES, type ReportFormat, type WorkspacePreferences } from "../workspace/preferences";
import WebSearchSettings from "./WebSearchSettings";
import RuntimeLogsSettings from "./RuntimeLogsSettings";
import LocalDocuments from "./LocalDocuments";
import styles from "./SettingsPanel.module.css";

type SettingsPanelProps = {
  projectId?: number;
  onEnsureProject: () => Promise<number>;
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
  { id: "files", label: "本地文档" },
];

const relevanceOptions = [
  { value: "relaxed", label: "宽松", description: "保留更多主题相邻资料，适合探索新方向和跨学科关联；仍会去重、检查安全并拦截明显垃圾结果。" },
  { value: "standard", label: "标准（推荐）", description: "结合研究问题、Planner 子任务和候选标题摘要评分；明显不相关的结果不进入优先取证队列。" },
  { value: "strict", label: "严格", description: "优先采用直接回答当前研究子问题的材料，降低噪声；可能漏掉有价值的间接证据。" },
] as const;

const depthOptions = [
  { value: "light", label: "轻量" },
  { value: "standard", label: "标准（默认）" },
  { value: "deep", label: "深入" },
] as const;

const depthDescriptions: Record<WorkspacePreferences["researchDepth"], string> = {
  light: "预算上限：1 轮；每轮/全程 4 次工具动作；背景搜索 1 次。完成主要目标后可提前结束。",
  standard: "预算上限：2 轮；每轮 6 次、全程 10 次工具动作；背景搜索 2 次。主要问题解决后可提前结束，未解决则继续至上限。",
  deep: "预算上限：4 轮；每轮 8 次、全程 24 次工具动作；背景搜索 4 次。覆盖充分、证据可靠且无重要矛盾时可提前结束；达上限后报告未完成方向。可能消耗大量 Token。",
};

const reportFormatOptions: { value: ReportFormat; label: string }[] = [
  { value: "academic", label: "学术综述" },
  { value: "exploratory", label: "探索发现" },
];

function PreferenceSegments<T extends string>({ label, name, options, value, warningValue, onChange }: {
  label: string;
  name: string;
  options: readonly { value: T; label: string }[];
  value: T;
  warningValue: T;
  onChange: (value: T) => void;
}) {
  const index = options.findIndex(option => option.value === value);

  return <fieldset
    className={styles.segmentedOptions}
    aria-label={label}
    data-warning={value === warningValue}
    style={{ "--active-segment-index": index } as CSSProperties}
  >
    {options.map(option => <label key={option.value} className={styles.segmentedOption} data-warning={option.value === warningValue}>
      <input type="radio" name={name} value={option.value} checked={value === option.value} onChange={() => onChange(option.value)} />
      {value === option.value && option.value === warningValue && <svg className={styles.warningIcon} viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5 22 20.5H2L12 3.5Z" /><path d="M12 9v5.3M12 17.6v.1" /></svg>}
      <span>{option.label}</span>
    </label>)}
  </fieldset>;
}

function ReportFormatPicker({ value, onChange }: { value: ReportFormat; onChange: (value: ReportFormat) => void }) {
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const selectedIndex = Math.max(0, reportFormatOptions.findIndex(option => option.value === value));
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(selectedIndex);

  useEffect(() => {
    if (!open) setActiveIndex(selectedIndex);
  }, [open, selectedIndex]);

  useLayoutEffect(() => {
    const anchor = trigger.current;
    const list = menu.current;
    if (!open || !anchor || !list) return;

    const positionMenu = () => {
      const gap = 7;
      const inset = 8;
      const { clientWidth, clientHeight } = document.documentElement;
      const rect = anchor.getBoundingClientRect();
      const width = Math.min(Math.max(rect.width, 180), clientWidth - inset * 2);
      list.style.width = `${width}px`;
      list.style.left = `${Math.max(inset, Math.min(rect.right - width, clientWidth - width - inset))}px`;
      const height = list.scrollHeight + (list.offsetHeight - list.clientHeight);
      const below = clientHeight - rect.bottom - gap - inset;
      const above = rect.top - gap - inset;
      const opensAbove = below < height && above > below;
      const available = Math.max(0, opensAbove ? above : below);
      list.style.maxHeight = `${available}px`;
      list.style.top = `${opensAbove ? rect.top - gap - Math.min(height, available) : rect.bottom + gap}px`;
    };

    positionMenu();
    window.addEventListener("resize", positionMenu);
    window.addEventListener("scroll", positionMenu, { capture: true, passive: true });
    return () => {
      window.removeEventListener("resize", positionMenu);
      window.removeEventListener("scroll", positionMenu, true);
    };
  }, [open]);

  const chooseFormat = (index: number) => {
    const option = reportFormatOptions[index];
    if (!option) return;
    onChange(option.value);
    menu.current?.hidePopover();
    trigger.current?.focus({ preventScroll: true });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Tab") {
      if (open) menu.current?.hidePopover();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      if (!open) {
        setActiveIndex(selectedIndex);
        event.currentTarget.click();
      } else {
        setActiveIndex(index => (index + delta + reportFormatOptions.length) % reportFormatOptions.length);
      }
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setActiveIndex(event.key === "Home" ? 0 : reportFormatOptions.length - 1);
      if (!open) event.currentTarget.click();
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) chooseFormat(activeIndex);
      else {
        setActiveIndex(selectedIndex);
        event.currentTarget.click();
      }
    }
  };

  return <div className={styles.reportFormatPicker}>
    <button
      ref={trigger}
      className={styles.reportFormatTrigger}
      type="button"
      role="combobox"
      aria-label="报告格式"
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls={menuId}
      aria-activedescendant={open ? `${menuId}-option-${activeIndex}` : undefined}
      popoverTarget={menuId}
      onKeyDown={handleKeyDown}
    >
      <span>{reportFormatOptions[selectedIndex]?.label}</span>
      <svg className={open ? styles.reportFormatChevronOpen : styles.reportFormatChevron} viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
    </button>
    <div
      ref={menu}
      id={menuId}
      className={styles.reportFormatMenu}
      popover="auto"
      role="listbox"
      aria-label="报告格式"
      onToggle={event => setOpen(event.newState === "open")}
    >
      {reportFormatOptions.map((option, index) => <div
        id={`${menuId}-option-${index}`}
        key={option.value}
        className={`${styles.reportFormatOption} ${index === activeIndex ? styles.reportFormatOptionActive : ""} ${option.value === value ? styles.reportFormatOptionSelected : ""}`}
        role="option"
        aria-selected={option.value === value}
        onMouseEnter={() => setActiveIndex(index)}
        onClick={() => chooseFormat(index)}
      >
        <span>{option.label}</span>
        <svg className={styles.reportFormatCheck} viewBox="0 0 16 16" aria-hidden="true"><path d="m3 8 3.2 3.2L13 4.5" /></svg>
      </div>)}
    </div>
  </div>;
}

function SettingsHelp({ label, children }: { label: string; children: ReactNode }) {
  const descriptionId = useId();
  const [open, setOpen] = useState(false);
  return <div className={styles.settingHelp} data-open={open}>
    <button
      className={styles.settingHelpButton}
      type="button"
      aria-label={label}
      aria-describedby={descriptionId}
      aria-controls={descriptionId}
      aria-expanded={open}
      onClick={event => {
        const next = !open;
        setOpen(next);
        if (!next) event.currentTarget.blur();
      }}
      onBlur={() => setOpen(false)}
      onKeyDown={event => {
        if (event.key === "Escape") {
          setOpen(false);
          event.currentTarget.blur();
        }
      }}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 10.5v5" /><circle className={styles.helpDot} cx="12" cy="7.5" r=".65" /></svg>
    </button>
    <span id={descriptionId} className={styles.settingHelpTooltip} role="tooltip">{children}</span>
  </div>;
}

export default function SettingsPanel({
  projectId,
  onEnsureProject,
  onClose,
  preferences,
  onPreferencesChange,
  onNotice,
}: SettingsPanelProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const previousFocus = useRef<HTMLElement | null>(null);
  const closeTimer = useRef<number | null>(null);
  const closeRequested = useRef(false);
  const closeFinished = useRef(false);
  const [dialogVisible, setDialogVisible] = useState(false);
  const [closing, setClosing] = useState(false);
  const [activeTab, setActiveTab] = useState<PanelTab>("general");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!element.open) element.showModal();
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => setDialogVisible(true));
    });
    return () => {
      cancelAnimationFrame(firstFrame);
      if (secondFrame) cancelAnimationFrame(secondFrame);
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
      if (element.open) element.close();
      const target = previousFocus.current;
      if (target?.isConnected) requestAnimationFrame(() => target.focus());
    };
  }, []);

  const finishClose = useCallback(() => {
    if (!closeRequested.current || closeFinished.current) return;
    closeFinished.current = true;
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    closeTimer.current = null;
    onClose();
  }, [onClose]);

  const requestClose = useCallback(() => {
    if (closeRequested.current) return;
    closeRequested.current = true;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      onClose();
      return;
    }
    setClosing(true);
    closeTimer.current = window.setTimeout(finishClose, 220);
  }, [finishClose, onClose]);

  const notify = (message: string) => {
    setNotice(message);
    onNotice(message);
  };

  const selectTab = (tab: PanelTab) => {
    setActiveTab(tab);
    setNotice("");
  };

  const updatePreference = <K extends keyof WorkspacePreferences>(key: K, value: WorkspacePreferences[K]) => {
    onPreferencesChange({ ...preferences, [key]: value });
  };

  const resetPreferences = () => {
    onPreferencesChange({ ...DEFAULT_PREFERENCES, modelChoice: preferences.modelChoice });
    notify("界面偏好已重置，模型选择与服务配置已保留。");
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
  const activeTabIndex = tabs.findIndex(tab => tab.id === activeTab);
  return (
    <dialog
      ref={dialog}
      className={`${styles.dialog} ${dialogVisible ? styles.dialogVisible : ""} ${closing ? styles.dialogClosing : ""}`}
      aria-labelledby="settings-title"
      onCancel={event => { event.preventDefault(); requestClose(); }}
      onClick={event => { if (event.target === dialog.current) requestClose(); }}
      onTransitionEnd={event => { if (event.target === dialog.current && event.propertyName === "opacity") finishClose(); }}
    >
      <div className={styles.frame}>
        <header className={styles.header}>
          <div>
            <h1 id="settings-title">设置</h1>
          </div>
          <button className={styles.closeButton} type="button" aria-label="关闭设置" onClick={requestClose}>×</button>
        </header>

        <div className={styles.layout}>
          <div className={styles.sidebar}>
            <nav className={styles.tabs} style={{ "--active-tab-index": activeTabIndex } as CSSProperties} role="tablist" aria-label="设置类别" aria-orientation="vertical">
              <span className={styles.tabIndicator} aria-hidden="true" />
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
            className={`${styles.content} ${activeTab === "research" ? styles.researchContent : ""}`}
            id="settings-content"
            role="tabpanel"
            aria-labelledby={`settings-tab-${activeTab}`}
            tabIndex={0}
          >
          {notice && <p className={styles.notice} role="status">{notice}</p>}

          {activeTab === "general" && <div className={styles.sectionStack}>
            <section className={styles.card}>
              <h2>新问题的默认模式</h2>
              <div className={styles.modeChoices} role="radiogroup" aria-label="新问题的默认模式">
                <label className={preferences.mode === "auto" ? styles.choiceSelected : styles.choice}>
                  <input type="radio" name="default-mode" value="auto" checked={preferences.mode === "auto"} onChange={() => updatePreference("mode", "auto")} />
                  <span><strong>自动</strong><small>由系统决定是否需要研究。</small></span>
                </label>
                <label className={preferences.mode === "quick" ? styles.choiceSelected : styles.choice}>
                  <input type="radio" name="default-mode" value="quick" checked={preferences.mode === "quick"} onChange={() => updatePreference("mode", "quick")} />
                  <span><strong>快速探索</strong><small>直接回答，不启动检索。</small></span>
                </label>
                <label className={preferences.mode === "deep" ? styles.choiceSelected : styles.choice}>
                  <input type="radio" name="default-mode" value="deep" checked={preferences.mode === "deep"} onChange={() => updatePreference("mode", "deep")} />
                  <span><strong>深度研究</strong><small>检索、分析并生成报告。</small></span>
                </label>
              </div>
              <div className={styles.toggleHelpRow}>
                <label className={styles.toggleRow}>
                  <span><strong>Enter 发送消息</strong></span>
                  <input type="checkbox" checked={preferences.enterToSend} onChange={event => updatePreference("enterToSend", event.target.checked)} />
                </label>
                <SettingsHelp label="Enter 发送消息说明">关闭后，按 Enter 换行；使用 Ctrl+Enter 或发送按钮提交。</SettingsHelp>
              </div>
            </section>

            <section className={styles.card}>
              <h2>本次提问模型</h2>
              <ModelControls value={preferences.modelChoice} onChange={value => updatePreference("modelChoice", value)} disabled={false} />
            </section>

            <PreprocesserSettings />

            <section className={styles.resetSection}>
              <p>恢复界面偏好默认值。API、URL 和模型选择会保留。</p>
              <button className={styles.resetButton} type="button" onClick={resetPreferences}>重置所有设置</button>
            </section>
          </div>}

          {activeTab === "research" && <section className={`${styles.card} ${styles.researchPreferences}`}>
            <div className={styles.settingControl}>
              <div className={styles.settingHeading}>
                <strong>研究深度</strong>
              </div>
              <PreferenceSegments label="研究深度" name="research-depth" options={depthOptions} value={preferences.researchDepth} warningValue="deep" onChange={value => updatePreference("researchDepth", value)} />
              <p className={`${styles.depthDescription} ${preferences.researchDepth === "deep" ? styles.depthDescriptionWarning : ""}`} aria-live="polite">
                <strong>{depthOptions.find(option => option.value === preferences.researchDepth)?.label}：</strong>
                {depthDescriptions[preferences.researchDepth]}
              </p>
            </div>

            <div className={`${styles.settingControl} ${styles.settingControlBordered}`}>
              <div className={styles.toggleHelpRow}>
                <label className={styles.toggleRow}>
                  <span><strong>优先使用原始证据</strong></span>
                  <input type="checkbox" checked={preferences.preferPrimaryEvidence} onChange={event => updatePreference("preferPrimaryEvidence", event.target.checked)} />
                </label>
                <SettingsHelp label="优先使用原始证据说明">开启后优先读取质量更高、来源更直接的材料，并尽可能引用带页码或网页定位的原文。关闭后可接受更多间接来源以扩大覆盖、减少部分检索开销。两种情况下都会校验引用真实性、来源范围和数值，关闭此偏好不放宽这些校验。</SettingsHelp>
              </div>
            </div>

            <div className={`${styles.settingControl} ${styles.settingControlBordered}`}>
              <div className={styles.settingHeading}>
                <strong>相关性过滤强度</strong>
              </div>
              <PreferenceSegments label="相关性过滤强度" name="relevance-filter" options={relevanceOptions} value={preferences.relevanceFilter} warningValue="strict" onChange={value => updatePreference("relevanceFilter", value)} />
              <p className={`${styles.relevanceDescription} ${preferences.relevanceFilter === "strict" ? styles.relevanceDescriptionStrict : ""}`} aria-live="polite">
                <strong>{relevanceOptions.find(option => option.value === preferences.relevanceFilter)?.label}：</strong>
                {relevanceOptions.find(option => option.value === preferences.relevanceFilter)?.description}
              </p>
            </div>

            <div className={`${styles.settingControl} ${styles.settingControlBordered} ${styles.reportFormatRow}`}>
              <span><strong>报告格式</strong><small>暂不生效 · 终稿按当前工作流生成。</small></span>
              <ReportFormatPicker value={preferences.reportFormat} onChange={value => updatePreference("reportFormat", value)} />
            </div>
          </section>}

          {activeTab === "appearance" && <section className={styles.card}>
            <div className={styles.toggleHelpRow}>
              <label className={styles.toggleRow}>
                <span><strong>环境光效</strong></span>
                <input type="checkbox" checked={preferences.glow} onChange={event => updatePreference("glow", event.target.checked)} />
              </label>
              <SettingsHelp label="环境光效说明">显示工作台的柔和边缘光。</SettingsHelp>
            </div>
            <div className={styles.toggleHelpRow}>
              <label className={styles.toggleRow}>
                <span><strong>背景网格</strong></span>
                <input type="checkbox" checked={preferences.grid} onChange={event => updatePreference("grid", event.target.checked)} />
              </label>
              <SettingsHelp label="背景网格说明">显示低对比度的工作区网格。</SettingsHelp>
            </div>
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

          <div hidden={activeTab !== "files"}><LocalDocuments projectId={projectId} onEnsureProject={onEnsureProject} /></div>
          </main>
        </div>
        <footer className={`${styles.footer} ${activeTab === "research" ? styles.footerResearch : ""}`}>
          {activeTab !== "research" && <span>{currentTab.label}</span>}
          <button className={styles.doneButton} type="button" onClick={requestClose}>完成</button>
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


type PreprocesserConfig = { preprocesser: { model: string | null; model_source: "default" | "local" | "environment" }; model: { model: string | null } };
function PreprocesserSettings() {
  const saveController = useRef<AbortController | null>(null);
  const [config, setConfig] = useState<PreprocesserConfig>();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<PreprocesserConfig>("/settings", { signal: controller.signal }).then(data => {
      setConfig(data); setDraft(data.preprocesser.model_source === "local" ? data.preprocesser.model || "" : "");
    }).catch(cause => { if (!controller.signal.aborted) setNotice(messageOf(cause)); });
    return () => { controller.abort(); saveController.current?.abort(); };
  }, []);
  const save = async () => {
    const controller = new AbortController(); saveController.current = controller;
    setBusy(true); setNotice("");
    try {
      const data = await api<PreprocesserConfig>("/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ preprocesser_model: draft.trim() }), signal: controller.signal });
      setConfig(data); setDraft(data.preprocesser.model_source === "local" ? data.preprocesser.model || "" : ""); setNotice("已保存");
    } catch (cause) { if (!controller.signal.aborted) setNotice(messageOf(cause)); } finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <section className={styles.card}>
    <h2>文档分析模型</h2>
    {!config && !notice && <p role="status">正在读取配置…</p>}
    {config && <div className={styles.formGrid}>
      <label htmlFor="preprocesser-model">模型 ID</label>
      <input id="preprocesser-model" maxLength={300} value={config.preprocesser.model_source === "environment" ? config.preprocesser.model || "" : draft} placeholder={config.model.model || "默认主模型"} disabled={busy || config.preprocesser.model_source === "environment"} onChange={event => setDraft(event.target.value)} />
      <small>{config.preprocesser.model_source === "environment" ? "由环境变量锁定" : "留空使用主模型"}</small>
      <button className={styles.actionButton} type="button" disabled={busy || config.preprocesser.model_source === "environment"} onClick={() => void save()}>保存分析模型</button>
    </div>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
