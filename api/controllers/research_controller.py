"""
그래프 실행과 보고서 이력 DB 저장/조회를 담당하는 비즈니스 로직.
그래프는 한 번만 컴파일해 재사용한다.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

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
        _graph = build_graph()
    return _graph


def run_research(db: Session, corp_name: str, question: str) -> dict:
    """
    그래프를 실행한다.

    공시/뉴스가 없어 보고서를 만들 수 없으면 그래프가 ValueError를 던지고,
    라우터가 이를 HTTP 422로 변환한다. 유료 호출은 그래프 실행당 1회로 제한된다.
    """
    final_state = get_graph().invoke(initial_state(corp_name, question))

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
