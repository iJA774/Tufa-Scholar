"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, messageOf } from "./api";
import type { Paper, Project } from "./types";
import { CollectionsProvider, CollectionsPanel, useCollections } from "./Collections";
import ProjectSidebar from "./ProjectSidebar";
import ResearchChat from "./ResearchChat";
import SearchDialog from "./SearchDialog";
import PdfImport from "./PdfImport";
import PaperLibrary from "./PaperLibrary";

export default function ProjectWorkspace({ projects, project, onProjects, onSelect, onError, onNotice, error: errorText, notice: noticeText, clearError, clearNotice }: { projects: Project[]; project?: Project; onProjects: (projects: Project[]) => void; onSelect: (id?: number) => void; onError: (message: string) => void; onNotice: (message: string) => void; error: string; notice: string; clearError: () => void; clearNotice: () => void }) {
  const [view, setView] = useState<"chat" | "collections">("chat");
  const [papers, setPapers] = useState<Paper[]>([]);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const error = useCallback((message: string) => { if (alive.current) onError(message); }, [onError]);
  const notice = useCallback((message: string) => { if (alive.current) onNotice(message); }, [onNotice]);
  const refresh = useCallback(async () => { if (!project) return; try { const data = await api<Paper[]>(`/projects/${project.id}/papers`); if (alive.current) setPapers(data); } catch (e) { if (alive.current) error(messageOf(e)); } }, [project, error]);
  useEffect(() => { void refresh(); }, [refresh]);
  return <CollectionsProvider projectId={project?.id ?? 0} onChanged={() => void refresh()} onError={error} onNotice={notice}>
    <div className="shell">
      <ProjectSidebar projects={projects} projectId={project?.id} onProjects={onProjects} onSelect={onSelect} view={view} onView={setView} onError={error} onNotice={notice} />
      <WorkspaceContent project={project} papers={papers} refresh={refresh} view={view} setView={setView} onError={error} onNotice={notice} errorText={errorText} noticeText={noticeText} clearError={clearError} clearNotice={clearNotice} />
    </div>
  </CollectionsProvider>;
}

function WorkspaceContent({ project, papers, refresh, view, setView, onError, onNotice, errorText, noticeText, clearError, clearNotice }: { project?: Project; papers: Paper[]; refresh: () => Promise<void>; view: "chat" | "collections"; setView: (view: "chat" | "collections") => void; onError: (message: string) => void; onNotice: (message: string) => void; errorText: string; noticeText: string; clearError: () => void; clearNotice: () => void }) {
  const { collections, collectionId, setCollectionId, collectionPapers, folderLoading, paperAction } = useCollections();
  const [searchOpen, setSearchOpen] = useState(false);
  const [conversation, setConversation] = useState<number>();
  return <main className="main">
    <header className="topbar">
      <span>研究工作台 <span className="slash">/</span> {project?.name || "开始使用"}</span>
      <button className="history-trigger" aria-label="搜索文献与历史" disabled={!project} onClick={() => { setConversation(undefined); setSearchOpen(true); }}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m16 16 5 5" />
        </svg>
      </button>
    </header>
    {errorText && <div className="alert error" role="alert">{errorText}
      <button onClick={clearError} aria-label="关闭提示">×</button>
    </div>}
    {noticeText && <div className="alert" role="status">{noticeText}
      <button onClick={clearNotice} aria-label="关闭提示">×</button>
    </div>}
    {!project ? <div className="welcome">
      <div className="eyebrow">YOUR RESEARCH, ROOTED HERE</div>
      <h1>让每一次发现，<br />都有迹可循<span className="accent">。</span>
      </h1>
      <p>为你的研究创建一个项目，开始检索和收藏文献。</p>
      <div className="welcome-line" />
    </div> : <div className="workspace">
      <div className="page-heading">
        <div>
          <div className="eyebrow">RESEARCH WORKSPACE</div>
          <h1>{project.name}</h1>
          <p>检索、导入并整理你的研究文献。</p>
        </div>
        <div className="paper-count">
          <strong>{String(papers.length).padStart(2, "0")}</strong>
          <span>项目文献</span>
        </div>
      </div>
      {view === "collections" && <CollectionsPanel />}
      <div hidden={view === "collections"}>
        <ResearchChat projectId={project.id} collections={collections} collectionId={collectionId} onCollectionChange={setCollectionId} collectionPapers={collectionPapers} folderLoading={folderLoading} onError={onError} openConversationId={conversation} library={<PaperLibrary papers={papers} renderPaperAction={paperAction} />} importPanel={<PdfImport projectId={project.id} onImported={refresh} notify={onNotice} />} />
      </div>
    </div>}
    {project && searchOpen && <SearchDialog projectId={project.id} onClose={() => setSearchOpen(false)} onOpenConversation={id => { setView("chat"); setConversation(id); setSearchOpen(false); }} renderPaperAction={paperAction} />}
  </main>;
}
