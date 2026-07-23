"""
LangGraph로 마이그레이션한 RAG 파이프라인의 엔트리포인트.
컴포넌트는 data/와 langchain_rag/의 것을 그대로 쓴다.

흐름:
1. 회사명 / 질문 입력 -> 초기 상태를 만들어 그래프 invoke
2. 회사명이 해석 안 되면 그래프가 퍼지매칭 후보를 보여주고 재입력 받음
3. 정상 실행이면 최종 상태의 report를 출력 (유료 API는 실행당 1회)
"""

from langgraph_rag.state import initial_state
from langgraph_rag.graph import build_graph


def main() -> None:
    """
    회사명과 질문을 입력받아 그래프를 실행하고 리포트를 출력한다.
    회사명이 해석되지 않으면 퍼지매칭 후보를 보여주고 다시 입력받는다.
    """
    graph = build_graph()

    question = ""
    while True:
        corp_name = input("회사명: ")
        if not question:
            question = input("질문: ")

        try:
            final_state = graph.invoke(initial_state(corp_name, question))
        except ValueError as e:
            print(e)            
            continue

        if "report" in final_state:
            break

        candidates = final_state.get("corp_candidates")
        if candidates:
            print(f"'{corp_name}'와 일치하는 회사가 없습니다. 비슷한 이름: {', '.join(candidates)}")
        else:
            print(f"'{corp_name}'와 비슷한 이름의 회사가 없습니다. 회사명을 다시 입력해 주세요.")

    print("=" * 20 + "보고서" + "=" * 20)
    print(final_state["report"])


if __name__ == "__main__":
    main()
