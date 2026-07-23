"""
LangGraph 파이프라인이 노드 간에 주고받는 상태 정의.

ResearchState -> 그래프 전체가 공유하는 상태 스키마
initial_state() -> 회사명/질문으로 그래프 입력용 초기 상태를 만드는 함수
"""

from typing import TypedDict
from langchain_core.documents import Document


class ResearchState(TypedDict):
    """
    리서치 파이프라인의 전체 상태.
    """

    # 입력
    corp_name: str
    question: str

    # resolve_corp 노드 결과 (매칭하는 회사명 못찾으면 corp_code 없이 corp_candidates만 존재)
    corp_code: str
    stock_code: str
    corp_candidates: list[str]

    # analyze_query 노드 결과
    keywords: list[str]
    news_mode: str  # "keyword"(A: 회사명+키워드) | "trend"(B: 회사명만+최신순)
    search_query: str  # retrieve가 쓰는 쿼리. 처음엔 질문 전문, 재검색 시 키워드로 교체

    # collect_disclosures / collect_news 노드 결과
    disclosures: list[dict]
    news: list[dict]

    # retrieve 노드 결과
    kept_chunks: list[tuple[Document, float]]
    retrieve_attempts: int  # 재검색 상한 제어용

    # generate 노드 결과
    context: str
    report: str

    # 이번 그래프 실행에서 사용한 유료 API 호출 횟수
    paid_call_count: int


def initial_state(corp_name: str, question: str) -> ResearchState:
    """
    그래프 입력용 초기 상태를 만든다.
    """
    return {
        "corp_name": corp_name,
        "question": question,
        "retrieve_attempts": 0,
        "paid_call_count": 0,
    }
