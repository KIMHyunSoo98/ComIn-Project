// FastAPI api/schemas/research_schema.py 를 그대로 미러링한 타입.

export type ResearchRecord = {
  id: number;
  corp_name: string;
  corp_code: string;
  question: string;
  report: string;
  news_mode: string; // "keyword"(A) | "trend"(B)
  created_at: string;
};

export type ResearchResponse = {
  status: "ok" | "candidates";
  result: ResearchRecord | null; // status="ok"일 때
  candidates: string[] | null; // status="candidates"일 때
};

// 프론트 채팅 화면에서 렌더링하는 메시지 모델.
export type ChatMessage =
  | { id: string; role: "user"; kind: "text"; content: string }
  | {
      id: string;
      role: "assistant";
      kind: "streaming";
      status: string;
      text: string;
      corp_name?: string;
      corp_code?: string;
    }
  | { id: string; role: "assistant"; kind: "report"; record: ResearchRecord; elapsedMs: number }
  | { id: string; role: "assistant"; kind: "candidates"; candidates: string[]; question: string }
  | { id: string; role: "assistant"; kind: "error"; content: string };

// POST /research/stream 이 흘려보내는 SSE 이벤트.
export type StreamEvent =
  | { type: "status"; stage: string; message: string }
  | { type: "metadata"; corp_name: string; corp_code: string }
  | { type: "candidates"; candidates: string[] }
  | { type: "chunk"; text: string }
  | ({ type: "done"; thread_id: string } & ResearchRecord)
  | { type: "error"; message: string };
