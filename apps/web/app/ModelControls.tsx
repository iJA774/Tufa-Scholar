"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

export type ModelChoice = { model_name: string; reasoning_effort: string };
type Model = { id: string; reasoning_efforts: string[]; default_effort: string | null };
const labels: Record<string, string> = { none: "关闭", minimal: "极低", low: "低", medium: "中", high: "高", xhigh: "极高", max: "最高" };

export default function ModelControls({ value, onChange, disabled }: { value: ModelChoice; onChange: (value: ModelChoice) => void; disabled: boolean }) {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [locked, setLocked] = useState(false);
  const [manual, setManual] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [initialModel, setInitialModel] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setModels([]);
    async function load() {
      try {
        const settingsResponse = await fetch("/api/settings", { signal: controller.signal });
        if (!settingsResponse.ok) throw new Error("无法读取模型配置");
        const settings = await settingsResponse.json();
        if (controller.signal.aborted) return;
        setLocked(settings.model.model_source === "environment");
        setInitialModel(settings.model.model || "");
        const response = await fetch("/api/models", { signal: controller.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "获取模型失败");
        if (!controller.signal.aborted) setModels(data.models);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "获取模型失败");
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    if (initialModel && (locked || !value.model_name)) onChange({ model_name: initialModel, reasoning_effort: "" });
  }, [initialModel, locked]); // Initialize from the effective saved configuration only.
  const selected = models.find(m => m.id === value.model_name);
  const declared = !!selected?.reasoning_efforts.length;
  const levels = declared ? selected!.reasoning_efforts : ["low", "medium", "high"];
  useEffect(() => {
    if (value.reasoning_effort && !levels.includes(value.reasoning_effort)) onChange({ ...value, reasoning_effort: "" });
  }, [models, value.model_name]);
  return <>
    <div className="select-group model-select"><label htmlFor="chat-model">模型</label>
      {manual && !locked ? <input id="chat-model" value={value.model_name} disabled={disabled} placeholder="输入模型 ID" onChange={e => onChange({ model_name: e.target.value, reasoning_effort: "" })} /> :
        <select id="chat-model" value={value.model_name} disabled={disabled || locked || loading} onChange={e => onChange({ model_name: e.target.value, reasoning_effort: "" })}>
          <option value="">选择模型</option>
          {value.model_name && !models.some(m => m.id === value.model_name) && <option value={value.model_name}>{value.model_name}（当前值）</option>}
          {models.map(m => <option value={m.id} key={m.id}>{m.id}</option>)}
        </select>}
    </div>
    <div className="select-group"><label htmlFor="reasoning-effort">思考强度</label><select id="reasoning-effort" value={value.reasoning_effort} disabled={disabled || loading || !levels.length} onChange={e => onChange({ ...value, reasoning_effort: e.target.value })}>
      <option value="">供应商默认{selected?.default_effort ? `（${labels[selected.default_effort] || selected.default_effort}）` : ""}</option>
      {levels.map(level => <option value={level} key={level}>{labels[level] || level}</option>)}
    </select></div>
    <div className="model-actions"><button type="button" className="model-action" disabled={disabled || loading} onClick={() => { onChange({ ...value, reasoning_effort: "" }); setRefresh(v => v + 1); }}>刷新模型</button><button type="button" className="model-action" disabled={disabled || locked} onClick={() => setManual(v => !v)}>{manual ? "从列表选择" : "手动填写"}</button></div>
    <small className="model-hint" role="status">{loading ? "正在获取模型…" : error ? <>{error} · <Link href="/settings">配置服务</Link>，或手动填写模型。</> : !models.length ? "服务未返回模型，可手动填写。" : !declared ? "供应商未声明档位，提供低、中、高。" : "思考强度选项来自供应商。"}{locked && " 模型由环境变量指定。"}</small>
  </>;
}
