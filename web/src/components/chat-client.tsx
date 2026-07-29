"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

import type { ChatMessage, ResearchRecord, StreamEvent } from "@/lib/types";
import { HistorySidebar } from "@/components/history-sidebar";
import { Markdown } from "@/components/markdown";

const SUGGESTIONS = [
  { company: "삼성전자", question: "주요 사업과 최근 이슈를 알려줘." },
  { company: "카카오", question: "최근 실적과 리스크 요인은?" },
  { company: "현대자동차", question: "전기차 관련 전략이 궁금해." },
];

function uid(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// SSE 블록에서 "data:" 줄의 JSON을 뽑는다.
function parseSse(block: string): StreamEvent | null {
  const line = block.split("\n").find((l) => l.startsWith("data:"));
  if (!line) return null;
  try {
    return JSON.parse(line.slice(5).trim()) as StreamEvent;
  } catch {
    return null;
  }
}

type Pinned = { corp_name: string; corp_code: string } | null;

export function ChatClient() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pinned, setPinned] = useState<Pinned>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [company, setCompany] = useState("");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  function push(message: ChatMessage) {
    setMessages((prev) => [...prev, message]);
  }

  function replaceById(id: string, message: ChatMessage) {
    setMessages((prev) => prev.map((m) => (m.id === id ? message : m)));
  }

  function patchStreaming(id: string, patch: Partial<Extract<ChatMessage, { kind: "streaming" }>>) {
    setMessages((prev) =>
      prev.map((m) => (m.id === id && m.kind === "streaming" ? { ...m, ...patch } : m)),
    );
  }

  // 회사명+질문으로 리서치를 SSE 스트리밍한다. (POST /research/stream)
  async function runResearch(corpName: string, q: string) {
    setBusy(true);
    const streamId = uid("stream");
    push({ id: streamId, role: "assistant", kind: "streaming", status: "질문을 분석하고 있어요.", text: "" });

    const started = performance.now();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch("/api/research/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(
          threadId ? { question: q, thread_id: threadId } : { corp_name: corpName, question: q },
        ),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        let detail = "리서치 요청에 실패했습니다.";
        try {
          const data = (await res.json()) as { detail?: string };
          if (data.detail) detail = data.detail;
        } catch {
          // 기본 메시지 유지
        }
        throw new Error(detail);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let streamedText = "";
      let terminal = false;

      while (true) {
        const { done, value } = await reader.read();
        if (value) buffer += decoder.decode(value, { stream: true });

        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const event = parseSse(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf("\n\n");
          if (!event) continue;

          switch (event.type) {
            case "status":
              patchStreaming(streamId, { status: event.message });
              break;
            case "metadata":
              patchStreaming(streamId, { corp_name: event.corp_name, corp_code: event.corp_code });
              break;
            case "chunk":
              streamedText += event.text;
              patchStreaming(streamId, { text: streamedText });
              break;
            case "candidates":
              terminal = true;
              replaceById(streamId, {
                id: uid("cand"),
                role: "assistant",
                kind: "candidates",
                candidates: event.candidates ?? [],
                question: q,
              });
              break;
            case "done": {
              terminal = true;
              const record: ResearchRecord = {
                id: event.id,
                corp_name: event.corp_name,
                corp_code: event.corp_code,
                question: event.question,
                report: event.report,
                news_mode: event.news_mode,
                created_at: event.created_at,
              };
              setPinned({ corp_name: record.corp_name, corp_code: record.corp_code });
              setThreadId(event.thread_id);
              replaceById(streamId, {
                id: uid("report"),
                role: "assistant",
                kind: "report",
                record,
                elapsedMs: performance.now() - started,
              });
              setHistoryKey((key) => key + 1);
              break;
            }
            case "error":
              terminal = true;
              replaceById(streamId, { id: uid("error"), role: "assistant", kind: "error", content: event.message });
              break;
          }
        }

        if (done) break;
      }

      if (!terminal) {
        replaceById(streamId, { id: uid("error"), role: "assistant", kind: "error", content: "응답이 완료되지 않았습니다." });
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        replaceById(streamId, { id: uid("stop"), role: "assistant", kind: "error", content: "답변 생성을 중지했어요." });
      } else {
        const message = error instanceof Error ? error.message : "요청을 처리하지 못했습니다.";
        replaceById(streamId, { id: uid("error"), role: "assistant", kind: "error", content: message });
      }
    } finally {
      abortRef.current = null;
      setBusy(false);
    }
  }

  function submit() {
    if (busy) return;
    const q = question.trim();
    if (!q) return;
    if (pinned) {
      push({ id: uid("user"), role: "user", kind: "text", content: q });
      setQuestion("");
      void runResearch(pinned.corp_name, q);
    } else {
      const c = company.trim();
      if (!c) return;
      push({ id: uid("user"), role: "user", kind: "text", content: `${c} — ${q}` });
      setQuestion("");
      void runResearch(c, q);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  function pickCandidate(candidate: string, q: string) {
    if (busy) return;
    push({ id: uid("user"), role: "user", kind: "text", content: `회사 선택: ${candidate}` });
    void runResearch(candidate, q);
  }

  function reset() {
    if (busy) return;
    setPinned(null);
    setThreadId(null);
    setMessages([]);
    setCompany("");
    setQuestion("");
  }

  const canSend = question.trim().length > 0 && (pinned !== null || company.trim().length > 0);

  return (
    <div className="app">
      <HistorySidebar refreshKey={historyKey} />

      <main className="chat">
        <header className="chat-header">
          <div className="chat-title">
            <h1>ComIn 기업 리서치</h1>
            {pinned ? (
              <span className="pinned">🏢 {pinned.corp_name} · {pinned.corp_code}</span>
            ) : (
              <span className="muted">회사명과 질문을 입력해 조사를 시작하세요</span>
            )}
          </div>
          {pinned ? (
            <button type="button" className="btn ghost" onClick={reset} disabled={busy}>
              새 조사
            </button>
          ) : null}
        </header>

        <div className="messages">
          {messages.length === 0 ? (
            <Welcome
              onPick={(c, q) => {
                setCompany(c);
                setQuestion(q);
              }}
            />
          ) : (
            messages.map((message) => (
              <MessageView key={message.id} message={message} onPickCandidate={pickCandidate} />
            ))
          )}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={onSubmit}>
          {!pinned ? (
            <input
              className="company-input"
              placeholder="회사명 (예: 삼성전자)"
              value={company}
              onChange={(event) => setCompany(event.target.value)}
              disabled={busy}
            />
          ) : null}
          <textarea
            className="question-input"
            placeholder={pinned ? `${pinned.corp_name}에 대해 더 물어보세요…` : "질문 (예: 주요 사업과 최근 이슈는?)"}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            maxLength={2000}
            disabled={busy}
          />
          {busy ? (
            <button type="button" className="btn ghost" onClick={() => abortRef.current?.abort()}>
              중지
            </button>
          ) : null}
          <button className="btn primary" type="submit" disabled={busy || !canSend}>
            {busy ? "생성 중…" : "질문 보내기"}
          </button>
        </form>
        <p className="budget-note">‘질문 보내기’ 1회 = 유료 API 1회 호출</p>
      </main>
    </div>
  );
}

function Welcome({ onPick }: { onPick: (company: string, question: string) => void }) {
  return (
    <div className="welcome">
      <div className="welcome-badge">공시·뉴스 기반 기업 리서치</div>
      <h2>어떤 회사가 궁금하세요?</h2>
      <p className="muted">회사명과 질문을 입력하면 공시와 뉴스를 분석해 리포트를 만들어드려요.</p>
      <div className="suggestions">
        {SUGGESTIONS.map((item) => (
          <button
            type="button"
            key={item.company}
            className="suggestion"
            onClick={() => onPick(item.company, item.question)}
          >
            <strong>{item.company}</strong>
            <span>{item.question}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function MessageView({
  message,
  onPickCandidate,
}: {
  message: ChatMessage;
  onPickCandidate: (candidate: string, question: string) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="msg user">
        <div className="bubble">{message.content}</div>
      </div>
    );
  }

  switch (message.kind) {
    case "streaming":
      return (
        <div className="msg bot">
          <div className="bubble report">
            <div className="report-meta">
              {message.corp_name ? <span className="tag">{message.corp_name}</span> : null}
              {message.corp_code ? <span className="tag light">코드 {message.corp_code}</span> : null}
              <span className="elapsed streaming-status">
                <span className="spinner" aria-hidden="true" />
                {message.text ? "작성 중…" : message.status}
              </span>
            </div>
            {message.text ? <Markdown>{message.text}</Markdown> : null}
          </div>
        </div>
      );
    case "error":
      return (
        <div className="msg bot">
          <div className="bubble error">⚠ {message.content}</div>
        </div>
      );
    case "candidates":
      return (
        <div className="msg bot">
          <div className="bubble">
            <p>입력한 회사명을 찾지 못했어요. 아래 후보 중에서 선택하면 그 회사로 조사합니다.</p>
            <div className="candidates">
              {message.candidates.map((candidate) => (
                <button
                  type="button"
                  key={candidate}
                  className="chip"
                  onClick={() => onPickCandidate(candidate, message.question)}
                >
                  {candidate}
                </button>
              ))}
            </div>
          </div>
        </div>
      );
    case "report":
      return (
        <div className="msg bot">
          <div className="bubble report">
            <div className="report-meta">
              <span className="tag">{message.record.corp_name}</span>
              <span className="tag light">코드 {message.record.corp_code}</span>
              <span className="tag light">뉴스 {message.record.news_mode}</span>
              <span className="elapsed">⏱ {(message.elapsedMs / 1000).toFixed(1)}s</span>
            </div>
            <Markdown>{message.record.report}</Markdown>
          </div>
        </div>
      );
    default:
      return null;
  }
}
