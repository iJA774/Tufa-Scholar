// Adapted from ByteDance DeerFlow's ResearchBlock, ResearchActivitiesBlock,
// LoadingAnimation, RainbowText, Card, and Tabs components. See THIRD_PARTY_LICENSES/DEERFLOW-MIT-LICENSE.txt.
// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates

"use client";

import { type CSSProperties, type KeyboardEvent, type RefObject, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message, Run } from "./types";
import styles from "./ResearchPanel.module.css";

type Props = {
  run: Run;
  report?: Message;
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

export default function ResearchPanel({ run, report, open, status, stage, active, onClose, closeButtonRef }: Props) {
  const hasReport = !!report?.metadata?.report_id;
  const [tab, setTab] = useState<"report" | "activities">(hasReport ? "report" : "activities");
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
    setTab(hasReport ? "report" : "activities");
  }, [run.id, report?.id, hasReport]);

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
  }, [open, tab, run.status, run.stage, run.subtasks]);

  const changeTab = (next: "report" | "activities") => {
    if (next === "report" && !hasReport) return;
    setTab(next);
  };

  const handleTabKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next = event.key === "ArrowRight" ? (tab === "report" ? "activities" : "report") : (tab === "activities" && hasReport ? "report" : "activities");
    changeTab(next);
    requestAnimationFrame(() => tabRefs.current[next === "report" ? 0 : 1]?.focus());
  };

  const subtasks = run.subtasks || [];
  const expanded = open && hasEntered;
  const className = `${styles.researchPanel} ${expanded ? styles.researchPanelOpen : styles.researchPanelClosed}`;
  return <aside className={className} aria-label="研究报告与活动" aria-hidden={!open} inert={!open}>
    <div className={styles.researchCard}>
      <div className={styles.cardToolbar}>
        <div className={styles.tabs} role="tablist" aria-label="研究内容">
          <button ref={node => { tabRefs.current[0] = node; }} id={reportTabId} className={`${styles.tab} ${tab === "report" ? styles.tabActive : ""}`} type="button" role="tab" aria-selected={tab === "report"} aria-controls={reportPanelId} tabIndex={tab === "report" ? 0 : -1} disabled={!hasReport} onClick={() => changeTab("report")} onKeyDown={handleTabKey}>报告</button>
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
        </> : <p className={styles.emptyState}>报告生成后会显示在这里。</p>}
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
        </ol> : <p className={styles.emptyState}>当前任务暂未返回研究活动。</p>}
      </section>
    </div>
  </aside>;
}
