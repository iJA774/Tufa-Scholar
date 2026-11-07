"use client";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode, type FormEvent } from "react";
import { api, json, messageOf } from "./api";
import { samePaper } from "../paperIdentity";
import type { Collection, Paper } from "./types";
import PaperRow from "./PaperRow";

type CollectionContext = {
  collections: Collection[]; collectionId: number | undefined; setCollectionId: (id: number | undefined) => void;
  collectionPapers: Paper[]; folderLoading: boolean; paperAction: (paper: Paper) => ReactNode;
  createForm: (id: string) => ReactNode; setDeleting: (item: Collection | undefined) => void; busy: boolean; removePaper: (paper: Paper) => void;
};
const Context = createContext<CollectionContext | null>(null);
export function useCollections() { const value = useContext(Context); if (!value) throw new Error("Missing collection provider"); return value; }

export function CollectionsProvider({ projectId, children, onChanged, onError, onNotice }: { projectId: number; children: ReactNode; onChanged: () => void; onError: (error: string) => void; onNotice: (message: string) => void }) {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collectionId, setCollectionId] = useState<number>();
  const [collectionPapers, setCollectionPapers] = useState<Paper[]>([]);
  const [allMembers, setAllMembers] = useState<Record<number, Paper[]>>({});
  const [folderLoading, setFolderLoading] = useState(false);
  const [membersLoading, setMembersLoading] = useState(false);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<Paper>();
  const [mode, setMode] = useState<"add" | "remove">("add");
  const [pickerId, setPickerId] = useState<number>();
  const [deleting, setDeleting] = useState<Collection>();
  const picker = useRef<HTMLDialogElement>(null), deletion = useRef<HTMLDialogElement>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!projectId) return;
    let current = true;
    api<Collection[]>(`/projects/${projectId}/collections`).then(data => {
      if (current) {
        setCollections(data);
        setCollectionId(data[0]?.id);

      }
    }).catch(e => {
      if (current) onError(messageOf(e));
    });
    return () => {
      current = false;
    };
  }, [projectId, onError]);
  useEffect(() => {
    let current = true;
    setCollectionPapers([]);
    if (!collectionId) {
      setFolderLoading(false);
      return;

    } setFolderLoading(true);
    api<Paper[]>(`/projects/${projectId}/collections/${collectionId}/papers`).then(data => {
      if (current) setCollectionPapers(data);
    }).catch(e => {
      if (current) onError(messageOf(e));
    }).finally(() => {
      if (current) setFolderLoading(false);
    });
    return () => {
      current = false;
    };
  }, [projectId, collectionId, version, onError]);
  useEffect(() => {
    if (!projectId) return;
    let current = true;
    setMembersLoading(true);
    Promise.all(collections.map(async c => [c.id, await api<Paper[]>(`/projects/${projectId}/collections/${c.id}/papers`)] as const)).then(data => {
      if (current) setAllMembers(Object.fromEntries(data));
    }).catch(e => {
      if (current) onError(messageOf(e));
    }).finally(() => {
      if (current) setMembersLoading(false);
    });
    return () => {
      current = false;
    };
  }, [projectId, collections, version, onError]);
  useEffect(() => { if (selected) picker.current?.showModal(); }, [selected]);
  useEffect(() => { if (deleting) { setDialogError(""); deletion.current?.showModal(); } }, [deleting]);
  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setDialogError("");
    onNotice("");
    try {
      await fn();
    } catch (e) {
      if (alive.current) {
        setDialogError(messageOf(e));
        onError(messageOf(e));
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  const create = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    void act(async () => {
      const created = await api<Collection>(`/projects/${projectId}/collections`, json({ name: name.trim() }));
      const list = await api<Collection[]>(`/projects/${projectId}/collections`);
      if (!alive.current) return;
      setCollections(list);
      if (selected) setPickerId(created.id);
      else setCollectionId(created.id);
      setName("");
      onNotice("收藏夹已创建。");
    });
  };
  const confirm = () => {
    if (!selected || !pickerId) return;
    void act(async () => {
      if (mode === "remove") {
        const member = allMembers[pickerId]?.find(p => samePaper(p, selected));
        if (!member?.id) throw new Error("文献已不在该收藏夹中，请重试。");
        await api(`/projects/${projectId}/collections/${pickerId}/papers/${member.id}`, {
          method: "DELETE"
        });

      } else await api(`/projects/${projectId}/collections/${pickerId}/papers`, json(selected.id ? {
        paper_id: selected.id
      } : selected));
      if (!alive.current) return;
      setCollectionId(pickerId);
      setVersion(v => v + 1);
      onChanged();
      picker.current?.close();
      onNotice(mode === "remove" ? "已从收藏夹移除。" : "已加入收藏夹。");
    });
  };
  const paperAction = (paper: Paper) => {
    const folders = collections.filter(c => allMembers[c.id]?.some(p => samePaper(p, paper)));
    const member = folders.length > 0;
    return <button disabled={busy || membersLoading} className="text-button" title={member ? `从收藏夹移除${paper.title}` : `将${paper.title}加入收藏夹`} aria-label={member ? `从收藏夹移除${paper.title}` : `将${paper.title}加入收藏夹`} onClick={() => {
      if (folders.length === 1) {
        const found = allMembers[folders[0].id].find(p => samePaper(p, paper));
        if (!found?.id) return;
        void act(async () => {
          await api(`/projects/${projectId}/collections/${folders[0].id}/papers/${found.id}`, {
            method: "DELETE"
          });
          if (alive.current) {
            setVersion(v => v + 1);
            onNotice("已从收藏夹移除。");
          }
        });

      } else {
        setMode(member ? "remove" : "add");
        setPickerId(member ? folders[0]?.id : collectionId);
        setDialogError("");
        setSelected(paper);

      }
    }}>{member ? "−" : "＋"}</button>;
  };
  const createForm = (id: string) => <form onSubmit={create} className="new-conversation">
    <label htmlFor={id}>新建收藏夹</label>
    <div className="inline-input">
      <input id={id} value={name} onChange={e => setName(e.target.value)} placeholder="收藏夹名称" />
      <button aria-label="创建收藏夹" disabled={busy || !name.trim()}>＋</button>
    </div>
  </form>;
  const removePaper = (paper: Paper) => {
    if (!paper.id || !collectionId) return;
    void act(async () => {
      await api(`/projects/${projectId}/collections/${collectionId}/papers/${paper.id}`, {
        method: "DELETE"
      });
      if (alive.current) {
        setVersion(v => v + 1);
        onNotice("已从收藏夹移除。");
      }
    });
  };
  return <Context.Provider value={{ collections, collectionId, setCollectionId, collectionPapers, folderLoading, paperAction, createForm, setDeleting, busy, removePaper }}>{children}
    <dialog ref={picker} className="confirm-dialog" aria-labelledby="collection-title" onClose={() => setSelected(undefined)} onCancel={e => { if (busy) e.preventDefault(); }}>
      <div className="section-heading">
        <h2 id="collection-title">{mode === "remove" ? "从收藏夹移除" : "加入收藏夹"}</h2>
        <button disabled={busy} className="history-close" aria-label="关闭" onClick={() => picker.current?.close()}>×</button>
      </div>
      {dialogError && <p role="alert">{dialogError}</p>}
      <fieldset className="folder-picker">
        <legend>选择收藏夹</legend>{collections.filter(c => mode === "add" || (selected && allMembers[c.id]?.some(p => samePaper(p, selected)))).map(c => <label key={c.id} className={pickerId === c.id ? "folder-option selected" : "folder-option"}>
          <input type="radio" name="folder-picker" checked={pickerId === c.id} disabled={busy} onChange={() => setPickerId(c.id)} />
          <span>{c.name}</span>{pickerId === c.id && <span aria-hidden="true">✓</span>}</label>)}</fieldset>{mode === "add" && createForm("new-folder-dialog")}
      <div className="dialog-actions">
        <button className="button secondary" disabled={busy} onClick={() => picker.current?.close()}>取消</button>
        <button className="button primary" disabled={busy || !pickerId} onClick={confirm}>确认</button>
      </div>
    </dialog>
    <dialog ref={deletion} className="confirm-dialog" aria-labelledby="delete-folder-title" onClose={() => setDeleting(undefined)} onCancel={e => { if (busy) e.preventDefault(); }}>
      <h2 id="delete-folder-title">删除“{deleting?.name}”？</h2>
      <p>删除收藏夹及其关联，项目文献与原文保留。</p>
      {dialogError && <p role="alert">{dialogError}</p>}
      <div className="dialog-actions">
        <button className="button secondary" disabled={busy} onClick={() => deletion.current?.close()}>取消</button>
        <button className="button danger" disabled={busy} onClick={() => { if (!deleting) return; void act(async () => { await api(`/projects/${projectId}/collections/${deleting.id}`, { method: "DELETE" }); if (!alive.current) return; const remaining = collections.filter(c => c.id !== deleting.id); setCollections(remaining); if (collectionId === deleting.id) setCollectionId(remaining[0]?.id); deletion.current?.close(); onNotice("收藏夹已删除。"); }); }}>确认删除</button>
      </div>
    </dialog>
  </Context.Provider>;
}

