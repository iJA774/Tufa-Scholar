"use client";

import { type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type RefObject, useEffect, useRef, useState } from "react";
import { useRunPolling } from "../hooks/useRunPolling";
import { api, json, messageOf } from "./api";
import type { Message, ResearchMode, Run, Subtask } from "./types";
import type { ModelChoice } from "./preferences";
import { webSourceLabel } from "./sourceLabels";
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

function modeText(mode?: ResearchMode | "auto") {
  if (mode === "quick") return "快速探索";
  if (mode === "deep") return "深度研究";
  if (mode === "auto") return "自动判断";
  return "研究任务";
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

function ResearchProgressPanel({ run, onClose, closeButtonRef }: { run: Run; onClose: () => void; closeButtonRef: RefObject<HTMLButtonElement | null> }) {
  const active = ["queued", "pending", "running"].includes(run.status);
  const status = statusText[run.status] || run.status;
  const subtasks = run.subtasks || [];
  return <aside className={styles.progressPanel} aria-label="研究进度">
    <header className={styles.progressHeader}>
      <div><h2>研究进度</h2><p>{modeText(run.research_mode)}</p></div>
      <button ref={closeButtonRef} className={styles.progressClose} type="button" onClick={onClose} aria-label="收起研究进度">×</button>
    </header>
    <div className={styles.progressBody}>
      <section className={styles.progressStage}>
        <span className={`${styles.progressMarker} ${active ? styles.progressMarkerActive : ""}`} aria-hidden="true" />
        <div><strong>{stageLabel(run)}</strong>{status !== stageLabel(run) && <small>{status}</small>}</div>
      </section>
      {run.error && <p className={styles.progressError}>{run.error}</p>}
      {subtasks.length ? <ol className={styles.progressSteps}>
        {subtasks.map(step => <li key={step.index}>
          <span className={`${styles.progressMarker} ${styles.progressMarkerDone}`} aria-hidden="true" />
          <div>
            <strong>{step.index}. {step.task}</strong>
            {step.findings?.map((finding, index) => <p key={index}>{finding.text}{finding.citations.length ? ` ${finding.citations.map(id => `[${id}]`).join(" ")}` : ""}</p>)}
            {step.status === "sources_only" && <p>已找到相关资料，结论仍待核验。</p>}
            {step.status === "no_evidence" && <p>本步暂无可用证据。</p>}
            {step.sources?.map(source => {
              const href = safeSourceUrl(source.original_url);
              return <p className={styles.progressSource} key={source.id}>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{source.title || source.id} ↗</a> : source.title || source.id} [{source.id}]</p>;
            })}
            {step.gap && <p>待查：{step.gap}</p>}
          </div>
        </li>)}
      </ol> : <p className={styles.progressEmpty}>{active ? "正在等待研究步骤…" : "此任务没有返回研究步骤。"}</p>}
    </div>
  </aside>;
}

