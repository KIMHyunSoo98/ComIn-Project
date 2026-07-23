"""
노드들을 엣지로 이어 실행 가능한 그래프로 컴파일한다.

build_graph() -> 컴파일된 그래프를 만드는 함수
"""

from langgraph.graph import StateGraph, START, END

from langgraph_rag.state import ResearchState
from langgraph_rag.nodes import (
    MAX_RETRIEVE_ATTEMPTS,
    resolve_corp,
    analyze_query,
    collect_disclosures,
    collect_news,
    index,
    retrieve,
    rewrite_query,
    generate,
)


def route_after_resolve(state: ResearchState) -> str:
    """
    corp_code가 존재(회사명으로 검색 성공)하면 진행, 아니면 후보만 출력하고 END 노드로.
    """
    return "analyze" if "corp_code" in state else "end"


def route_after_retrieve(state: ResearchState) -> str:
    """
    공시정보 관련 청크가 있으면 생성으로 간다.
    없으면 키워드 재검색을 한 번 시도하고, 그래도 없으면 뉴스만으로 생성한다.
    키워드가 없거나 이미 키워드로 검색한 경우엔 재검색해도 같은 결과라 바로 생성으로 간다.
    """
    if state["kept_chunks"]:
        return "generate"

    keyword_query = " ".join(state["keywords"])
    can_rewrite = (
        state["retrieve_attempts"] < MAX_RETRIEVE_ATTEMPTS
        and state["keywords"]
        and state["search_query"] != keyword_query
    )
    return "rewrite" if can_rewrite else "generate"


def build_graph():
    
    builder = StateGraph(ResearchState)

    # 노드 추가
    builder.add_node("resolve_corp", resolve_corp)
    builder.add_node("analyze_query", analyze_query)
    builder.add_node("collect_disclosures", collect_disclosures)
    builder.add_node("collect_news", collect_news)
    builder.add_node("index", index)
    builder.add_node("retrieve", retrieve)
    builder.add_node("rewrite_query", rewrite_query)
    builder.add_node("generate", generate)

    # 엣지 추가
    builder.add_edge(START, "resolve_corp")
    builder.add_conditional_edges(
        "resolve_corp", route_after_resolve, {"analyze": "analyze_query", "end": END}
    )

    # 공시정보와 뉴스 병렬 수집
    builder.add_edge("analyze_query", "collect_disclosures")
    builder.add_edge("analyze_query", "collect_news")
    builder.add_edge(["collect_disclosures", "collect_news"], "index")

    builder.add_edge("index", "retrieve")
    builder.add_conditional_edges(
        "retrieve", route_after_retrieve, {"generate": "generate", "rewrite": "rewrite_query"}
    )
    builder.add_edge("rewrite_query", "retrieve")
    builder.add_edge("generate", END)

    return builder.compile()
