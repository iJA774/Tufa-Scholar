export type Collection = { id: number; name: string };
export type Project = { id: number; name: string };
export type Conversation = { id: number; title: string };
export type Paper = { id?: number; title: string; authors?: string[]; year?: number | null; doi?: string | null; arxiv_id?: string | null; abstract?: string | null; source?: string; source_level?: string; original_url?: string | null; pdf_url?: string | null; versions?: { id: number; filename?: string; file_url?: string; text_status?: string }[] };
export type Message = { id: number; role: string; content: string; run_id?: number | null; metadata?: { evidence?: Citation[]; report_id?: number } };
export type Citation = { id?: string; title?: string; version_id?: number; page?: number; original_url?: string; source_level?: string; provider?: string; retrieved_at?: string };
export type Subtask = { index: number; source: string; task: string; status: "findings" | "sources_only" | "no_evidence"; findings: { text: string; citations: string[] }[]; sources: { id: string; title: string; original_url?: string | null; source_level: string }[]; gap?: string };
export type Run = { id: number; conversation_id?: number; query?: string; status: string; subtasks?: Subtask[]; stage?: string | null; error?: string | null; result?: Paper[] | { papers?: Paper[]; results?: Paper[]; warning?: string } | null };
