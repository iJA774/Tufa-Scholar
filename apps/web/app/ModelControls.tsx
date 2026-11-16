"use client";

import { useEffect, useState } from "react";
import type { ModelChoice } from "./workspace/preferences";

export type { ModelChoice } from "./workspace/preferences";

export default function ModelControls({ value, onChange, disabled }: { value: ModelChoice; onChange: (value: ModelChoice) => void; disabled: boolean }) {
  const [loading, setLoading] = useState(true);
  const [locked, setLocked] = useState(false);
  const [defaultModel, setDefaultModel] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch("/api/settings", { signal: controller.signal });
        if (!response.ok) throw new Error("无法读取模型配置");
        const settings = await response.json();
        if (controller.signal.aborted) return;
        setLocked(settings.model.model_source === "environment");
        setDefaultModel(settings.model.model || "");
      } catch {
        if (!controller.signal.aborted) setError("无法读取模型配置");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, []); // Read the effective configuration once when the settings panel opens.
  return <>
    <div className="select-group model-select"><label htmlFor="chat-model">模型</label>
      <input id="chat-model" value={locked ? defaultModel : value.model_name} disabled={disabled || locked || loading} placeholder={defaultModel ? `默认：${defaultModel}` : "输入模型 ID"} onChange={e => onChange({ model_name: e.target.value })} />
    </div>
    <small className="model-hint" role="status">{locked ? "模型由环境变量指定。" : error || "留空使用服务默认模型。"}</small>
  </>;
}
