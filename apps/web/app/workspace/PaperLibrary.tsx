"use client";

import { ReactNode } from "react";
import PaperRow from "./PaperRow";
import type { Paper } from "./types";

export default function PaperLibrary({ papers, renderPaperAction }: { papers: Paper[]; renderPaperAction: (paper: Paper) => ReactNode }) {
  return (
    <section className="card library-card">
      <div className="section-kicker">02 / LIBRARY</div>
      <div className="section-heading">
        <div>
          <h2>项目文献 <span className="count">{papers.length}</span></h2>
          <p>项目中的文献在所有会话间共享。</p>
        </div>
      </div>
      {papers.length ? (
        <div className="paper-list">
          {papers.map((paper, i) => (
            <PaperRow key={paper.id ?? i} paper={paper} action={renderPaperAction(paper)} />
          ))}
        </div>
      ) : (
        <div className="empty-state">
          <span className="empty-icon">▤</span>
          <strong>还没有文献</strong>
          <span>检索并保存结果，或导入一份 PDF。</span>
        </div>
      )}
    </section>
  );
}
