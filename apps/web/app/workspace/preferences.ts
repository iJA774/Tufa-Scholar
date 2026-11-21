import type { ResearchMode } from "./types";

export type ModelChoice = { model_name: string };
export type ResearchDepth = "light" | "standard" | "deep";
export type RelevanceFilter = "relaxed" | "standard" | "strict";
export type ReportFormat = "academic" | "exploratory" | "brief" | "explainer";
export type WorkspacePreferences = {
  mode: ResearchMode;
  researchDepth: ResearchDepth;
  preferPrimaryEvidence: boolean;
  relevanceFilter: RelevanceFilter;
  reportFormat: ReportFormat;
  enterToSend: boolean;
  glow: boolean;
  grid: boolean;
  density: "default" | "compact";
  modelChoice: ModelChoice;
};

export const PREFERENCES_KEY = "tufa-scholar-workspace-prefs-v1";
export const DEFAULT_PREFERENCES: WorkspacePreferences = {
  mode: "auto",
  researchDepth: "standard",
  preferPrimaryEvidence: true,
  relevanceFilter: "standard",
  reportFormat: "academic",
  enterToSend: true,
  glow: true,
  grid: true,
  density: "default",
  modelChoice: { model_name: "" },
};

export function readWorkspacePreferences(): WorkspacePreferences {
  try {
    const stored = JSON.parse(window.localStorage.getItem(PREFERENCES_KEY) || "null") as Partial<WorkspacePreferences> | null;
    return {
      mode: stored?.mode === "quick" || stored?.mode === "deep" || stored?.mode === "auto" ? stored.mode : "auto",
      researchDepth: stored?.researchDepth === "light" || stored?.researchDepth === "deep" ? stored.researchDepth : "standard",
      preferPrimaryEvidence: typeof stored?.preferPrimaryEvidence === "boolean" ? stored.preferPrimaryEvidence : DEFAULT_PREFERENCES.preferPrimaryEvidence,
      relevanceFilter: stored?.relevanceFilter === "relaxed" || stored?.relevanceFilter === "strict" ? stored.relevanceFilter : "standard",
      reportFormat: stored?.reportFormat === "academic" || stored?.reportFormat === "exploratory" || stored?.reportFormat === "brief" || stored?.reportFormat === "explainer" ? stored.reportFormat : "academic",
      enterToSend: typeof stored?.enterToSend === "boolean" ? stored.enterToSend : DEFAULT_PREFERENCES.enterToSend,
      glow: typeof stored?.glow === "boolean" ? stored.glow : DEFAULT_PREFERENCES.glow,
      grid: typeof stored?.grid === "boolean" ? stored.grid : DEFAULT_PREFERENCES.grid,
      density: stored?.density === "compact" ? "compact" : "default",
      modelChoice: {
        model_name: typeof stored?.modelChoice?.model_name === "string" ? stored.modelChoice.model_name : "",
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
