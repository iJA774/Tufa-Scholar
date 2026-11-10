"use client";

import { useEffect, useRef, useState } from "react";

type ClearLogsResponse = { cleared_files: number };
type ErrorResponse = { detail?: unknown };

export default function RuntimeLogsSettings() {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const request = useRef<AbortController | null>(null);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      request.current?.abort();
    };
  }, []);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (confirming && !element.open) element.showModal();
    if (!confirming && element.open) element.close();
  }, [confirming]);

  const clearLogs = async () => {
    if (busy || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/settings/logs", { method: "DELETE", signal: controller.signal });
      const data = await response.json().catch(() => null) as ClearLogsResponse & ErrorResponse | null;
      if (!response.ok) {
        throw new Error(typeof data?.detail === "string" ? data.detail : "无法清除运行日志，请稍后重试。");
      }
      const clearedFiles = data?.cleared_files;
      if (typeof clearedFiles !== "number" || !Number.isInteger(clearedFiles) || clearedFiles < 0) {
        throw new Error("无法确认日志清理结果，请稍后重试。");
      }
      if (!mounted.current || controller.signal.aborted) return;
      setNotice(clearedFiles === 0
        ? "没有旧日志需要清除。"
        : `已清除 ${clearedFiles} 个 API 与 worker 日志文件（含轮转备份）。`);
      setConfirming(false);
    } catch (reason) {
      if (!mounted.current || controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "无法清除运行日志，请稍后重试。");
    } finally {
      if (request.current === controller) request.current = null;
      if (mounted.current && !controller.signal.aborted) setBusy(false);
    }
  };

  return <>
    <section className="card settings-card" aria-labelledby="runtime-logs-title" aria-busy={busy}>
      <div className="section-kicker">MAINTENANCE</div>
      <h2 id="runtime-logs-title">运行日志</h2>
      <p>清除本机 API 与 worker 的当前日志和轮转备份。项目、报告与私有诊断材料会保留。</p>
      {notice && <p className="settings-alert" role="status">{notice}</p>}
      {!confirming && error && <p className="settings-alert error" role="alert">{error}</p>}
      <div>
        <button className="button danger" type="button" disabled={busy || confirming} onClick={() => { setError(""); setNotice(""); setConfirming(true); }}>
          清除旧日志
        </button>
      </div>
    </section>

    <dialog ref={dialog} className="confirm-dialog" aria-labelledby="clear-logs-title" aria-describedby="clear-logs-description" onClose={() => setConfirming(false)} onCancel={event => { if (busy) event.preventDefault(); }}>
      <h2 id="clear-logs-title">清除旧运行日志？</h2>
      <p id="clear-logs-description">将删除 API 与 worker 的当前日志及轮转备份。项目、报告和私有诊断材料不会删除。</p>
      {error && <p className="settings-alert error" role="alert">{error}</p>}
      <div className="dialog-actions">
        <button className="button secondary" type="button" disabled={busy} onClick={() => dialog.current?.close()}>取消</button>
        <button className="button danger" type="button" disabled={busy} onClick={() => { void clearLogs(); }}>{busy ? "清除中…" : "确认清除"}</button>
      </div>
    </dialog>
  </>;
}
