"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import SettingsPanel from "./SettingsPanel";
import {
  DEFAULT_PREFERENCES,
  readWorkspacePreferences,
  writeWorkspacePreferences,
  type WorkspacePreferences,
} from "../workspace/preferences";
import { api, json } from "../workspace/api";
import type { Project } from "../workspace/types";

export default function SettingsPage() {
  const router = useRouter();
  const [preferences, setPreferences] = useState<WorkspacePreferences>(DEFAULT_PREFERENCES);
  const [preferencesReady, setPreferencesReady] = useState(false);

  useEffect(() => {
    setPreferences(readWorkspacePreferences());
    setPreferencesReady(true);
  }, []);

  const updatePreferences = useCallback((next: WorkspacePreferences) => {
    setPreferences(next);
    writeWorkspacePreferences(next);
  }, []);

  const ensureProject = useCallback(async () => {
    const projects = await api<Project[]>("/projects");
    if (projects[0]) return projects[0].id;
    const created = await api<Project>("/projects", json({ name: "默认研究空间" }));
    return created.id;
  }, []);

  if (!preferencesReady) return <main aria-busy="true" />;

  return (
    <SettingsPanel
      onClose={() => router.push("/workspace")}
      preferences={preferences}
      onPreferencesChange={updatePreferences}
      ensureProject={ensureProject}
      onNotice={() => {}}
    />
  );
}
