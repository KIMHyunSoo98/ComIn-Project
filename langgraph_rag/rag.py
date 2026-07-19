"""
LangGraph로 마이그레이션한 RAG 파이프라인의 엔트리포인트.
langchain_rag/rag.py와 흐름은 동일하다.
컴포넌트는 data/와 langchain_rag/의 것을 그대로 쓴다.

흐름:
1. 회사명 / 질문 입력 -> 초기 상태를 만들어 그래프 invoke
2. collect -> index -> retrieve -> generate 노드가 순서대로 실행
3. 회사명이 잘못되면 collect 노드의 ValueError로 실행이 중단되고, 다시 입력받아 재실행
4. 최종 상태의 report를 출력 (유료 API는 실행당 1회)
"""

from langgraph_rag.state import initial_state
from langgraph_rag.graph import build_graph


def main() -> None:
    """
    회사명과 질문을 입력받아 그래프를 실행하고 리포트를 출력한다.
    회사명이 잘못되면(ValueError) 다시 입력받는다.
    """
    graph = build_graph()

    question = ""
    while True:
        corp_name = input("회사명: ")
        if not question:
            question = input("질문: ")
        try:
            final_state = graph.invoke(initial_state(corp_name, question))
            break
        except ValueError as e:
            print(e)

    print("="*20 + "보고서" "="*20)
    print(final_state["report"])


if __name__ == "__main__":
    main()
