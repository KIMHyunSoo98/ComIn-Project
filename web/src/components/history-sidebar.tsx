"use client";

import { useEffect, useState } from "react";

import { apiJson } from "@/lib/api";
import type { ResearchRecord } from "@/lib/types";
import { Markdown } from "@/components/markdown";

// 조사 이력 목록. 항목 클릭 시 GET /research/{id}로 상세를 불러와 모달로 보여준다.
export function HistorySidebar({ refreshKey }: { refreshKey: number }) {
  const [items, setItems] = useState<ResearchRecord[]>([]);
  const [active, setActive] = useState<ResearchRecord | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    apiJson<ResearchRecord[]>("/research")
      .then((data) => {
        if (alive) {
          setItems(data);
          setError("");
        }
      })
      .catch(() => {
        if (alive) setError("이력을 불러오지 못했습니다.");
      });
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  async function open(id: number) {
    try {
      const record = await apiJson<ResearchRecord>(`/research/${id}`);
      setActive(record);
    } catch {
      // 상세 조회 실패는 조용히 무시한다.
    }
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <h2>조사 이력</h2>
      </div>

      {error ? <p className="muted">{error}</p> : null}
      {!error && items.length === 0 ? <p className="muted">아직 조사한 기록이 없어요.</p> : null}

      <ul className="history-list">
        {items.map((item) => (
          <li key={item.id}>
            <button type="button" className="history-item" onClick={() => open(item.id)}>
              <strong>{item.corp_name}</strong>
              <span>{item.question}</span>
            </button>
          </li>
        ))}
      </ul>

      {active ? (
        <div className="modal" role="dialog" aria-modal="true" onClick={() => setActive(null)}>
          <div className="modal-body" onClick={(event) => event.stopPropagation()}>
            <button type="button" className="modal-close" aria-label="닫기" onClick={() => setActive(null)}>
              ✕
            </button>
            <div className="report-meta">
              <span className="tag">{active.corp_name}</span>
              <span className="tag light">코드 {active.corp_code}</span>
              <span className="tag light">뉴스 {active.news_mode}</span>
            </div>
            <p className="modal-question">Q. {active.question}</p>
            <Markdown>{active.report}</Markdown>
          </div>
        </div>
      ) : null}
    </aside>
  );
}
