"""
LangGraph 그래프를 구성하는 노드 함수들.

LangChain 버전의 langchain_rag/rag.py main()이 순서대로 호출하던 단계를 노드 하나당 한 단계씩 함수로 감쌌다.
컴포넌트(수집 data/, 벡터스토어/체인 langchain_rag/)는 LangGraph가 대체할 영역이 아니라 그대로 import해서 쓴다.

각 노드는 상태를 읽고, 자신이 바꾼 필드만 부분 딕셔너리로 반환한다.

collect() -> 회사명/질문으로 공시 목록과 뉴스를 수집하는 노드
index() -> 미적재 공시만 원문 추출 / 청킹 / 벡터 스토어 적재하는 노드
retrieve() -> 질문으로 유사 청크를 검색하고 임계값 필터를 거는 노드
generate() -> 컨텍스트를 조립하고 리포트를 생성하는 노드 
"""

from data.collect_data import research
from data.dart_origin_document import get_disclosure_text
from langchain_rag.vectorstore import (
    get_vectorstore,
    split_disclosure_text,
    store_disclosure,
    check_disclosure_in_db,
    search_disclosure,
    filter_disclosure_by_relevance,
)
from langchain_rag.chain import build_context, build_report_chain
from langgraph_rag.state import ResearchState


# 그래프 실행(invoke) 1번당 허용하는 유료 API 호출 상한.
MAX_PAID_CALLS_PER_RUN = 1


def collect(state: ResearchState) -> dict:
    """
    회사명과 질문으로 공시 목록과 뉴스를 수집한다.
    회사명이 잘못되면 research()가 ValueError를 던지고, 그래프 실행이 그대로 중단된다.
    """
    information = research(corp_name=state["corp_name"], query=state["question"])
    return {
        "corp_code": information["corp_code"],
        "stock_code": information["stock_code"],
        "disclosures": information["disclosures"],
        "news": information["news"],
    }


def index(state: ResearchState) -> dict:
    """
    수집된 공시 중 아직 적재되지 않은 것만 원문 추출/청킹/벡터 스토어 적재한다.
    결과는 벡터 스토어에 쌓이는 부수효과라 상태 변경은 없다.
    """
    vectorstore = get_vectorstore()
    for dis in state["disclosures"]:
        rcept_no = dis.get("rcept_no")
        if check_disclosure_in_db(vectorstore, rcept_no):
            continue

        text = get_disclosure_text(rcept_no)
        documents = split_disclosure_text(text, rcept_no, state["corp_code"])
        store_disclosure(vectorstore, documents)

    return {}


def retrieve(state: ResearchState) -> dict:
    """
    질문으로 해당 회사의 유사 청크를 검색하고 임계값 필터를 건다.
    전부 임계값 미달이면 top-1만 남긴다.
    """
    vectorstore = get_vectorstore()
    results = search_disclosure(vectorstore, state["question"], state["corp_code"], k=3)
    kept = filter_disclosure_by_relevance(results)
    return {"kept_chunks": kept}


def generate(state: ResearchState) -> dict:
    """
    청크와 뉴스로 컨텍스트를 조립하고 체인을 실행해 리포트를 생성한다.
    프로젝트의 유일한 유료 API 호출 지점이다.

    상태의 paid_call_count가 상한 이상이면 호출 전에 종료된다.
    멀티 턴에서는 어떻게 할지 고민해봐야한다.
    """
    if state["paid_call_count"] >= MAX_PAID_CALLS_PER_RUN:
        raise RuntimeError(
            f"유료 API는 그래프 실행당 {MAX_PAID_CALLS_PER_RUN}회만 호출합니다. "
            f"(현재 {state['paid_call_count']}회)"
        )

    context = build_context(state["kept_chunks"], state["news"])
    chain = build_report_chain()
    report = chain.invoke(
        {"corp_name": state["corp_name"], "question": state["question"], "context": context}
    )

    return {
        "context": context,
        "report": report,
        "paid_call_count": state["paid_call_count"] + 1,
    }
