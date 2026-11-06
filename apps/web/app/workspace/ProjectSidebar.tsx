"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { api, json, messageOf } from "./api";
import type { Project } from "./types";
import { useCollections } from "./Collections";

export default function ProjectSidebar({ projects, projectId, onSelect, onProjects, view, onView, onError, onNotice }: { projects: Project[]; projectId?: number; onSelect: (id?: number) => void; onProjects: (projects: Project[]) => void; view: "chat" | "collections"; onView: (view: "chat" | "collections") => void; onError: (message: string) => void; onNotice: (message: string) => void }) {
  const { collections, collectionId, setCollectionId, folderLoading } = useCollections();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState("");
  const [menu, setMenu] = useState<number>();
  const [target, setTarget] = useState<Project>();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (target) dialog.current?.showModal(); }, [target]);
  useEffect(() => {
    if (menu === undefined) return;
    const outside = (e: PointerEvent) => {
      if (!(e.target as Element).closest(".project-menu-wrap")) setMenu(undefined);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(undefined);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [menu]);
  const create = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setLocalError("");
    void (async () => {
      try {
        const created = await api<Project>("/projects", json({ name: name.trim() }));
        onProjects(await api<Project[]>("/projects"));
        onSelect(created.id);
        setName("");

      } catch (error) {
        setLocalError(messageOf(error));
        onError(messageOf(error));

      } finally {
        setBusy(false);

      }
    })();
  };
  const remove = () => {
    if (!target) return;
    setBusy(true);
    setLocalError("");
    void (async () => {
      try {
        await api(`/projects/${target.id}`, {
          method: "DELETE"
        });
        const remaining = projects.filter(p => p.id !== target.id);
        onProjects(remaining);
        if (target.id === projectId) onSelect(remaining[0]?.id);
        dialog.current?.close();
        onNotice("研究空间已删除。");

      } catch (error) {
        setLocalError(messageOf(error));
        onError(messageOf(error));

      } finally {
        setBusy(false);

      }
    })();
  };
  return <>
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">✳</span>
        <div>
          <strong>TUFA</strong>
          <small>SCHOLAR</small>
        </div>
      </div>
      <div className="side-heading">
        <span>研究空间</span>
        <span>{String(projects.length).padStart(2, "0")}</span>
      </div>
      <nav aria-label="项目" className="project-list">{projects.map(p => <div className={p.id === projectId ? "project-row active" : "project-row"} key={p.id}>
        <button className="project" disabled={busy} onClick={() => onSelect(p.id)}>
          <span className="project-icon">▤</span>
          <span>{p.name}</span>
        </button>
        <div className="project-menu-wrap">
          <button className="project-menu-trigger" type="button" disabled={busy} aria-label={`${p.name}的操作`} aria-expanded={menu === p.id} aria-haspopup="menu" onClick={() => setMenu(menu === p.id ? undefined : p.id)}>⋯</button>{menu === p.id && <div className="project-menu" role="menu">
            <button role="menuitem" onClick={() => { setMenu(undefined); setLocalError(""); setTarget(p); }}>删除空间</button>
          </div>}</div>
      </div>)}{!projects.length && <p className="side-empty">创建一个项目，开始整理研究。</p>}</nav>
      <form onSubmit={create} className="side-form">
        <label htmlFor="project-name">新建项目</label>
        <div className="inline-input">
          <input id="project-name" value={name} onChange={e => setName(e.target.value)} placeholder="输入项目名称" />
          <button aria-label="创建项目" disabled={!name.trim() || busy}>＋</button>
        </div>
      </form>
      <div className="side-management">
        <button disabled={!projectId || busy} className={view === "collections" ? "side-view active" : "side-view"} onClick={() => onView(view === "collections" ? "chat" : "collections")}>▤　收藏夹</button>{view === "collections" && collections.map(c => <button className={c.id === collectionId ? "side-folder active" : "side-folder"} disabled={busy || folderLoading} key={c.id} onClick={() => setCollectionId(c.id)}>{c.name}</button>)}</div>
      <div className="side-bottom">
        <Link className="settings-link" href="/settings">⚙ <span>设置</span>
        </Link>
      </div>
    </aside>
    <dialog ref={dialog} className="confirm-dialog" aria-labelledby="delete-title" aria-describedby="delete-description" onClose={() => setTarget(undefined)} onCancel={e => { if (busy) e.preventDefault(); }}>
      <h2 id="delete-title">删除“{target?.name}”？</h2>
      <p id="delete-description">删除空间、会话及检索记录，原文保留，其他空间不受影响。</p>
      {localError && <p className="run-error" role="alert">{localError}</p>}
      <div className="dialog-actions">
        <button className="button secondary" disabled={busy} onClick={() => dialog.current?.close()}>取消</button>
        <button className="button danger" disabled={busy} onClick={remove}>{busy ? "删除中…" : "确认删除"}</button>
      </div>
    </dialog></>;
}
