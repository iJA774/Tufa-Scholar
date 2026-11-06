import type { ReactNode } from "react";
import type { Paper } from "./types";

export default function PaperRow({ paper, action }: { paper: Paper; action?: ReactNode }) {
  const versions = paper.versions ?? [];
  const sourceLevel: Record<string, string> = { full_text: "已提取正文", abstract: "仅摘要", metadata_only: "仅文献信息", metadata: "仅文献信息" };
  return <article className="paper-row">
    <div className="paper-index">◇</div>
    <div className="paper-body">
      <h3>{paper.title}</h3>
      <p className="metadata">{[paper.authors?.join(", "), paper.year, paper.source].filter(Boolean).join(" · ") || "文献信息待补充"}</p>{paper.source_level && sourceLevel[paper.source_level] && <span className="source-level">{sourceLevel[paper.source_level]}</span>}{paper.abstract && <p className="abstract">{paper.abstract}</p>}
      <div className="paper-links">{versions.map((version, index) => <span key={version.id}>
        <a href={`/api/paper-versions/${version.id}/file`} target="_blank" rel="noreferrer">{versions.length > 1 ? `PDF ${index + 1}${version.filename ? ` · ${version.filename}` : ""}` : "打开 PDF"} ↗</a>{version.text_status === "no_extractable_text" && <span className="text-unavailable"> · 未提取到文字</span>}</span>)}{paper.original_url && <a href={paper.original_url} target="_blank" rel="noreferrer">原文 ↗</a>}{!versions.length && paper.pdf_url && <a href={paper.pdf_url} target="_blank" rel="noreferrer">PDF ↗</a>}{paper.doi && <span>DOI: {paper.doi}</span>}</div>
    </div>{action}</article>;
}
