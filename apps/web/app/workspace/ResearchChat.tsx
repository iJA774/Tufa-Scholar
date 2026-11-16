"use client";

import { type CSSProperties, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, useEffect, useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { createPortal } from "react-dom";
import remarkGfm from "remark-gfm";
import { useRunPolling } from "../hooks/useRunPolling";
import { useRunEvents } from "../hooks/useRunEvents";
import { buildModelPreviews, type RunEvent } from "../hooks/runEvents";
import { api, json, messageOf } from "./api";
import type { Message, ResearchMode, Run, Subtask } from "./types";
import type { ModelChoice, ResearchDepth, RelevanceFilter } from "./preferences";
import { webSourceLabel } from "./sourceLabels";
import HeroEmblem from "./HeroEmblem";
import ResearchPanel from "./ResearchPanel";
import { displayReport, reportEvidenceIds, reportLink, safeRunError } from "./reportDisplay";
import styles from "./ResearchChat.module.css";

const statusText: Record<string, string> = {
  queued: "等待处理",
  pending: "等待处理",
  running: "正在处理",
  succeeded: "回答完成",
  success: "回答完成",
  completed: "回答完成",
  failed: "任务失败",
  interrupted: "任务已中断",
};

const stageText: Record<string, string> = {
  queued: "等待处理",
  pending: "等待处理",
  searching: "检索文献",
  searching_web: "搜索网页",
  search: "检索文献",
  local: "搜索项目文献",
  openalex: "搜索 OpenAlex",
  completed: "回答完成",
  done: "回答完成",
  retrieving: "检索文献",
  generating: "生成回答",
  synthesizing: "整理证据",
  planning: "规划问题",
  evidence: "提取证据",
  assessing: "检查证据缺口",
  verifying: "核验结论",
  answering: "生成回答",
  failed: "任务失败",
  interrupted: "任务已中断",
  dispatcher: "",
  background_investigator: "检索背景资料",
  planner: "制定研究计划",
  supervisor: "统筹研究步骤",
  researcher: "搜索与整理证据",
  analyst: "分析研究证据",
  coder: "",
  reporter: "整理最终报告",
};

function modeText(mode?: ResearchMode) {
  if (mode === "quick") return "快速探索";
  if (mode === "deep") return "深度研究";
  if (mode === "auto") return "自动";
  return "研究任务";
}

const modeOptions: { value: ResearchMode; label: string }[] = [
  { value: "auto", label: "自动" },
  { value: "deep", label: "深度研究" },
  { value: "quick", label: "快速探索" },
];

function ModePicker({ mode, disabled, onModeChange }: { mode: ResearchMode; disabled: boolean; onModeChange: (mode: ResearchMode) => void }) {
  const [open, setOpen] = useState(false);
  const selectedIndex = Math.max(0, modeOptions.findIndex(option => option.value === mode));
  const [activeIndex, setActiveIndex] = useState(selectedIndex);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) setActiveIndex(selectedIndex);
  }, [open, selectedIndex]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  useEffect(() => {
    if (!open) return;
    const onDocumentClick = (event: MouseEvent) => {
      if (!(event.target instanceof Node) || root.current?.contains(event.target)) return;
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest("[data-research-preset]")) {
        event.preventDefault();
        event.stopPropagation();
      }
      setOpen(false);
    };

    document.addEventListener("click", onDocumentClick, true);
    return () => document.removeEventListener("click", onDocumentClick, true);
  }, [open]);

  const chooseMode = (index: number) => {
    const option = modeOptions[index];
    if (!option) return;
    onModeChange(option.value);
    setOpen(false);
    trigger.current?.focus();
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (event.key === "Tab") {
      if (open) setOpen(false);
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      setOpen(false);
      trigger.current?.focus();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      if (!open) {
        setActiveIndex(selectedIndex);
        setOpen(true);
      } else {
        setActiveIndex(index => (index + delta + modeOptions.length) % modeOptions.length);
      }
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setActiveIndex(event.key === "Home" ? 0 : modeOptions.length - 1);
      setOpen(true);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) chooseMode(activeIndex);
      else {
        setActiveIndex(selectedIndex);
        setOpen(true);
      }
    }
  };

  return <div ref={root} className={`${styles.modePicker} ${open ? styles.modePickerOpen : ""}`}>
    <button
      ref={trigger}
      className={styles.modeTrigger}
      type="button"
      role="combobox"
      aria-label={`研究模式：当前${modeOptions[selectedIndex]?.label || "自动"}`}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls={menuId}
      aria-activedescendant={open ? `${menuId}-option-${activeIndex}` : undefined}
      disabled={disabled}
      onClick={() => {
        if (disabled) return;
        if (open) setOpen(false);
        else {
          setActiveIndex(selectedIndex);
          setOpen(true);
        }
      }}
      onKeyDown={handleKeyDown}
    >
      <svg className={styles.modeSpark} viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 1.8 7.2L21 11l-7.2 1.8L12 20l-1.8-7.2L3 11l7.2-1.8L12 2Z" /></svg>
      <span>{modeOptions[selectedIndex]?.label || "自动"}</span>
      <svg className={`${styles.modeChevron} ${open ? styles.modeChevronOpen : ""}`} viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
    </button>
    <div
      id={menuId}
      className={`${styles.modeMenu} ${open ? styles.modeMenuOpen : styles.modeMenuClosed}`}
      role="listbox"
      aria-label="研究模式"
      aria-hidden={!open}
      inert={!open}
    >
      {modeOptions.map((option, index) => <div
        id={`${menuId}-option-${index}`}
        key={option.value}
        className={`${styles.modeOption} ${index === activeIndex ? styles.modeOptionActive : ""} ${option.value === mode ? styles.modeOptionSelected : ""}`}
        style={{ "--mode-option-index": index } as CSSProperties}
        role="option"
        aria-selected={option.value === mode}
        onMouseEnter={() => setActiveIndex(index)}
        onClick={() => chooseMode(index)}
      >
        <span>{option.label}</span>
        <svg className={`${styles.modeCheck} ${option.value === mode ? styles.modeCheckSelected : ""}`} viewBox="0 0 16 16" aria-hidden="true"><path d="m3 8 3.2 3.2L13 4.5" /></svg>
      </div>)}
    </div>
  </div>;
}