export function CollectionsPanel() {
  const { collections, collectionId, setCollectionId, collectionPapers, folderLoading, createForm, setDeleting, busy, removePaper } = useCollections();
  const current = collections.find(c => c.id === collectionId);
  return <section className="card collection-management">
    <div className="section-heading">
      <div>
        <div className="section-kicker">COLLECTIONS</div>
        <h2>收藏夹</h2>
      </div>{current && <button disabled={busy} className="button secondary" onClick={() => setDeleting(current)}>删除收藏夹</button>}</div>
    <label htmlFor="folder">选择收藏夹</label>
    <select disabled={busy || folderLoading} id="folder" value={collectionId ?? ""} onChange={e => setCollectionId(e.target.value ? Number(e.target.value) : undefined)}>
      <option value="">选择收藏夹</option>{collections.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select>{createForm("new-folder")}{folderLoading ? <p>加载中…</p> : collectionId && (collectionPapers.length ? <div className="paper-list">{collectionPapers.map((paper, i) => <PaperRow key={paper.id ?? i} paper={paper} action={<button disabled={busy || folderLoading} className="text-button" title={`从${current?.name}移除${paper.title}`} aria-label={`从${current?.name}移除${paper.title}`} onClick={() => removePaper(paper)}>−</button>} />)}</div> : <div className="empty-state">收藏夹暂无文献。</div>)}</section>;
}