type Props = {
  projectId?: number;
  projectLoading: boolean;
  conversationId?: number;
  conversationTitle?: string;
  resetKey: number;
  mode: ResearchMode;
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
  const [query, setQuery] = useState("");
  const [run, setRun] = useState<Run>();
  const [loadedConversationId, setLoadedConversationId] = useState<number>();
  const [researchPanelOpen, setResearchPanelOpen] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]);
  const [historyRuns, setHistoryRuns] = useState<Record<number, Run>>({});
  const [busy, setBusy] = useState("");
  const [loadingConversation, setLoadingConversation] = useState(false);
  const requestedHistoryRuns = useRef(new Set<number>());
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
  const modeName = modeText(mode);
  const progressRun = hasConversation && loadedConversationId === conversationId && run?.research_mode === "deep" ? run : undefined;
  const openProgressPanel = () => {
    setResearchPanelOpen(true);
    requestAnimationFrame(() => progressCloseButton.current?.focus());
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
      setResearchPanelOpen(nextRun?.research_mode === "deep" && ["queued", "pending", "running"].includes(nextRun.status));
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
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 220)}px`;
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
          model_name: modelChoice.model_name.trim() || undefined,
          reasoning_effort: modelChoice.reasoning_effort || undefined,
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
      setResearchPanelOpen(nextRun.research_mode === "deep");
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
      if (run.research_mode === "deep" || next.research_mode === "deep") setResearchPanelOpen(true);
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

  return <main className={`${styles.workspace} ${hasConversation ? styles.workspaceConversation : ""} ${density === "compact" ? styles.compact : ""} ${progressRun && researchPanelOpen ? styles.workspaceWithPanel : ""}`}>
    <div className={styles.chatColumn}>
    {!hasConversation ? <section className={styles.welcome} aria-label="开始一项新研究">
      <div className={`${styles.heroLight} ${!ambient ? styles.heroLightMuted : ""}`} aria-hidden="true" />
      <div className={`${styles.heroEmblem} ${!ambient ? styles.heroEmblemMuted : ""}`} aria-hidden="true">
        <svg className={styles.emblemSvg} viewBox="0 0 38 38" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path className={styles.emblemStar} d="M19 2 23 14.6 36 19l-13 4.3L19 36l-4.2-12.7L2 19l12.8-4.4L19 2Z" stroke={ambient ? "url(#emblemGradFlow)" : "url(#emblemGradStatic)"} strokeWidth="1.5" />
          <circle className={styles.emblemCore} cx="19" cy="19" r="3.1" fill="#B7A6FF" /><circle className={styles.emblemDotOne} cx="5" cy="5" r="1" fill="#68DCED" /><circle className={styles.emblemDotTwo} cx="34" cy="7" r="1.2" fill="#D47AFF" /><circle className={styles.emblemDotThree} cx="31" cy="33" r="1" fill="#F5A1C7" />
          <defs>
            <linearGradient id="emblemGradFlow" gradientUnits="userSpaceOnUse" x1="0" y1="19" x2="38" y2="19">
              <stop stopColor="#68DDEB" /><stop offset=".33" stopColor="#A89DFF" /><stop offset=".66" stopColor="#F89AC3" /><stop offset="1" stopColor="#68DDEB" />
              {ambient && <animateTransform attributeName="gradientTransform" type="rotate" from="0 19 19" to="360 19 19" dur="12s" repeatCount="indefinite" />}
            </linearGradient>
            <linearGradient id="emblemGradStatic" gradientUnits="userSpaceOnUse" x1="2" y1="13" x2="36" y2="26"><stop stopColor="#68DDEB" /><stop offset=".48" stopColor="#A89DFF" /><stop offset="1" stopColor="#F89AC3" /></linearGradient>
          </defs>
        </svg>
        <span className={`${styles.firefly} ${styles.fireflyOne}`} /><span className={`${styles.firefly} ${styles.fireflyTwo}`} /><span className={`${styles.firefly} ${styles.fireflyThree}`} /><span className={`${styles.firefly} ${styles.fireflyFour}`} />
      </div>
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
          return <article className={`${styles.message} ${message.role === "user" ? styles.messageUser : styles.messageAssistant}`} key={message.id}>
            {message.role !== "user" && <div className={styles.avatar} aria-hidden="true">✧</div>}
            <div className={styles.messageBubble}>
              <div className={styles.messageLabel}>{message.role === "user" ? "你" : `TUFA · ${modeText(messageRun?.research_mode)}`}</div>
              {message.role === "assistant" && message.run_id && <ResearchActivity items={messageRun?.subtasks} />}
              <div className={styles.messageContent}>{message.content}</div>
              {message.metadata?.evidence?.map((citation, index) => {
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
        {!loadingConversation && messages.length === 0 && <p className={styles.loadingMessages}>此会话暂无消息。</p>}
        {activeRun && <div className={styles.liveStatus} role="status"><span className={styles.spinner} />
          <span>{statusText[run.status] || run.status}{stageSuffix(run)}</span>
          <small>{modeText(run.research_mode)}</small>
        </div>}
      </div>
    </section>}

    <div className={`${styles.chatBottom} ${hasConversation ? styles.chatBottomConversation : ""}`}>
      {run && !["succeeded", "success", "completed"].includes(run.status) && !activeRun && <div className={styles.runNotice} role="status">
        <div><strong>{statusText[run.status] || run.status}</strong>{stageSuffix(run) && <span>{stageSuffix(run)}</span>}{run.research_mode && <span> · {modeText(run.research_mode)}</span>}{run.error && <p>{run.error}</p>}</div>
        {(["failed", "interrupted"].includes(run.status)) && <button type="button" disabled={!!busy} onClick={retry}>{busy === "retry" ? "重新执行中…" : "重新执行"}</button>}
      </div>}
      {run && activeRun && run.subtasks?.length ? <div className={styles.currentActivity}><ResearchActivity items={run.subtasks} /></div> : null}
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
          placeholder={mode === "deep" ? "提出一个问题，开始深度研究..." : "提出一个问题，快速获得直接回答..."}
          aria-label="输入你的研究问题"
        />
        <div className={styles.composerToolbar}>
          <button
            className={styles.modeSwitch}
            type="button"
            title="切换研究模式"
            aria-label={`当前模式：${modeName}。切换为${mode === "deep" ? "快速探索" : "深度研究"}`}
            aria-pressed={mode === "quick"}
            disabled={!!busy || !!activeRun}
            onClick={() => onModeChange(mode === "deep" ? "quick" : "deep")}
          ><svg className={styles.modeSpark} viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 1.8 7.2L21 11l-7.2 1.8L12 20l-1.8-7.2L3 11l7.2-1.8L12 2Z" /></svg><span>{modeName}</span><svg className={styles.modeChevron} viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg></button>
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
          {presets.map(preset => <button key={preset.prompt} className={styles.preset} type="button" onClick={() => choosePreset(preset.prompt)}>
            <span className={`${styles.presetIcon} ${styles[preset.tone]}`}>{preset.icon}</span><span>{preset.text}</span><svg className={styles.presetArrow} viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6" /></svg>
          </button>)}
        </div>
      </>}
    </div>
    </div>
    {progressRun && researchPanelOpen && <ResearchProgressPanel run={progressRun} onClose={closeProgressPanel} closeButtonRef={progressCloseButton} />}
  </main>;
}
