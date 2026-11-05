type Paper = { id?: number; doi?: string | null; arxiv_id?: string | null };
const normalizeDoi = (v?: string | null) => v?.trim().toLowerCase().replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/, "");
const normalizeArxiv = (v?: string | null) => v?.trim().toLowerCase().replace(/^(?:https?:\/\/(?:www\.)?arxiv\.org\/(?:abs|pdf)\/|arxiv:\s*)/, "").replace(/\.pdf$/, "").replace(/v\d+$/, "");
export const samePaper = (a: Paper, b: Paper) => !!((a.id && a.id === b.id) || (normalizeDoi(a.doi) && normalizeDoi(a.doi) === normalizeDoi(b.doi)) || (normalizeArxiv(a.arxiv_id) && normalizeArxiv(a.arxiv_id) === normalizeArxiv(b.arxiv_id)));
