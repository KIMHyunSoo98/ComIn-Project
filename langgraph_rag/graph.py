"""
노드들을 엣지로 이어 실행 가능한 그래프로 조립한다.

1차 이터레이션은 LangChain과 같은 선형 흐름이다:
START -> collect -> index -> retrieve -> generate -> END

LangChain 버전에서는 main()의 호출 순서가 곧 파이프라인이었지만,
LangGraph에서는 흐름이 엣지 선언으로 분리되어 있어 조건 분기(저품질 검색, 회사명 퍼지매칭)나 병렬 수집을 넣을 때
노드 코드를 건드리지 않고 엣지만 바꾸면 된다.

build_graph() -> 컴파일된 그래프를 만드는 함수
"""

from langgraph.graph import StateGraph, START, END

from langgraph_rag.state import ResearchState
from langgraph_rag.nodes import collect, index, retrieve, generate


def build_graph():
    """
    상태 스키마와 노드 4개로 선형 그래프를 만들어 컴파일한다.
    반환값은 invoke로 실행할 수 있는 Runnable이다.
    """
    builder = StateGraph(ResearchState)

    builder.add_node("collect", collect)
    builder.add_node("index", index)
    builder.add_node("retrieve", retrieve)
    builder.add_node("generate", generate)

    builder.add_edge(START, "collect")
    builder.add_edge("collect", "index")
    builder.add_edge("index", "retrieve")
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", END)

    return builder.compile()
