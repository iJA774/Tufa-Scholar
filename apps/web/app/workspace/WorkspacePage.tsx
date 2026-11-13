"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import SettingsPanel from "../settings/SettingsPanel";
import { api, json, messageOf } from "./api";
import ResearchChat from "./ResearchChat";
import type { Conversation, Project } from "./types";
import {
  DEFAULT_PREFERENCES,
  readWorkspacePreferences,
  writeWorkspacePreferences,
  type WorkspacePreferences,
} from "./preferences";
import styles from "./WorkspacePage.module.css";

type ConversationGroup = "今天" | "最近 7 天" | "更早";

function timestamp(value: string | null) {
  if (!value) return 0;
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const parsed = Date.parse(normalized);
  return Number.isFinite(parsed) ? parsed : 0;
}

function groupFor(value: string | null): ConversationGroup {
  const time = timestamp(value);
  if (!time) return "更早";
  const date = new Date(time);
  const today = new Date();
  if (date.getFullYear() === today.getFullYear() && date.getMonth() === today.getMonth() && date.getDate() === today.getDate()) return "今天";
  if (Date.now() - time < 7 * 24 * 60 * 60 * 1000) return "最近 7 天";
  return "更早";
}

export default function WorkspacePage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number>();
  const [projectLoading, setProjectLoading] = useState(true);
  const [projectError, setProjectError] = useState("");
  const [retryProjects, setRetryProjects] = useState(0);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<number>();
  const [preferences, setPreferences] = useState<WorkspacePreferences>(DEFAULT_PREFERENCES);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historySearch, setHistorySearch] = useState("");
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [pageError, setPageError] = useState("");
  const [newStudyKey, setNewStudyKey] = useState(0);
  const panelRef = useRef<HTMLElement>(null);
  const brandRef = useRef<HTMLButtonElement>(null);
  const toastTimer = useRef<number | undefined>(undefined);
  const projectsRef = useRef<Project[]>([]);
  const projectIdRef = useRef<number | undefined>(undefined);
  const createProjectRequest = useRef<Promise<number> | null>(null);
  const createProjectController = useRef<AbortController | null>(null);
  const projectReady = useRef<Promise<void> | null>(null);
  const projectReadyResolve = useRef<(() => void) | null>(null);
  const projectLoadSucceeded = useRef<boolean | null>(null);
  const historyController = useRef<AbortController | null>(null);
  const historyRequest = useRef(0);
  const mounted = useRef(false);

  if (!projectReady.current) {
    projectReady.current = new Promise(resolve => { projectReadyResolve.current = resolve; });
  }

  const activeProject = projects.find(project => project.id === projectId);
  const activeConversation = conversations.find(conversation => conversation.id === conversationId);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      createProjectController.current?.abort();
      historyController.current?.abort();
      historyRequest.current++;
      window.clearTimeout(toastTimer.current);
    };
  }, []);

  useEffect(() => {
    setPreferences(readWorkspacePreferences());
  }, []);

  useEffect(() => {
    if (!historyOpen) return;
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!panelRef.current?.contains(target) && !brandRef.current?.contains(target)) setHistoryOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setHistoryOpen(false);
      brandRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [historyOpen]);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    projectReady.current = new Promise(resolve => { projectReadyResolve.current = resolve; });
    setProjectLoading(true);
    setHistoryLoading(true);
    setProjectError("");
    setHistoryError("");
    projectLoadSucceeded.current = null;

    api<Project[]>("/projects", { signal: controller.signal }).then(existingProjects => {
      if (!current || controller.signal.aborted) return;
      projectsRef.current = existingProjects;
      const first = existingProjects[0];
      projectIdRef.current = first?.id;
      projectLoadSucceeded.current = true;
      setProjects(existingProjects);
      setProjectId(first?.id);
      setProjectError("");
    }).catch(error => {
      if (current && !controller.signal.aborted) {
        projectLoadSucceeded.current = false;
        setProjectError(messageOf(error));
      }
    }).finally(() => {
      if (current) {
        setProjectLoading(false);
        projectReadyResolve.current?.();
        projectReadyResolve.current = null;
      }
    });

    api<Conversation[]>("/conversations", { signal: controller.signal }).then(history => {
      if (!current || controller.signal.aborted) return;
      setConversations(history);
      setHistoryError("");
    }).catch(error => {
      if (current && !controller.signal.aborted) setHistoryError(messageOf(error));
    }).finally(() => {
      if (current) setHistoryLoading(false);
    });

    return () => {
      current = false;
      controller.abort();
      projectReadyResolve.current?.();
      projectReadyResolve.current = null;
    };
  }, [retryProjects]);

  const notify = useCallback((message: string) => {
    setToast(message);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(""), 2600);
  }, []);

  const reportError = useCallback((message: string) => {
    setPageError(message);
    notify(message);
  }, [notify]);

  const refreshHistory = useCallback(async () => {
    historyController.current?.abort();
    const controller = new AbortController();
    historyController.current = controller;
    const requestId = ++historyRequest.current;
    try {
      const history = await api<Conversation[]>("/conversations", { signal: controller.signal });
      if (mounted.current && !controller.signal.aborted && requestId === historyRequest.current) {
        setConversations(history);
        setHistoryError("");
      }
      return history;
    } catch (error) {
      if (mounted.current && !controller.signal.aborted && requestId === historyRequest.current) setHistoryError(messageOf(error));
      throw error;
    } finally {
      if (historyController.current === controller) historyController.current = null;
    }
  }, []);

  const ensureProject = useCallback(async () => {
    await projectReady.current;
    if (!mounted.current) throw new DOMException("页面已关闭", "AbortError");
    const currentId = projectIdRef.current ?? projectsRef.current[0]?.id;
    if (currentId) {
      projectIdRef.current = currentId;
      setProjectId(currentId);
      return currentId;
    }
    if (projectLoadSucceeded.current !== true) throw new Error("无法确认当前研究空间。请重新加载后重试。");
    if (createProjectRequest.current) return createProjectRequest.current;

    const controller = new AbortController();
    createProjectController.current = controller;
    const request = api<Project>("/projects", { ...json({ name: "默认研究空间" }), signal: controller.signal }).then(project => {
      if (!mounted.current || controller.signal.aborted) throw new DOMException("页面已关闭", "AbortError");
      const updated = [...projectsRef.current, project];
      projectsRef.current = updated;
      projectIdRef.current = project.id;
      setProjects(updated);
      setProjectId(project.id);
      setProjectError("");
      return project.id;
    }).finally(() => {
      if (createProjectRequest.current === request) {
        createProjectRequest.current = null;
        createProjectController.current = null;
      }
    });
    createProjectRequest.current = request;
    return request;
  }, []);

  const updatePreferences = useCallback((next: WorkspacePreferences) => {
    setPreferences(next);
    writeWorkspacePreferences(next);
  }, []);

  const updatePreference = useCallback(<K extends keyof WorkspacePreferences>(key: K, value: WorkspacePreferences[K]) => {
    setPreferences(previous => {
      const next = { ...previous, [key]: value };
      writeWorkspacePreferences(next);
      return next;
    });
  }, []);

  const toggleHistory = useCallback(() => setHistoryOpen(open => !open), []);
  const createNewStudy = useCallback(() => {
    setConversationId(undefined);
    setNewStudyKey(key => key + 1);
    setHistorySearch("");
    setHistoryOpen(false);
    setPageError("");
    brandRef.current?.focus();
  }, []);

  const openConversation = useCallback((conversation: Conversation) => {
    const project = projectsRef.current.find(item => item.id === conversation.project_id);
    if (!project) {
      setHistoryError("此会话所属的研究空间当前不可用，请刷新后重试。");
      return;
    }
    projectIdRef.current = project.id;
    setProjectId(project.id);
    setConversationId(conversation.id);
    setHistoryOpen(false);
    setPageError("");
  }, []);

  const onConversationChange = useCallback((id: number, nextProjectId: number) => {
    if (projectIdRef.current !== nextProjectId) {
      projectIdRef.current = nextProjectId;
      setProjectId(nextProjectId);
    }
    setConversationId(id);
  }, []);

  const onHistoryUpdated = useCallback(() => {
    void refreshHistory().catch(error => reportError(messageOf(error)));
  }, [refreshHistory, reportError]);

  const conversationsByGroup = useMemo(() => {
    const search = historySearch.trim().toLocaleLowerCase();
    const matches = conversations.filter(item => !search || item.title.toLocaleLowerCase().includes(search));
    return (["今天", "最近 7 天", "更早"] as ConversationGroup[]).map(group => ({
      group,
      items: matches.filter(item => groupFor(item.updated_at) === group),
    })).filter(section => section.items.length);
  }, [conversations, historySearch]);

  const appClass = [
    styles.app,
    !preferences.glow ? styles.noGlow : "",
    !preferences.grid ? styles.noGrid : "",
  ].filter(Boolean).join(" ");

  return <div className={appClass}>
    <div className={styles.backdrop} aria-hidden="true"><div className={styles.ambient} /></div>
    <header className={styles.header}>
      <button
        ref={brandRef}
        className={styles.brand}
        type="button"
        aria-label="展开研究历史记录"
        aria-controls="historyPanel"
        aria-expanded={historyOpen}
        onClick={toggleHistory}
      >
        <span className={styles.brandCode} aria-hidden="true"><span>T</span><span>U</span><span>F</span><span>A</span></span>
        <span className={styles.brandDivider} aria-hidden="true" />
        <span className={styles.brandLabel}>Traceable Unified<br />Fact-finding Agent</span>
        <svg className={`${styles.icon} ${styles.brandChevron}`} viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
      </button>
      <div className={styles.headerActions}>
        <div className={styles.headerStatus} aria-label="TUFA Scholar 研究工作台"><span className={styles.statusDot} />SCHOLAR / RESEARCH WORKSPACE</div>
        <span className={styles.headerDivider} aria-hidden="true" />
        <button className={styles.settingsButton} type="button" aria-haspopup="dialog" onClick={() => setSettingsOpen(true)}>
          <svg className={styles.icon} viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.3a3.3 3.3 0 1 0 0-6.6 3.3 3.3 0 0 0 0 6.6Z" /><path d="M19.1 13.6a7.6 7.6 0 0 0 0-3.2l1.7-1.3-1.7-3-2 .7a7.8 7.8 0 0 0-2.8-1.6L14 3h-4l-.3 2.2A7.8 7.8 0 0 0 7 6.8l-2-.7-1.8 3 1.7 1.3a7.6 7.6 0 0 0 0 3.2l-1.7 1.3 1.8 3 2-.7a7.8 7.8 0 0 0 2.7 1.6L10 21h4l.3-2.2a7.8 7.8 0 0 0 2.8-1.6l2 .7 1.7-3-1.7-1.3Z" /></svg>
          <span>设置</span>
        </button>
      </div>
    </header>

    <aside
      ref={panelRef}
      className={`${styles.historyPanel} ${historyOpen ? styles.historyPanelOpen : ""}`}
      id="historyPanel"
      aria-label="研究历史"
      aria-hidden={!historyOpen}
    >
      <div className={styles.historyTop}>
        <div className={styles.drawerKicker}>YOUR RESEARCH LIBRARY</div>
        <div className={styles.historyHeading}>
          <h2>研究历史</h2>
          <button className={styles.historyClose} type="button" aria-label="关闭历史记录" onClick={() => setHistoryOpen(false)}><svg className={styles.icon} viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg></button>
        </div>
        <button className={styles.newButton} type="button" onClick={createNewStudy}><svg className={`${styles.icon} ${styles.iconSmall}`} viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>开启新的研究</button>
        <label className={styles.historySearch}>
          <svg className={`${styles.icon} ${styles.iconSmall}`} viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.6" cy="10.6" r="6.2" /><path d="m15.4 15.4 5 5" /></svg>
          <span className={styles.visuallyHidden}>搜索研究历史</span>
          <input type="search" value={historySearch} onChange={event => setHistorySearch(event.target.value)} placeholder="搜索研究记录..." autoComplete="off" />
        </label>
      </div>
      <div className={styles.historyList} aria-live="polite">
        {historyLoading && <p className={styles.historyEmpty}>正在读取研究记录…</p>}
        {!historyLoading && historyError && <p className={styles.historyError} role="alert">{historyError}<button type="button" onClick={onHistoryUpdated}>重试</button></p>}
        {!historyLoading && !historyError && conversationsByGroup.map(section => <section key={section.group}>
          <h3 className={styles.historyGroupTitle}>{section.group}</h3>
          {section.items.map(item => <button key={item.id} className={`${styles.historyItem} ${conversationId === item.id ? styles.historyItemActive : ""}`} type="button" disabled={projectLoading} title={item.title} onClick={() => openConversation(item)}>
            <svg className={`${styles.icon} ${styles.iconSmall}`} viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5.5l3.5 2" /></svg>
            <span>{item.title}</span>
          </button>)}
        </section>)}
        {!historyLoading && !historyError && !conversationsByGroup.length && <p className={styles.historyEmpty}>{historySearch.trim() ? "没有找到匹配的研究记录。" : "还没有研究记录。开始研究后，会话会保存在这里。"}</p>}
      </div>
      <div className={styles.historyBottom}><strong>LOCAL RESEARCH ARCHIVE</strong><br />研究会话保存在本机项目中</div>
    </aside>

    {pageError && <div className={styles.pageError} role="alert">{pageError}<button type="button" onClick={() => setPageError("")} aria-label="关闭提示">×</button></div>}
    {projectError && <div className={styles.projectError} role="alert">无法读取研究空间：{projectError}<button type="button" onClick={() => setRetryProjects(value => value + 1)}>重试</button></div>}
    <ResearchChat
      projectId={activeProject?.id}
      projectLoading={projectLoading}
      conversationId={conversationId}
      conversationTitle={activeConversation?.title}
      resetKey={newStudyKey}
      mode={preferences.mode}
      enterToSend={preferences.enterToSend}
      modelChoice={preferences.modelChoice}
      ambient={preferences.glow}
      density={preferences.density}
      onModeChange={mode => updatePreference("mode", mode)}
      onEnsureProject={ensureProject}
      onConversationChange={onConversationChange}
      onNewStudy={createNewStudy}
      onHistoryUpdated={onHistoryUpdated}
      onError={reportError}
    />

    {settingsOpen && <SettingsPanel
      onClose={() => setSettingsOpen(false)}
      projectId={activeProject?.id}
      projectName={activeProject?.name}
      conversationId={conversationId}
      preferences={preferences}
      onPreferencesChange={updatePreferences}
      ensureProject={ensureProject}
      onNotice={notify}
    />}
    {toast && <div className={styles.toast} role="status" aria-live="polite">{toast}</div>}
  </div>;
}