const deepResearchNodes = new Set(["background_investigator", "planner", "supervisor", "researcher", "analyst", "coder", "reporter"]);

function isDeepResearchRun(run?: Run, events: RunEvent[] = []) {
  if (!run) return false;
  if (run.research_mode === "deep") return true;
  if (run.research_mode !== "auto") return false;
  if (run.stage && deepResearchNodes.has(run.stage)) return true;
  if (run.subtasks?.length) return true;
  return events.some(event =>
    (event.kind === "node.started" || event.kind === "node.completed")
    && typeof event.data.node === "string"
    && deepResearchNodes.has(event.data.node),
  );
}

function stageLabel(run: Run) {
  if (run.stage === "dispatcher") {
    if (run.research_mode === "quick") return "生成快速回答";
    if (run.research_mode === "deep") return "进入深度研究";
    return "判断是否需要研究";
  }
  return run.stage ? stageText[run.stage] || "研究进行中" : statusText[run.status] || run.status;
}

function stageSuffix(run: Run) {
  const stage = stageLabel(run);
  const status = statusText[run.status] || run.status;
  return stage && stage !== status ? ` · ${stage}` : "";
}

function safeSourceUrl(value?: string | null) {
  try {
    const url = new URL(value || "");
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function ResearchActivity({ items }: { items?: Subtask[] }) {
  if (!items?.length) return null;
  return <details className={styles.activity}>
    <summary>研究过程 · {items.length} 步</summary>
    <div className={styles.activityBody}>
      {items.map(step => <section key={step.index}>
        <strong>{step.index}. {step.task}</strong>
        {step.findings?.map((finding, index) => <p key={index}>{finding.text} {finding.citations.map(id => `[${id}]`).join(" ")}</p>)}
        {step.status === "sources_only" && <p>已找到相关资料，结论仍待核验。</p>}
        {step.status === "no_evidence" && <p>本步暂无可用证据。</p>}
        {step.sources?.map(source => {
          const href = safeSourceUrl(source.original_url);
          return <div key={source.id}>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{source.title || source.id} ↗</a> : <span>{source.title || source.id}</span>} [{source.id}]</div>;
        })}
        {step.gap && <p>待查：{step.gap}</p>}
      </section>)}
    </div>
  </details>;
}

type Props = {
  projectId?: number;
  projectLoading: boolean;
  conversationId?: number;
  conversationTitle?: string;
  resetKey: number;
  mode: ResearchMode;
  researchDepth: ResearchDepth;
  preferPrimaryEvidence: boolean;
  relevanceFilter: RelevanceFilter;
  enterToSend: boolean;
  modelChoice: ModelChoice;
  ambient: boolean;
  density: "default" | "compact";
  onModeChange: (mode: ResearchMode) => void;
  onEnsureProject: () => Promise<number>;
  onConversationChange: (id: number, projectId: number) => void;
  onNewStudy: () => void;
  onHistoryUpdated: () => void;
  onError: (message: string) => void;
};

export default function ResearchChat({
  projectId,
  projectLoading,
  conversationId,
  conversationTitle,
  resetKey,
  mode,
  researchDepth,
  preferPrimaryEvidence,
  relevanceFilter,
  enterToSend,
  modelChoice,
  ambient,
  density,
  onModeChange,
  onEnsureProject,
  onConversationChange,
  onNewStudy,
  onHistoryUpdated,
  onError,
}: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const [closingNotice, setClosingNotice] = useState(false);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [query, setQuery] = useState("");
  const [run, setRun] = useState<Run>();
  const [loadedConversationId, setLoadedConversationId] = useState<number>();
  const [researchPanelOpen, setResearchPanelOpen] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]);
  const [historyRuns, setHistoryRuns] = useState<Record<number, Run>>({});
  const [busy, setBusy] = useState("");
  const [loadingConversation, setLoadingConversation] = useState(false);
  const requestedHistoryRuns = useRef(new Set<number>());
  const autoOpenedRuns = useRef(new Set<number>());
  const actionController = useRef<AbortController | null>(null);
  const selectionEpoch = useRef(0);
  const selected = useRef({ projectId, conversationId, resetKey });
  const submittedConversationRef = useRef<number | undefined>(undefined);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const messagesElement = useRef<HTMLDivElement>(null);
  const progressToggle = useRef<HTMLButtonElement>(null);
  const progressCloseButton = useRef<HTMLButtonElement>(null);
  const activeRun = run && ["queued", "pending", "running"].includes(run.status);
  const hasConversation = conversationId !== undefined;
  const historyRunCandidate = isDeepResearchRun(run) ? undefined
    : runs.find(item => item.id !== run?.id && isDeepResearchRun(item))
      ?? runs.find(item => item.id !== run?.id && item.research_mode === "auto");
  const updateFromEventStream = (next: Run) => {
    setRun(previous => previous?.id === next.id ? next : previous);
    setRuns(previous => previous.some(item => item.id === next.id) ? previous.map(item => item.id === next.id ? next : item) : [next, ...previous]);
    setHistoryRuns(previous => ({ ...previous, [next.id]: next }));
  };
  const liveEvents = useRunEvents({
    run,
    projectId,
    conversationId,
    fetchRun: (id, signal) => api<Run>(`/runs/${id}`, { signal }),
    onRunUpdate: updateFromEventStream,
  });
  const historyEvents = useRunEvents({
    run: historyRunCandidate,
    projectId,
    conversationId,
    fetchRun: (id, signal) => api<Run>(`/runs/${id}`, { signal }),
    onRunUpdate: updateFromEventStream,
  });
  const panelRun = isDeepResearchRun(run, liveEvents) ? run : runs.find(item => {
    if (item.id === run?.id) return false;
    const events = item.id === historyRunCandidate?.id ? historyEvents : [];
    return isDeepResearchRun(item, events);
  });
  const progressRun = hasConversation && loadedConversationId === conversationId ? panelRun : undefined;
  const progressReport = progressRun ? [...messages].reverse().find(message => message.role === "assistant" && message.run_id === progressRun.id && message.metadata?.report_id !== undefined) : undefined;
  const progressEvents = progressRun?.id === run?.id
    ? liveEvents
    : progressRun?.id === historyRunCandidate?.id ? historyEvents : [];
  const noticeRun = hasConversation && loadedConversationId === conversationId ? run : undefined;
  const noticeEvents = noticeRun?.id === run?.id ? liveEvents : [];
  const fallbackEvent = [...noticeEvents].reverse().find(event => event.kind === "report.fallback");
  const failedEvent = [...noticeEvents].reverse().find(event => event.kind === "task.failed");
  const failed = noticeRun && ["failed", "interrupted"].includes(noticeRun.status);
  const noticeKey = noticeRun && (failed || fallbackEvent) ? `${conversationId}:${noticeRun.id}:${failed ? "failed" : "fallback"}` : undefined;
  const failureDetails = safeRunError(noticeRun?.error);
  const fallbackMessage = typeof fallbackEvent?.data.message === "string" ? fallbackEvent.data.message : "终稿未能完成，已保留已有研究结果。";
  const noticeMessage = failed ? (typeof failedEvent?.data.message === "string" && !/HTTP\s*\d/i.test(failedEvent.data.message) ? failedEvent.data.message : failureDetails.message) : fallbackMessage;
  const suggestionValue = failed ? failedEvent?.data.suggestion : fallbackEvent?.data.suggestion;
  const noticeSuggestion = typeof suggestionValue === "string" && suggestionValue ? suggestionValue : failed ? failureDetails.suggestion : /模型不存在或无法访问/.test(fallbackMessage) ? "请检查手动填写的模型 ID 及访问权限，或选择其他模型。" : "请检查服务配置后重试。";
  const noticeVisible = !!noticeKey && !dismissed.has(noticeKey);
  useEffect(() => {
    try { setDismissed(new Set(JSON.parse(sessionStorage.getItem("tufa.dismissedResearchNotices") || "[]"))); } catch { /* Storage may be unavailable. */ }
  }, []);
  useEffect(() => {
    setClosingNotice(false);
    return () => { if (noticeTimer.current) clearTimeout(noticeTimer.current); noticeTimer.current = null; };
  }, [noticeKey]);
  const closeNotice = () => {
    if (!noticeKey || noticeTimer.current || closingNotice) return;
    const finish = () => {
      noticeTimer.current = null;
      setDismissed(previous => {
        const next = new Set(previous).add(noticeKey);
        try { sessionStorage.setItem("tufa.dismissedResearchNotices", JSON.stringify([...next])); } catch { /* Storage may be unavailable. */ }
        return next;
      });
      setClosingNotice(false);
    };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { finish(); return; }
    setClosingNotice(true);
    noticeTimer.current = setTimeout(finish, 180);
  };
  const quickDraft = run?.research_mode === "quick"
    ? buildModelPreviews(liveEvents, run.research_mode).find(preview => preview.node === "dispatcher")?.text
    : undefined;
  const openProgressPanel = () => {
    setResearchPanelOpen(true);
    requestAnimationFrame(() => requestAnimationFrame(() => progressCloseButton.current?.focus()));
  };
  const closeProgressPanel = () => {
    setResearchPanelOpen(false);
    requestAnimationFrame(() => progressToggle.current?.focus());
  };

  useEffect(() => {
    const previous = selected.current;
    const projectChangedInNewStudy = previous.conversationId === undefined && conversationId === undefined && previous.projectId !== projectId && previous.resetKey === resetKey;
    selected.current = { projectId, conversationId, resetKey };
    if (projectChangedInNewStudy) return;
    if (previous.projectId === projectId && previous.conversationId === conversationId && previous.resetKey === resetKey) return;

    const marker = ++selectionEpoch.current;
    const retainingSubmittedConversation = previous.conversationId === undefined && conversationId !== undefined && submittedConversationRef.current === conversationId;
    actionController.current?.abort();
    actionController.current = null;
    requestedHistoryRuns.current.clear();
    if (retainingSubmittedConversation) {
      setLoadedConversationId(conversationId);
      setLoadingConversation(false);
      setBusy("");
      submittedConversationRef.current = undefined;
      return;
    }
    setMessages([]);
    setRuns([]);
    setHistoryRuns({});
    setRun(undefined);
    setLoadedConversationId(undefined);
    setResearchPanelOpen(false);
    setQuery("");
    setBusy("");
    if (!projectId || conversationId === undefined) {
      setLoadingConversation(false);
      return;
    }

    const controller = new AbortController();
    setLoadingConversation(true);
    Promise.all([
      api<Message[]>(`/conversations/${conversationId}/messages`, { signal: controller.signal }),
      api<Run[]>(`/projects/${projectId}/runs?conversation_id=${conversationId}`, { signal: controller.signal }),
    ]).then(([nextMessages, nextRuns]) => {
      if (controller.signal.aborted || selectionEpoch.current !== marker) return;
      setMessages(nextMessages);
      setRuns(nextRuns);
      const nextRun = nextRuns.find(item => item.conversation_id === conversationId);
      setRun(nextRun);
      setLoadedConversationId(conversationId);
      const shouldOpenPanel = !!nextRun
        && isDeepResearchRun(nextRun)
        && ["queued", "pending", "running"].includes(nextRun.status);
      if (shouldOpenPanel && nextRun?.research_mode === "auto") autoOpenedRuns.current.add(nextRun.id);
      setResearchPanelOpen(shouldOpenPanel);
    }).catch(error => {
      if (!controller.signal.aborted && selectionEpoch.current === marker) onError(messageOf(error));
    }).finally(() => {
      if (!controller.signal.aborted && selectionEpoch.current === marker) setLoadingConversation(false);
    });
    return () => controller.abort();
  }, [projectId, conversationId, resetKey, onError]);

  useEffect(() => () => {
    selectionEpoch.current++;
    actionController.current?.abort();
    actionController.current = null;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const ids = [...new Set(messages.filter(message => message.role === "assistant" && message.run_id).map(message => message.run_id as number))];
    const missing = ids.filter(id => !runs.some(item => item.id === id) && !requestedHistoryRuns.current.has(id));
    if (!missing.length) return;
    missing.forEach(id => requestedHistoryRuns.current.add(id));
    Promise.all(missing.map(id => api<Run>(`/runs/${id}`, { signal: controller.signal }).catch(() => undefined))).then(values => {
      if (!controller.signal.aborted) setHistoryRuns(previous => ({ ...previous, ...Object.fromEntries(values.filter((value): value is Run => !!value).map(value => [value.id, value])) }));
    });
    return () => {
      controller.abort();
      missing.forEach(id => requestedHistoryRuns.current.delete(id));
    };
  }, [messages, runs, projectId]);

  useEffect(() => {
    if (run?.status !== "succeeded" && run?.status !== "success" && run?.status !== "completed") return;
    if (conversationId === undefined) return;
    const controller = new AbortController();
    const marker = selectionEpoch.current;
    api<Message[]>(`/conversations/${conversationId}/messages`, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted && selectionEpoch.current === marker) setMessages(data);
    }).catch(error => {
      if (!controller.signal.aborted && selectionEpoch.current === marker) onError(messageOf(error));
    });
    onHistoryUpdated();
    return () => controller.abort();
  }, [run?.id, run?.status, conversationId, projectId, onError, onHistoryUpdated]);

  useEffect(() => {
    if (messagesElement.current) messagesElement.current.scrollTop = messagesElement.current.scrollHeight;
  }, [messages, run?.status]);

  useEffect(() => {
    const element = textarea.current;
    if (!element) return;
    const resize = () => {
      if (query.length === 0) {
        element.style.height = "";
        return;
      }
      element.style.height = "auto";
      element.style.height = `${Math.min(element.scrollHeight, 220)}px`;
    };
    resize();
    if (query.length === 0) return;

    const container = element.form;
    if (!container || typeof ResizeObserver === "undefined") return;
    let previousWidth = container.clientWidth;
    const observer = new ResizeObserver(entries => {
      const nextWidth = entries[0]?.contentRect.width ?? container.clientWidth;
      if (Math.abs(nextWidth - previousWidth) < 0.5) return;
      previousWidth = nextWidth;
      resize();
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [query]);

  useRunPolling({
    run,
    projectId,
    conversationId,
    fetchRun: (id, signal) => api<Run>(`/runs/${id}`, { signal }),
    onUpdate: next => {
      setRun(next);
      setRuns(previous => previous.map(item => item.id === next.id ? next : item));
    },
    onError: error => onError(messageOf(error)),
  });

  useEffect(() => {
    if (!activeRun || run?.research_mode !== "auto" || !isDeepResearchRun(run, liveEvents) || autoOpenedRuns.current.has(run.id)) return;
    autoOpenedRuns.current.add(run.id);
    setResearchPanelOpen(true);
  }, [activeRun, run?.id, run?.research_mode, run?.stage, run?.subtasks, liveEvents]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim() || busy || projectLoading || activeRun) return;
    const question = query.trim();
    const submittedConversation = conversationId;
    const marker = selectionEpoch.current;
    actionController.current?.abort();
    const controller = new AbortController();
    actionController.current = controller;
    setBusy("send");
    try {
      const targetProjectId = projectId ?? await onEnsureProject();
      if (controller.signal.aborted || selectionEpoch.current !== marker || selected.current.conversationId !== submittedConversation) return;
      const submitted = await api<{ run_id: number; conversation_id: number }>(
        `/projects/${targetProjectId}/questions`,
        { ...json({
          question,
          conversation_id: submittedConversation ?? null,
          research_mode: mode,
          research_depth: researchDepth,
          prefer_primary_evidence: preferPrimaryEvidence,
          relevance_filter: relevanceFilter,
          model_name: modelChoice.model_name.trim() || undefined,
        }), signal: controller.signal },
      );
      const [nextRun, nextMessages, nextRuns] = await Promise.all([
        api<Run>(`/runs/${submitted.run_id}`, { signal: controller.signal }),
        api<Message[]>(`/conversations/${submitted.conversation_id}/messages`, { signal: controller.signal }),
        api<Run[]>(`/projects/${targetProjectId}/runs?conversation_id=${submitted.conversation_id}`, { signal: controller.signal }),
      ]);
      onHistoryUpdated();
      if (controller.signal.aborted || selectionEpoch.current !== marker || selected.current.conversationId !== submittedConversation) return;
      setQuery("");
      setMessages(nextMessages);
      setRuns(nextRuns);
      setRun(nextRun);
      setLoadedConversationId(submitted.conversation_id);
      const autoDeepActive = nextRun.research_mode === "auto"
        && ["queued", "pending", "running"].includes(nextRun.status)
        && isDeepResearchRun(nextRun);
      const shouldOpenPanel = nextRun.research_mode === "deep" || autoDeepActive;
      if (autoDeepActive) autoOpenedRuns.current.add(nextRun.id);
      setResearchPanelOpen(shouldOpenPanel);
      submittedConversationRef.current = submitted.conversation_id;
      onConversationChange(submitted.conversation_id, targetProjectId);
    } catch (error) {
      if (!controller.signal.aborted && selectionEpoch.current === marker) onError(messageOf(error));
    } finally {
      if (actionController.current === controller) actionController.current = null;
      if (selectionEpoch.current === marker) setBusy("");
    }
  };

  const retry = () => {
    if (!run || busy) return;
    const marker = selectionEpoch.current;
    actionController.current?.abort();
    const controller = new AbortController();
    actionController.current = controller;
    setBusy("retry");
    void api<Run>(`/runs/${run.id}/retry`, { method: "POST", signal: controller.signal }).then(next => {
      if (selectionEpoch.current !== marker) return;
      setRun(next);
      setRuns(previous => [next, ...previous.filter(item => item.id !== next.id)]);
      const autoDeepActive = next.research_mode === "auto"
        && ["queued", "pending", "running"].includes(next.status)
        && isDeepResearchRun(next);
      if (run.research_mode === "deep" || next.research_mode === "deep" || autoDeepActive) {
        if (autoDeepActive) autoOpenedRuns.current.add(next.id);
        setResearchPanelOpen(true);
      }
      onHistoryUpdated();
    }).catch(error => {
      if (!controller.signal.aborted && selectionEpoch.current === marker) onError(messageOf(error));
    }).finally(() => {
      if (actionController.current === controller) actionController.current = null;
      if (selectionEpoch.current === marker) setBusy("");
    });
  };

  const choosePreset = (value: string) => {
    setQuery(value);
    textarea.current?.focus();
    requestAnimationFrame(() => textarea.current?.setSelectionRange(value.length, value.length));
  };

  const submitOnEnter = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    const shouldSubmit = enterToSend ? !event.shiftKey : event.ctrlKey || event.metaKey;
    if (!shouldSubmit) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  };

  const presets = [
    { text: "2026 年 AI Agent 有哪些值得关注的研究方向？", prompt: "2026 年 AI Agent 领域有哪些值得关注的研究方向？", tone: "cyan", icon: "✧" },
    { text: "梳理 GraphRAG 的关键技术与代表论文", prompt: "梳理 GraphRAG 的关键技术、代表论文与应用场景。", tone: "blue", icon: "⌘" },
    { text: "比较 Transformer 与 Mamba 的架构优劣", prompt: "系统比较 Transformer 与 Mamba 的架构特点、性能和适用场景。", tone: "violet", icon: "◇" },
    { text: "视觉语言模型在科研中有哪些新应用？", prompt: "总结视觉语言模型在科研工作中的典型应用与发展趋势。", tone: "pink", icon: "▤" },
  ];

  return <main className={`${styles.workspace} ${hasConversation ? styles.workspaceConversation : ""} ${density === "compact" ? styles.compact : ""} ${progressRun ? styles.workspaceResearchLayout : ""} ${progressRun && researchPanelOpen ? styles.workspaceWithPanel : ""}`}>
    {noticeVisible && typeof document !== "undefined" && createPortal(<div className={`${styles.researchNotice} ${closingNotice ? styles.researchNoticeClosing : ""}`}>
      <div role="status" aria-live="polite"><strong>{failed ? "研究任务未完成" : "报告已降级"}</strong><p>{noticeMessage}</p><p>{noticeSuggestion}</p></div>
      <button type="button" aria-label="关闭研究通知" onClick={closeNotice}>×</button>
    </div>, document.body)}
    <div className={styles.chatColumn}>
    {!hasConversation ? <section className={styles.welcome} aria-label="开始一项新研究">
      <div className={`${styles.heroLight} ${!ambient ? styles.heroLightMuted : ""}`} aria-hidden="true" />
      <HeroEmblem ambient={ambient} />
      <h1>今天，想<span>研究</span>什么？</h1>
    </section> : <section className={styles.conversation} aria-label="当前研究对话">
      <div className={styles.conversationTop}>
        <div><h2>{conversationTitle || "新的研究"}</h2></div>
        <div className={styles.conversationActions}>
          {progressRun && !researchPanelOpen && <button ref={progressToggle} className={styles.progressOpen} type="button" onClick={openProgressPanel}><svg className={styles.iconSmall} viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h10M4 18h7" /></svg>研究进度</button>}
          <button className={styles.conversationNew} type="button" onClick={onNewStudy}><svg className={styles.iconSmall} viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>新研究</button>
        </div>
      </div>
      <div ref={messagesElement} className={styles.messages} aria-live="polite">
        {loadingConversation && <p className={styles.loadingMessages}>正在读取研究记录…</p>}
        {messages.map(message => {
          const messageRun = message.run_id ? runs.find(item => item.id === message.run_id) || historyRuns[message.run_id] : undefined;
          const referenceIds = message.metadata?.report_id ? reportEvidenceIds(message.content) : new Set<string>();
          return <article className={`${styles.message} ${message.role === "user" ? styles.messageUser : styles.messageAssistant}`} key={message.id}>
            {message.role !== "user" && <div className={styles.avatar} aria-hidden="true">✧</div>}
            <div className={styles.messageBubble}>
              <div className={styles.messageLabel}>{message.role === "user" ? "你" : `TUFA · ${modeText(messageRun?.research_mode)}`}</div>
              {message.role === "assistant" && message.run_id && <ResearchActivity items={messageRun?.subtasks} />}
              <div className={`${styles.messageContent} ${message.metadata?.report_id ? styles.messageMarkdown : ""}`}>
                {message.role === "assistant" && message.metadata?.report_id ? <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
                  h2: ({ node, children }) => <h2 className={node?.children.length === 1 && node.children[0].type === "text" && node.children[0].value === "参考文献" ? styles.referencesHeading : undefined}>{children}</h2>,
                  a: ({ href, children }) => {
                    const safeHref = reportLink(href);
                    return safeHref ? <a href={safeHref} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
                  },
                  img: () => null,
                }}>{displayReport(message.content, message.run_id === noticeRun?.id && !!fallbackEvent)}</ReactMarkdown> : message.content}
              </div>
              {message.metadata?.evidence?.map((citation, index) => {
                if (citation.id && referenceIds.has(citation.id)) return null;
                const href = citation.version_id ? `/api/paper-versions/${citation.version_id}/file${citation.page ? `#page=${citation.page}` : ""}` : safeSourceUrl(citation.original_url);
                return <div className={styles.citation} key={`${citation.id || index}-${index}`}>
                  {href ? <a href={href} target="_blank" rel="noopener noreferrer">{citation.title || `参考文献 ${index + 1}`} ↗</a> : <span>{citation.title || `参考文献 ${index + 1}`}</span>}
                  {citation.id && ` [${citation.id}]`}{citation.page && ` · 第 ${citation.page} 页`}{citation.source_level && ["abstract", "web_snippet", "web_content"].includes(citation.source_level) && ` · ${webSourceLabel(citation.source_level)}`}{citation.provider && ` · ${citation.provider}`}{citation.retrieved_at && ` · 检索于 ${new Date(citation.retrieved_at).toLocaleString("zh-CN")}`}
                </div>;
              })}
              {message.metadata?.report_id && <div className={styles.reportLinks}>
                <a href={`/api/reports/${message.metadata.report_id}/markdown`}>下载研究报告</a>
                <a href={`/api/reports/${message.metadata.report_id}/evidence.csv`}>下载证据表</a>
              </div>}
            </div>
          </article>;
        })}
        {activeRun && run.research_mode === "quick" && quickDraft && <article className={`${styles.message} ${styles.messageAssistant}`} key={`quick-preview-${run.id}`} aria-live="polite">
          <div className={styles.avatar} aria-hidden="true">✧</div>
          <div className={styles.messageBubble}>
            <div className={styles.messageLabel}>TUFA · 快速探索 <small className={styles.streamPreviewLabel}>实时草稿 · 未核验</small></div>
            <div className={styles.messageContent}>{quickDraft}</div>
          </div>
        </article>}
        {!loadingConversation && messages.length === 0 && <p className={styles.loadingMessages}>此会话暂无消息。</p>}
        {activeRun && <div className={styles.liveStatus} role="status"><span className={`${styles.spinner} ${styles.spinnerResearch}`} />
          <span>{statusText[run.status] || run.status}{stageSuffix(run)}</span>
          <small>{modeText(run.research_mode)}</small>
        </div>}
      </div>
    </section>}

    <div className={`${styles.chatBottom} ${hasConversation ? styles.chatBottomConversation : ""}`}>
      {run && !["succeeded", "success", "completed"].includes(run.status) && !activeRun && <div className={styles.runNotice} role="status">
        <div><strong>{statusText[run.status] || run.status}</strong>{stageSuffix(run) && <span>{stageSuffix(run)}</span>}{run.research_mode && <span> · {modeText(run.research_mode)}</span>}{run.error && <p>{safeRunError(run.error).message}</p>}</div>
        {(["failed", "interrupted"].includes(run.status)) && <button type="button" disabled={!!busy} onClick={retry}>{busy === "retry" ? "重新执行中…" : "重新执行"}</button>}
      </div>}
      <form className={styles.composer} onSubmit={submit} autoComplete="off">
        <label className={styles.visuallyHidden} htmlFor="promptInput">输入你的研究问题</label>
        <textarea
          ref={textarea}
          id="promptInput"
          rows={2}
          value={query}
          disabled={projectLoading || !!busy || !!activeRun}
          onChange={event => setQuery(event.target.value)}
          onKeyDown={submitOnEnter}
          placeholder={mode === "deep" ? "提出一个问题，开始深度研究..." : mode === "quick" ? "提出一个问题，快速获得直接回答..." : "提出一个问题，自动判断是否需要研究..."}
          aria-label="输入你的研究问题"
        />
        <div className={styles.composerToolbar}>
          <ModePicker mode={mode} disabled={!!busy || !!activeRun} onModeChange={onModeChange} />
          <div className={styles.composerRight}><span className={styles.shortcut}>{enterToSend ? "ENTER ↵ TO START" : "CTRL + ENTER TO START"}</span>
            <button className={`${styles.sendButton} ${query.trim() && !busy && !activeRun && !projectLoading ? styles.sendButtonReady : ""}`} type="submit" disabled={!query.trim() || !!busy || !!activeRun || projectLoading} aria-label="发送研究问题">
              {busy === "send" ? <span className={styles.spinner} /> : <svg className={styles.sendIcon} viewBox="0 0 24 24" aria-hidden="true"><path d="m6 12 6-6 6 6M12 6v12" /></svg>}
            </button>
          </div>
        </div>
      </form>
      {!hasConversation && <>
        <div className={styles.presetsHeading}><span>TRY ASKING TUFA</span><span>从灵感开始探索 ↗</span></div>
        <div className={styles.presets} aria-label="预设研究问题">
          {presets.map(preset => <button key={preset.prompt} data-research-preset className={styles.preset} type="button" onClick={() => choosePreset(preset.prompt)}>
            <span className={`${styles.presetIcon} ${styles[preset.tone]}`}>{preset.icon}</span><span>{preset.text}</span><svg className={styles.presetArrow} viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6" /></svg>
          </button>)}
        </div>
      </>}
    </div>
    </div>
    {progressRun && <ResearchPanel
      key={progressRun.id}
      run={progressRun}
      report={progressReport}
      events={progressEvents}
      open={researchPanelOpen}
      status={statusText[progressRun.status] || progressRun.status}
      stage={stageLabel(progressRun)}
      active={["queued", "pending", "running"].includes(progressRun.status)}
      onClose={closeProgressPanel}
      closeButtonRef={progressCloseButton}
    />}
  </main>;
}
