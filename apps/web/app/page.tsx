"use client";
import { useCallback, useEffect, useState } from "react";
import { api, messageOf } from "./workspace/api";
import type { Project } from "./workspace/types";
import ProjectWorkspace from "./workspace/ProjectWorkspace";

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let current = true;
    api<Project[]>("/projects").then(data => { if (current) { setProjects(data); setProjectId(data[0]?.id); } }).catch(e => { if (current) setError(messageOf(e)); });
    return () => { current = false; };
  }, []);
  const showError = useCallback((message: string) => { setError(message); setNotice(""); }, []);
  const showNotice = useCallback((message: string) => { setNotice(message); setError(""); }, []);
  return <>
    <ProjectWorkspace
      key={projectId ?? "empty"}
      projects={projects}
      project={projects.find(p => p.id === projectId)}
      onProjects={setProjects}
      onSelect={id => { setError(""); setNotice(""); setProjectId(id); }}
      onError={showError}
      onNotice={showNotice}
      error={error}
      notice={notice}
      clearError={() => setError("")}
      clearNotice={() => setNotice("")} />
  </>;
}
