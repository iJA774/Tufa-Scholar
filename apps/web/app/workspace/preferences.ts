import type { ResearchMode } from "./types";

export type ModelChoice = { model_name: string; reasoning_effort: string };
export type WorkspacePreferences = {
  mode: ResearchMode;
  enterToSend: boolean;
  glow: boolean;
  grid: boolean;
  density: "default" | "compact";
  modelChoice: ModelChoice;
};

export const PREFERENCES_KEY = "tufa-scholar-workspace-prefs-v1";
export const DEFAULT_PREFERENCES: WorkspacePreferences = {
  mode: "deep",
  enterToSend: true,
  glow: true,
  grid: true,
  density: "default",
  modelChoice: { model_name: "", reasoning_effort: "" },
};

export function readWorkspacePreferences(): WorkspacePreferences {
  try {
    const stored = JSON.parse(window.localStorage.getItem(PREFERENCES_KEY) || "null") as Partial<WorkspacePreferences> | null;
    return {
      mode: stored?.mode === "quick" ? "quick" : "deep",
      enterToSend: typeof stored?.enterToSend === "boolean" ? stored.enterToSend : DEFAULT_PREFERENCES.enterToSend,
      glow: typeof stored?.glow === "boolean" ? stored.glow : DEFAULT_PREFERENCES.glow,
      grid: typeof stored?.grid === "boolean" ? stored.grid : DEFAULT_PREFERENCES.grid,
      density: stored?.density === "compact" ? "compact" : "default",
      modelChoice: {
        model_name: typeof stored?.modelChoice?.model_name === "string" ? stored.modelChoice.model_name : "",
        reasoning_effort: typeof stored?.modelChoice?.reasoning_effort === "string" ? stored.modelChoice.reasoning_effort : "",
      },
    };
  } catch {
    return DEFAULT_PREFERENCES;
  }
}

export function writeWorkspacePreferences(preferences: WorkspacePreferences) {
  try {
    window.localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences));
  } catch {
    // Keep the current page usable when local storage is unavailable.
  }
}
