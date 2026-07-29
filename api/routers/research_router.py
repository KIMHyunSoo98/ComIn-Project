"""
엔드포인트
- POST/research        : 회사명 + 질문 → 리포트 (일치하는 회사명 못찾으면 회사명 후보)
- POST/research/stream : POST/research 엔드포인트의 스트리밍 버전
- GET/research        : 조사 이력 목록
- GET/research/{id}   : 조사 이력 한 건

현재 엔드포인트는 동기이다.
그래프 invoke는 임베딩/LLM으로 블로킹되는데, FastAPI가 동기 경로를 스레드풀에서 실행하므로 이벤트 루프를 막지 않는다.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from api.database import get_session
from api.schemas.research_schema import ResearchRequest, ResearchResponse, ResearchRecord
from api.controllers import research_controller

router = APIRouter(tags=["research"])


@router.post("/research", response_model=ResearchResponse)
def create_research(payload: ResearchRequest, db: Session = Depends(get_session)):
    try:
        return research_controller.run_research(db, payload.corp_name, payload.question)
    except ValueError as e:
        # 공시/뉴스가 없어 리서치를 만들 수 없는 경우
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/research/stream")
def create_research_stream(payload: ResearchRequest, db: Session = Depends(get_session)):
    """
    보고서를 SSE로 스트리밍한다.
    POST /research와 동작은 같지만 진행 상황과 리포트 토큰을 실시간으로 흘려보낸다.
    """
    return StreamingResponse(
        research_controller.stream_research(db, payload.corp_name, payload.question, payload.thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"}#, "X-Accel-Buffering": "no"}, # 스트리밍 응답이니 중간에 캐시나 버퍼를 모아두지 말라는 헤더 / 현재는 nginx 없어서 뒤의 헤더는 안붙인다.
    )


@router.get("/research", response_model=list[ResearchRecord])
def list_research(db: Session = Depends(get_session)):
    return research_controller.list_research(db)


@router.get("/research/{research_id}", response_model=ResearchRecord)
def get_research(research_id: int, db: Session = Depends(get_session)):
    research = research_controller.get_research(db, research_id)
    if not research:
        raise HTTPException(status_code=404, detail="입력한 id에 해당하는 보고서가 없습니다.")
    return research
