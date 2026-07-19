"""
LangGraph 파이프라인이 노드 간에 주고받는 상태 정의.

LangChain 버전에서는 main() 안의 지역 변수로 흘러가던 중간 결과를 LangGraph에서는 명시적인 상태 스키마로 선언한다.
- 각 노드는 상태 전체를 읽고, 자신이 바꾼 필드만 부분 딕셔너리로 반환한다.
- 상태가 명시적이라 실행 후 중간 결과(검색된 청크, 조립된 컨텍스트 등)를 들여다볼 수 있다.

ResearchState -> 그래프 전체가 공유하는 상태 스키마
initial_state() -> 회사명/질문으로 그래프 입력용 초기 상태를 만드는 함수
"""

from typing import TypedDict
from langchain_core.documents import Document


class ResearchState(TypedDict):
    """
    리서치 파이프라인의 전체 상태.

    paid_call_count는 예산 방어용 카운터다.
    """

    # 입력
    corp_name: str
    question: str

    # collect 노드 결과
    corp_code: str
    stock_code: str
    disclosures: list[dict]
    news: list[dict]

    # retrieve 노드 결과 
    kept_chunks: list[tuple[Document, float]]

    # generate 노드 결과
    context: str
    report: str

    # 이번 그래프 실행에서 사용한 유료 API 호출 횟수
    paid_call_count: int


def initial_state(corp_name: str, question: str) -> ResearchState:
    """
    그래프 입력용 초기 상태를 만든다.
    paid_call_count를 0으로 명시해, 예산 방어가 카운터 없이 실행되는 일이 없게 한다.
    """
    return {
        "corp_name": corp_name,
        "question": question,
        "paid_call_count": 0,
    }
