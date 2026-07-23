"""
LangGraph 그래프를 구성하는 노드 함수들.
컴포넌트(수집 data/, 벡터스토어/체인 langchain_rag/)는 그대로 import해서 사용한다.

resolve_corp() -> 회사명을 corp_code로 해석하는 노드. 실패 시 퍼지매칭 후보만 채운다
analyze_query() -> 질문에서 키워드를 뽑아 뉴스 검색 경로(A/B)를 정하는 노드
collect_disclosures() -> 공시 목록을 수집하는 노드
collect_news() -> 뉴스를 수집하는 노드. A(회사명+키워드) 실패 시 B(회사명만+최신순)
index() -> 미적재 공시만 원문 추출 / 청킹 / 벡터 스토어 적재하는 노드
retrieve() -> search_query로 유사 청크를 검색하고 임계값 필터를 거는 노드
rewrite_query() -> 재검색용으로 search_query를 키워드로 교체하는 노드
generate() -> 컨텍스트를 조립하고 리포트를 생성하는 노드
"""

from data.config import check_keys
from data.corp_code import find_corp_code, normalize_corp_name, find_corp_candidates
from data.collect_data import fetch_disclosures, fetch_news, filter_news_by_date
from data.dart_origin_document import get_disclosure_text
from langchain_rag.vectorstore import (
    RELEVANCE_THRESHOLD,
    get_vectorstore,
    split_disclosure_text,
    store_disclosure,
    check_disclosure_in_db,
    search_disclosure,
)
from langchain_rag.chain import build_context, build_report_chain
from langgraph_rag.state import ResearchState
from langgraph_rag.query_analysis import extract_keywords


# 그래프 실행(invoke) 1번당 허용하는 유료 API 호출 상한.
MAX_PAID_CALLS_PER_RUN = 1

# 검색 시도 상한 (첫 검색 1회 + 키워드 재검색 1회)
MAX_RETRIEVE_ATTEMPTS = 2


def resolve_corp(state: ResearchState) -> dict:
    """
    회사명을 corp_code로 해석한다.
    완전일치 -> 정규화 후 재시도 -> 그래도 없으면 퍼지매칭 후보만 채워서 반환한다.
    자동 선택은 하지 않고, 후보 제시까지만 한다.
    """
    check_keys()

    corp = find_corp_code(state["corp_name"])
    if corp is None:
        corp = find_corp_code(normalize_corp_name(state["corp_name"]))
    if corp is None:
        return {"corp_candidates": find_corp_candidates(state["corp_name"])}

    return {
        "corp_name": corp["corp_name"],
        "corp_code": corp["corp_code"],
        "stock_code": corp["stock_code"],
    }


def analyze_query(state: ResearchState) -> dict:
    """
    질문에서 키워드를 뽑아 뉴스 검색 경로를 정한다.
    키워드가 있으면 A(회사명+키워드), 없으면 B(회사명만+최신순 동향).
    첫 공시 검색 쿼리는 질문 전문을 쓴다 - 임베딩 검색은 자연어 문장에 강하다.
    """
    keywords = extract_keywords(state["question"], state["corp_name"])
    return {
        "keywords": keywords,
        "news_mode": "keyword" if keywords else "trend",
        "search_query": state["question"],
    }


def collect_disclosures(state: ResearchState) -> dict:
    """
    공시 목록을 수집한다. (collect_news와 병렬 실행)
    """
    disclosures = fetch_disclosures(state["corp_code"])
    if not disclosures:
        raise ValueError(f"{state['corp_name']}의 공시 정보가 없습니다.")
    return {"disclosures": disclosures}


def collect_news(state: ResearchState) -> dict:
    """
    뉴스를 수집한다. (collect_disclosures와 병렬 실행)
    A 경로(회사명+키워드, 유사도순)가 0건이면 B 경로(회사명만, 최신순)를 실행한다.
    B도 0건이면 뉴스 없이 진행하고, 컨텍스트에 '관련 뉴스 없음'으로 표시된다.
    """
    if state["news_mode"] == "keyword":
        news = fetch_news(state["corp_name"], " ".join(state["keywords"]), display=20, sort="sim")
        news = filter_news_by_date(news, days=90, num=10)
        if news:
            return {"news": news, "news_mode": "keyword"}

    news = fetch_news(state["corp_name"], "", display=20, sort="date")
    news = filter_news_by_date(news, days=90, num=10)
    return {"news": news, "news_mode": "trend"}


def index(state: ResearchState) -> dict:
    """
    수집된 공시 중 아직 적재되지 않은 것만 원문 추출/청킹/벡터 스토어 적재한다.
    결과는 벡터 스토어에 저장되는 효과라 상태 변경은 없다.
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
    search_query로 해당 회사의 유사 청크를 검색하고 임계값 필터를 건다.
    전부 미달이면 빈 리스트를 반환하고, 다음 갈래(키워드 재검색 / 뉴스만 생성)는 그래프의 조건부 엣지가 정한다.
    """
    vectorstore = get_vectorstore()
    results = search_disclosure(vectorstore, state["search_query"], state["corp_code"], k=3)
    kept = [(doc, score) for doc, score in results if score >= RELEVANCE_THRESHOLD]
    return {"kept_chunks": kept, "retrieve_attempts": state["retrieve_attempts"] + 1}


def rewrite_query(state: ResearchState) -> dict:
    """
    질문 전문 검색이 실패했을 때, 키워드만 남긴 쿼리로 교체해 재검색을 준비한다.
    """
    return {"search_query": " ".join(state["keywords"])}


def generate(state: ResearchState) -> dict:
    """
    청크와 뉴스로 컨텍스트를 조립하고 체인을 실행해 리포트를 생성한다.
    프로젝트의 유일한 유료 API 호출 지점이다.
    관련 청크가 없으면 뉴스만으로 생성한다 - 컨텍스트에 '공시 발췌 없음'이 명시되고,
    프롬프트 규칙에 따라 자료가 없으면 보고서에 명시한다.

    상태의 paid_call_count가 상한 이상이면 호출 전에 종료된다.
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
