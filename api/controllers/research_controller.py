"""
그래프 실행과 보고서 이력 DB 저장/조회를 담당하는 비즈니스 로직.
그래프는 한 번만 컴파일해 재사용한다.
"""

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import MemorySaver

from langgraph_rag.state import initial_state
from langgraph_rag.graph import build_graph
from api.models.research_model import Research


_graph = None


def get_graph():
    """
    컴파일된 그래프를 한 번만 만들어 재사용한다.
    """
    global _graph
    if _graph is None:
        # 멀티턴 대화를 위해 checkpointer를 붙여 컴파일한다(thread_id로 턴 간 상태 유지).
        # 지금은 InMemory라 서버가 종료되면 사라진다. 후에 sqliteSaver나 postgres를 사용할 예정.
        _graph = build_graph(checkpointer=MemorySaver())
    return _graph


def run_research(db: Session, corp_name: str, question: str) -> dict:
    """
    그래프를 실행한다.

    공시/뉴스가 없어 보고서를 만들 수 없으면 그래프가 ValueError를 던지고 라우터가 이를 HTTP 422로 변환한다.
    유료 호출은 그래프 실행당 1회로 제한된다.
    """
    # checkpointer가 붙어 있어 thread_id가 필요하다. 블로킹 단발 실행은 매번 새 스레드를 쓴다.
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    final_state = get_graph().invoke(initial_state(corp_name, question), config=config)

    if "report" in final_state:
        research = Research(
            corp_name=final_state["corp_name"],  
            corp_code=final_state["corp_code"],
            question=question,
            report=final_state["report"],
            news_mode=final_state.get("news_mode", ""),
        )
        db.add(research)
        db.commit()
        db.refresh(research)
        return {"status": "ok", "result": research}

    return {"status": "candidates", "candidates": final_state.get("corp_candidates", [])}


def list_research(db: Session):
    """
    보고서 이력을 최신순으로 반환한다.
    """
    return db.scalars(select(Research).order_by(Research.id.desc())).all()


def get_research(db: Session, research_id: int):
    """
    보고서 이력을 한 건을 반환한다. 없으면 None.
    """
    return db.scalar(select(Research).where(Research.id == research_id))


# 그래프 노드명 -> 사용자에게 보여줄 진행 단계 문구.
_STAGE_MESSAGES = {
    "resolve_corp": "회사명을 확인하고 있어요.",
    "analyze_query": "질문을 분석하고 있어요.",
    "collect_disclosures": "공시를 수집하고 있어요.",
    "collect_news": "뉴스를 수집하고 있어요.",
    "index": "문서를 색인하고 있어요.",
    "retrieve": "관련 자료를 검색하고 있어요.",
    "rewrite_query": "검색어를 바꿔 다시 찾고 있어요.",
    "generate": "리포트를 작성하고 있어요.",
}


def _sse(event: dict) -> str:
    """
    dict를 SSE 한 블록(data: ...\\n\\n)으로 직렬화한다.
    """
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _chunk_text(message) -> str:
    """
    messages 스트림의 메시지 청크에서 텍스트만 뽑는다.
    """
    if not isinstance(message, BaseMessage):
        return ""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def stream_research(db: Session, corp_name: str | None, question: str, thread_id: str | None = None):
    """
    그래프를 스트리밍 실행하며 SSE 이벤트를 만든다.

    - thread_id 없음 -> 새 대화(첫 턴): initial_state로 전체 파이프라인 실행.
    - thread_id 있음 -> 후속 턴: 회사/공시/뉴스는 checkpointer가 복원, 새 질문으로 재검색만.

    이벤트: status / metadata / candidates / chunk / done / error.
    유료 호출은 턴당 1회로 제한된다. 오류로 토큰 스트리밍이 안 돼도 done.report로 전문을 전달한다.
    """
    is_followup = thread_id is not None
    if not thread_id:
        thread_id = uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}

    # 후속 턴은 새 질문과 리셋 필드만 넣는다(나머지 상태는 checkpointer가 유지 -> 공시·뉴스 재사용).
    graph_input = (
        {"question": question, "retrieve_attempts": 0, "paid_call_count": 0}
        if is_followup
        else initial_state(corp_name, question)
    )

    report_text = ""
    ended_with_candidates = False

    try:
        for mode, chunk in get_graph().stream(
            graph_input, config=config, stream_mode=["updates", "messages"]
        ):
            if mode == "updates":
                for node, update in chunk.items():
                    if not isinstance(update, dict):
                        continue

                    # 회사명 미해결: corp_code 없이 후보만 반환하고 그래프가 종료된다.
                    if node == "resolve_corp" and "corp_code" not in update:
                        ended_with_candidates = True
                        yield _sse({"type": "candidates", "candidates": update.get("corp_candidates", [])})
                        continue

                    if node == "resolve_corp":
                        yield _sse({
                            "type": "metadata",
                            "corp_name": update.get("corp_name"),
                            "corp_code": update.get("corp_code"),
                        })

                    yield _sse({"type": "status", "stage": node, "message": _STAGE_MESSAGES.get(node, node)})

            elif mode == "messages":
                message, meta = chunk
                if meta.get("langgraph_node") != "generate":
                    continue
                text = _chunk_text(message)
                if text:
                    report_text += text
                    yield _sse({"type": "chunk", "text": text})

    except ValueError as exc:
        # 공시/뉴스가 없어 리서치를 만들 수 없는 경우 등
        yield _sse({"type": "error", "message": str(exc)})
        return
    except Exception as exc:  # noqa: BLE001
        yield _sse({"type": "error", "message": f"리서치 중 오류가 발생했습니다: {exc}"})
        return

    if ended_with_candidates:
        return

    # 후속 턴은 resolve/collect가 안 돌아 updates에 회사 정보가 없다 -> 해당 thread_id의 가장 최신 state에서 정보를 가져온다.
    final = get_graph().get_state(config).values
    final_report = final.get("report") or report_text
    if not final_report:
        yield _sse({"type": "error", "message": "리포트를 생성하지 못했습니다."})
        return

    research = Research(
        corp_name=final.get("corp_name") or corp_name or "",
        corp_code=final.get("corp_code", ""),
        question=question,
        report=final_report,
        news_mode=final.get("news_mode", ""),
    )
    db.add(research)
    db.commit()
    db.refresh(research)

    yield _sse({
        "type": "done",
        "id": research.id,
        "thread_id": thread_id,
        "corp_name": research.corp_name,
        "corp_code": research.corp_code,
        "question": research.question,
        "report": research.report,
        "news_mode": research.news_mode,
        "created_at": research.created_at.isoformat(),
    })
