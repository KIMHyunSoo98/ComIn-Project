"""
LangChain으로 마이그레이션한 RAG 파이프라인의 엔트리포인트.
vanilla_rag/rag.py와 흐름은 동일하고, 청킹 / 임베딩 / 벡터스토어 / 검색 / 생성만
LangChain 컴포넌트로 교체했다.

데이터 수집(data/)은 LangChain이 대체할 영역이 아니라 그대로 사용한다.
(Phase 3에서 LangGraph의 tool로 감쌀 예정)

흐름:
1. 회사명 / 질문 입력 -> research()로 공시 목록과 뉴스 수집
2. 공시별로 DB 적재 여부 확인 -> 없으면 원문 추출 / 청킹 / 적재
3. 질문으로 유사 청크 검색 -> 임계값 필터
4. 청크 + 뉴스를 컨텍스트로 조립 -> 체인 실행해 리포트 생성 (유료 API 1회)
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
from langchain_rag.chain import build_context, generate_report


def ask_corp_and_question() -> tuple[str, str]:
    """
    회사명과 질문을 입력받는다. 회사명이 잘못되면 다시 입력받는다.
    (vanilla_rag/rag.py의 while 루프를 함수로 분리)
    """
    question = ""
    while True:
        corp_name = input("회사명: ")
        if not question:
            question = input("질문: ")
        try:
            information = research(corp_name=corp_name, query=question)
            return corp_name, question, information
        except ValueError as e:
            print(e)


def index_disclosures(vectorstore, disclosures: list[dict], corp_code: str) -> None:
    """
    공시 목록을 받아 아직 적재되지 않은 공시만 원문 추출 / 청킹 / 벡터 스토어 적재한다.
    이미 적재된 공시는 재임베딩을 생략한다.
    """
    for dis in disclosures:
        rcept_no = dis.get("rcept_no")
        if check_disclosure_in_db(vectorstore, rcept_no):
            continue
        
        text = get_disclosure_text(rcept_no)
        documents = split_disclosure_text(text, rcept_no, corp_code)
        store_disclosure(vectorstore, documents)


def main() -> None:
    """
    전체 RAG 파이프라인을 실행하고 리포트를 출력한다.
    """
    corp_name, question, information = ask_corp_and_question()

    corp_code = information.get("corp_code")
    vectorstore = get_vectorstore()

    index_disclosures(vectorstore, information.get("disclosures"), corp_code)

    results = search_disclosure(vectorstore, question, corp_code, k=3)
    kept = filter_disclosure_by_relevance(results)

    news = information.get("news")
    context = build_context(kept, news)
    report = generate_report(corp_name, question, context)
    print(report)


if __name__ == "__main__":
    main()
