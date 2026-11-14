// Adapted from ByteDance DeerFlow's ResearchBlock, ResearchActivitiesBlock,
// LoadingAnimation, RainbowText, Card, and Tabs components. See THIRD_PARTY_LICENSES/DEERFLOW-MIT-LICENSE.txt.
// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates

"use client";

import { type CSSProperties, type KeyboardEvent, type RefObject, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message, Run } from "./types";
import { buildModelPreviews, type RunEvent } from "../hooks/runEvents";
import styles from "./ResearchPanel.module.css";

type Props = {
  run: Run;
  report?: Message;
  events: RunEvent[];
  open: boolean;
  status: string;
  stage: string;
  active: boolean;
  onClose: () => void;
  closeButtonRef: RefObject<HTMLButtonElement | null>;
};

function safeSourceUrl(value?: string | null) {
  try {
    const url = new URL(value || "");
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function subtaskStatus(status: string) {
  if (status === "findings") return "已整理发现";
  if (status === "sources_only") return "已找到来源";
  if (status === "no_evidence") return "暂无证据";
  return status;
}

const nodeLabels: Record<string, string> = {
  background_investigator: "背景检索",
  planner: "研究规划",
  supervisor: "研究调度",
  researcher: "搜索与证据整理",
  analyst: "证据分析",
  coder: "研究检查",
  reporter: "报告整理",
};

const toolLabels: Record<string, string> = {
  search_web: "网页检索",
  read_web_page: "读取网页",
  search_papers: "学术检索",
  search_local_papers: "检索本地文献",
  collect_evidence: "提取证据",
};

function textField(data: Record<string, unknown> | undefined, key: string) {
  return data && typeof data[key] === "string" ? data[key] as string : "";
}

function sourceName(source: string) {
  if (source === "web") return "网页资料";
  if (source === "paper") return "学术资料";
  return source;
}

function actionName(action: string) {
  if (action === "research") return "继续研究";
  if (action === "report") return "整理报告";
  return action;
}

function eventHeading(event: RunEvent) {
  const node = textField(event.data, "node");
  const tool = textField(event.data, "tool");
  if (event.kind === "node.started") return `${nodeLabels[node] || node || "研究节点"}开始`;
  if (event.kind === "node.completed") return `${nodeLabels[node] || node || "研究节点"}完成`;
  if (event.kind === "tool.started") return `正在${toolLabels[tool] || tool || "调用工具"}`;
  if (event.kind === "tool.completed") return `${toolLabels[tool] || tool || "工具调用"}完成`;
  if (event.kind === "tool.failed") return `${toolLabels[tool] || tool || "工具调用"}失败`;
  if (event.kind === "model.failed") return `${nodeLabels[node] || node || "模型输出"}生成失败`;
  return "研究活动";
}

function eventState(event: RunEvent) {
  if (event.kind.endsWith("failed")) return "失败";
  if (event.kind.endsWith("completed")) return "完成";
  if (event.kind.endsWith("started")) return "进行中";
  return "";
}

type ActivityEntry = { event: RunEvent; ending?: RunEvent };

function mergeActivityEvents(events: RunEvent[]) {
  const entries: ActivityEntry[] = [];
  const openNodes = new Map<string, number[]>();
  const openTools = new Map<string, number>();
  for (const event of events) {
    if (event.kind.startsWith("model.")) {
      if (event.kind === "model.failed" && event.data.node !== "dispatcher") entries.push({ event });
      continue;
    }
    const node = textField(event.data, "node");
    if (node === "dispatcher" || event.kind === "dispatch") continue;
    if (event.kind === "node.started") {
      const index = entries.push({ event }) - 1;
      openNodes.set(node, [...(openNodes.get(node) || []), index]);
      continue;
    }
    if (event.kind === "node.completed") {
      const candidates = openNodes.get(node);
      const index = candidates?.pop();
      if (index !== undefined) entries[index].ending = event;
      else entries.push({ event });
      continue;
    }
    if (event.kind === "tool.started") {
      const index = entries.push({ event }) - 1;
      const callId = textField(event.data, "call_id");
      if (callId) openTools.set(callId, index);
      continue;
    }
    if (event.kind === "tool.completed" || event.kind === "tool.failed") {
      const callId = textField(event.data, "call_id");
      const index = callId ? openTools.get(callId) : undefined;
      if (index !== undefined) {
        entries[index].ending = event;
        openTools.delete(callId);
      } else entries.push({ event });
    }
  }
  return entries;
}

export default function ResearchPanel({ run, report, events, open, status, stage, active, onClose, closeButtonRef }: Props) {
  const hasReport = !!report?.metadata?.report_id;
  const activityPreviews = buildModelPreviews(events, run.research_mode)
    .filter(preview => !report?.metadata?.report_id || active)
    .reduce((latest, preview) => {
      const index = latest.findIndex(item => item.node === preview.node);
      if (index === -1) latest.push(preview);
      else latest[index] = preview;
      return latest;
    }, [] as ReturnType<typeof buildModelPreviews>);
  const reportPreview = activityPreviews.find(preview => preview.node === "reporter");
  const reportTabAvailable = hasReport || !!reportPreview;
  const [tab, setTab] = useState<"report" | "activities">(reportTabAvailable ? "report" : "activities");
  const tabWasChosen = useRef(false);
  const [hasEntered, setHasEntered] = useState(false);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const activityViewport = useRef<HTMLElement>(null);
  const followActivity = useRef(true);
  const reportId = report?.metadata?.report_id;
  const reportTabId = `research-report-tab-${run.id}`;
  const activityTabId = `research-activity-tab-${run.id}`;
  const reportPanelId = `research-report-panel-${run.id}`;
  const activityPanelId = `research-activity-panel-${run.id}`;

  useEffect(() => {
    tabWasChosen.current = false;
  }, [run.id]);

  useEffect(() => {
    if (!tabWasChosen.current) setTab(reportTabAvailable ? "report" : "activities");
  }, [run.id, report?.id, reportPreview?.key, hasReport, reportTabAvailable]);

  useEffect(() => {
    if (!open || hasEntered) return;
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => setHasEntered(true));
    });
    return () => {
      cancelAnimationFrame(firstFrame);
      if (secondFrame) cancelAnimationFrame(secondFrame);
    };
  }, [open, hasEntered]);

  useEffect(() => {
    const viewport = activityViewport.current;
    if (!open || tab !== "activities" || !viewport || !followActivity.current) return;
    viewport.scrollTop = viewport.scrollHeight;
  }, [open, tab, run.status, run.stage, run.subtasks, events]);

  const changeTab = (next: "report" | "activities") => {
    if (next === "report" && !reportTabAvailable) return;
    tabWasChosen.current = true;
    setTab(next);
  };

  const handleTabKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next = event.key === "ArrowRight" ? (tab === "report" ? "activities" : "report") : (tab === "activities" && reportTabAvailable ? "report" : "activities");
    changeTab(next);
    requestAnimationFrame(() => tabRefs.current[next === "report" ? 0 : 1]?.focus());
  };

  const subtasks = run.subtasks || [];
  const timeline = mergeActivityEvents(events);
  const expanded = open && hasEntered;
  const className = `${styles.researchPanel} ${expanded ? styles.researchPanelOpen : styles.researchPanelClosed}`;
  return <aside className={className} aria-label="研究报告与活动" aria-hidden={!open} inert={!open}>
    <div className={styles.researchCard}>
      <div className={styles.cardToolbar}>
        <div className={styles.tabs} role="tablist" aria-label="研究内容">
          <button ref={node => { tabRefs.current[0] = node; }} id={reportTabId} className={`${styles.tab} ${tab === "report" ? styles.tabActive : ""}`} type="button" role="tab" aria-selected={tab === "report"} aria-controls={reportPanelId} tabIndex={tab === "report" ? 0 : -1} disabled={!reportTabAvailable} onClick={() => changeTab("report")} onKeyDown={handleTabKey}>报告</button>
          <button ref={node => { tabRefs.current[1] = node; }} id={activityTabId} className={`${styles.tab} ${tab === "activities" ? styles.tabActive : ""}`} type="button" role="tab" aria-selected={tab === "activities"} aria-controls={activityPanelId} tabIndex={tab === "activities" ? 0 : -1} onClick={() => changeTab("activities")} onKeyDown={handleTabKey}>活动</button>
        </div>
        <button ref={closeButtonRef} className={styles.closeButton} type="button" aria-label="关闭研究面板" onClick={onClose}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
      </div>

      <section id={reportPanelId} role="tabpanel" aria-labelledby={reportTabId} hidden={tab !== "report"} tabIndex={0} className={`${styles.tabPanel} ${styles.reportPanel}`}>
        {hasReport && report ? <>
          <article className={styles.reportContent}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
              a: ({ href, children }) => {
                const safeHref = safeSourceUrl(href);
                return safeHref ? <a href={safeHref} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
              },
              img: () => null,
            }}>{report.content}</ReactMarkdown>
          </article>
          {reportId && <div className={styles.reportDownloads}>
            <a href={`/api/reports/${reportId}/markdown`}>下载研究报告</a>
            <a href={`/api/reports/${reportId}/evidence.csv`}>下载证据表</a>
          </div>}
        </> : reportPreview ? <article className={`${styles.reportContent} ${styles.reportPreview}`}>
          <div className={styles.reportPreviewLabel}>实时草稿 · 未核验</div>
          <p>{reportPreview.text}</p>
        </article> : <p className={styles.emptyState}>报告生成后会显示在这里。</p>}
      </section>

      <section ref={activityViewport} id={activityPanelId} role="tabpanel" aria-labelledby={activityTabId} hidden={tab !== "activities"} tabIndex={0} onScroll={event => {
        const viewport = event.currentTarget;
        followActivity.current = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 48;
      }} className={`${styles.tabPanel} ${styles.activityPanel}`}>
        <div className={styles.activityStatus} role="status">
          {active && <span className={styles.loadingAnimation} aria-hidden="true"><i /><i /><i /></span>}
          <span className={`${styles.rainbowText} ${active ? styles.rainbowAnimated : ""}`}>{stage || status}</span>
          {status !== stage && <span className={styles.statusLabel}>{status}</span>}
        </div>
        {run.error && <p className={styles.runError}>{run.error}</p>}
        {timeline.length > 0 && <ol className={styles.liveActivityList} aria-label="实时研究活动">
          {timeline.map(({ event, ending }) => {
            const displayEvent = ending || event;
            const data = { ...event.data, ...(ending?.data || {}) };
            const combinedEvent = { ...displayEvent, data };
            const source = textField(data, "source");
            const action = textField(data, "action");
            const reason = textField(event.data, "reason");
            const query = textField(data, "query");
            const url = safeSourceUrl(textField(data, "url"));
            const title = textField(data, "title");
            const message = textField(data, "message");
            const node = textField(data, "node");
            const sourceRows = Array.isArray(data.sources) ? data.sources : [];
            const seenSourceKeys = new Set<string>();
            if (event.kind === "tool.started") {
              const startedUrl = safeSourceUrl(textField(event.data, "url"));
              const startedTitle = textField(event.data, "title").trim().toLocaleLowerCase();
              if (startedUrl) seenSourceKeys.add(`url:${startedUrl}`);
              if (startedTitle) seenSourceKeys.add(`title:${startedTitle}`);
            }
            const sourceItems = sourceRows.flatMap((value, index) => {
              if (!value || typeof value !== "object") return [];
              const item = value as Record<string, unknown>;
              const sourceTitle = typeof item.title === "string" ? item.title : "研究来源";
              const href = safeSourceUrl(typeof item.url === "string" ? item.url : undefined);
              const sourceKey = href ? `url:${href}` : `title:${sourceTitle.trim().toLocaleLowerCase()}`;
              if (seenSourceKeys.has(sourceKey)) return [];
              seenSourceKeys.add(sourceKey);
              return [{ key: `${event.id}-${index}`, title: sourceTitle, href }];
            });
            const isFailed = displayEvent.kind.endsWith("failed");
            const isComplete = !!ending || displayEvent.kind.endsWith("completed");
            return <li key={event.id} className={`${styles.liveActivityItem} ${isFailed ? styles.liveActivityFailed : isComplete ? styles.liveActivityComplete : ""}`}>
              <div className={styles.liveActivityHeading}>
                <span className={styles.liveActivityMark} aria-hidden="true" />
                <strong>{eventHeading(combinedEvent)}</strong>
                <small>{isFailed ? "失败" : isComplete ? "完成" : eventState(event)}</small>
              </div>
              {displayEvent.kind === "node.completed" && node === "planner" && (source || action) && <p className={styles.liveActivityDetail}>{[source && `选择${sourceName(source)}线路`, action && actionName(action)].filter(Boolean).join(" · ")}</p>}
              {message && <p className={styles.liveActivityDetail}>{message}</p>}
              {event.kind === "tool.started" && reason && <p className={styles.toolReason}>{reason}</p>}
              {query && <p className={styles.liveActivityDetail}>检索词：{query}</p>}
              {title && <p className={styles.liveActivityDetail}>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{title} ↗</a> : title}</p>}
              {url && !title && <p className={styles.liveActivityDetail}><a href={url} target="_blank" rel="noopener noreferrer">正在浏览网页 ↗</a></p>}
              {sourceItems.length > 0 && <ul className={styles.liveActivitySources}>
                {sourceItems.map(item => <li key={item.key}>{item.href ? <a href={item.href} target="_blank" rel="noopener noreferrer">{item.title} ↗</a> : <span>{item.title}</span>}</li>)}
              </ul>}
              {isFailed && message && <p className={styles.liveActivityError}>{message}</p>}
              {event !== timeline[timeline.length - 1]?.event && <hr />}
            </li>;
          })}
        </ol>}
        {activityPreviews.length > 0 && <section className={styles.liveDrafts} aria-label="模型实时输出">
          {activityPreviews.map(preview => <article className={styles.streamedDraft} key={preview.key}>
            <div><strong>{preview.node === "reporter" ? "报告草稿" : "研究草稿"}</strong><span>实时输出 · 未核验{preview.status === "complete" ? " · 已生成" : ""}</span></div>
            <p>{preview.text}</p>
          </article>)}
        </section>}
        {subtasks.length ? <ol className={styles.activityList}>
          {subtasks.map((step, index) => <li className={styles.activityItem} key={`${run.id}-${step.index}`} style={index < 10 ? { animationDelay: `${Math.min(index * .05, .5)}s` } as CSSProperties : undefined}>
            <h3>{step.task}</h3>
            <span className={styles.subtaskStatus}>{subtaskStatus(step.status)}</span>
            {step.findings?.map((finding, findingIndex) => <p key={`${step.index}-finding-${findingIndex}`}>{finding.text}{finding.citations.length ? ` ${finding.citations.map(id => `[${id}]`).join(" ")}` : ""}</p>)}
            {step.sources?.length > 0 && <ul className={styles.sourceChips}>
              {step.sources.map((source, sourceIndex) => {
                const href = safeSourceUrl(source.original_url);
                return <li className={styles.sourceChip} key={`${step.index}-${source.id}`} style={sourceIndex < 6 ? { animationDelay: `${Math.min(sourceIndex * .05, .3)}s` } : undefined}>
                  {href ? <a href={href} target="_blank" rel="noopener noreferrer">{source.title || source.id}</a> : <span>{source.title || source.id}</span>}
                </li>;
              })}
            </ul>}
            {step.gap && <p className={styles.gapText}>{step.gap}</p>}
            {index !== subtasks.length - 1 && <hr />}
          </li>)}
        </ol> : timeline.length === 0 && activityPreviews.length === 0 ? <p className={styles.emptyState}>当前任务暂未返回研究活动。</p> : null}
      </section>
    </div>
  </aside>;
}
