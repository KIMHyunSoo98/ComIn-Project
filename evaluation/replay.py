"""
평가용 픽스처 재생. 외부 API를 전혀 호출하지 않는다(유료·무료 모두).

수집 지점만 픽스처로 바꾸고, 파싱 / 청킹 / 임베딩 / 검색 / 생성은 그대로 둔다.
공시 원문은 fetch_document_zip만 교체하므로 clean_dart_xml, extract_narrative_text는
평가 대상 안에 남는다(표 파싱 실험이 여기서 측정된다).

install_replay() -> 수집 함수와 벡터 스토어를 픽스처 / 평가 전용으로 교체하는 함수
install_stub_llm() -> 리포트 생성을 고정 문자열로 대체하는 함수 (무과금 검증용)
build_eval_vectorstore() -> 픽스처 원문으로 평가 전용 컬렉션을 적재하는 함수

실행: uv run python -m evaluation.replay   (평가 전용 벡터 스토어 재빌드)
"""

import shutil
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from langchain_chroma import Chroma

import data.dart_origin_document as dart_document
import langgraph_rag.nodes as nodes
from data.collect_data import NEWS_FILTER_DAYS, NEWS_FILTER_NUM
from langchain_rag.vectorstore import (
    get_embeddings,
    check_disclosure_in_db,
    split_disclosure_text,
    store_disclosure,
)
from evaluation.fixture_store import (
    EVAL_DIR,
    MANIFEST_PATH,
    corp_dir,
    doc_path,
    load_json,
    news_key,
)


# 운영 chroma_db와 분리한다. 파싱·청킹이 바뀌면 통째로 지우고 다시 만든다.
CHROMA_EVAL_PATH = str(EVAL_DIR / "chroma_eval")
EVAL_COLLECTION = "disclosures_eval"
EVAL_TABLE_COLLECTION = "disclosures_eval_tables"

STUB_REPORT = "무과금 검증용 스텁 리포트입니다. (발췌 1) (뉴스 1)"

_manifest = None
_corp_by_code: dict[str, str] = {}
_corp_by_rcept: dict[str, str] = {}
_vectorstore = None
_table_vectorstore = None


def load_fixtures() -> dict:
    """manifest를 읽고 corp_code / rcept_no 역매핑을 만든다."""
    global _manifest

    if _manifest is None:
        _manifest = load_json(MANIFEST_PATH)
        for corp_name, info in _manifest["corps"].items():
            _corp_by_code[info["corp_code"]] = corp_name
            for dis in info["disclosures"]:
                _corp_by_rcept[dis["rcept_no"]] = corp_name
    return _manifest


def as_of_cutoff(days: int) -> datetime:
    """뉴스 날짜 필터의 기준 시각. 오늘이 아니라 스냅샷 시점을 쓴다."""
    as_of = datetime.fromisoformat(load_fixtures()["as_of"]).replace(tzinfo=timezone.utc)
    return as_of - timedelta(days=days)


def _fetch_disclosures(corp_code: str, days: int = 730, page_count: int = 10) -> list[dict]:
    corp_name = _corp_by_code.get(corp_code)
    if corp_name is None:
        raise KeyError(f"공시 픽스처 미스: corp_code={corp_code}. dataset.json에 없는 회사입니다.")
    return load_json(corp_dir(corp_name) / "disclosures.json")


def _fetch_news(corp_name: str, query: str, display: int = 10, sort: str = "sim") -> list[dict]:
    key = news_key(corp_name, query, display, sort)
    news = load_json(corp_dir(corp_name) / "news.json")
    if key not in news:
        raise KeyError(
            f"뉴스 픽스처 미스: {key}\n"
            "스냅샷 이후 키워드 추출이나 수집 조건이 바뀌었을 수 있습니다. "
            "snapshot.py를 다시 실행하거나 변경 사항을 확인하세요."
        )
    return news[key]


def _filter_news_by_date(
    news: list[dict], days: int = NEWS_FILTER_DAYS, num: int = NEWS_FILTER_NUM
) -> list[dict]:
    """운영 코드와 같은 로직이되 기준 시각만 as_of로 고정한다."""
    cutoff = as_of_cutoff(days)
    kept = []
    for item in news:
        if parsedate_to_datetime(item["pubDate"]) >= cutoff:
            if len(kept) >= num:
                break
            kept.append(item)
    return kept


def _fetch_document_zip(rcept_no: str) -> bytes:
    corp_name = _corp_by_rcept.get(rcept_no)
    path = doc_path(corp_name, rcept_no) if corp_name else None
    if path is None or not path.exists():
        raise KeyError(f"공시 원문 픽스처 미스: rcept_no={rcept_no}")
    return path.read_bytes()


def get_eval_vectorstore():
    """평가 전용 서술형 Chroma. 운영 chroma_db는 건드리지 않는다."""
    global _vectorstore

    if _vectorstore is None:
        _vectorstore = Chroma(
            collection_name=EVAL_COLLECTION,
            embedding_function=get_embeddings(),
            persist_directory=CHROMA_EVAL_PATH,
            collection_metadata={"hnsw:space": "cosine"},
        )
    return _vectorstore


def get_eval_table_vectorstore():
    """평가 전용 표 Chroma."""
    global _table_vectorstore

    if _table_vectorstore is None:
        _table_vectorstore = Chroma(
            collection_name=EVAL_TABLE_COLLECTION,
            embedding_function=get_embeddings(),
            persist_directory=CHROMA_EVAL_PATH,
            collection_metadata={"hnsw:space": "cosine"},
        )
    return _table_vectorstore


class _StubChain:
    """유료 호출 없이 그래프 전체 경로를 확인할 때 쓰는 가짜 체인."""

    def invoke(self, inputs: dict) -> str:
        return STUB_REPORT


def install_replay() -> None:
    """
    nodes와 dart_document의 수집 지점을 픽스처 버전으로 교체한다.
    nodes가 이름을 바인딩해 import하므로 원본 모듈이 아니라 nodes의 속성을 바꿔야 한다.
    """
    load_fixtures()
    dart_document.fetch_document_zip = _fetch_document_zip
    nodes.fetch_disclosures = _fetch_disclosures
    nodes.fetch_news = _fetch_news
    nodes.filter_news_by_date = _filter_news_by_date
    nodes.get_vectorstore = get_eval_vectorstore
    nodes.get_table_vectorstore = get_eval_table_vectorstore


def install_stub_llm() -> None:
    """리포트 생성을 고정 문자열로 대체한다. 유료 호출이 0이 된다."""
    nodes.build_report_chain = lambda: _StubChain()
    nodes.build_followup_chain = lambda: _StubChain()


def build_eval_vectorstore(rebuild: bool = True) -> dict:
    """
    픽스처 원문을 파싱 / 청킹해 평가 전용 컬렉션에 적재한다.

    rebuild=True면 기존 컬렉션을 지우고 다시 만든다. 파싱이나 청킹을 바꾸면
    청크 수가 달라져 id가 겹치지 않는 잔재가 남으므로 반드시 새로 만들어야 한다.
    질문마다 index 시간이 들쭉날쭉해지지 않도록 평가 전에 미리 채워둔다.
    """
    global _vectorstore, _table_vectorstore

    manifest = load_fixtures()
    if rebuild:
        shutil.rmtree(CHROMA_EVAL_PATH, ignore_errors=True)
        _vectorstore = None
        _table_vectorstore = None

    install_replay()
    narrative_store = get_eval_vectorstore()
    table_store = get_eval_table_vectorstore()
    stats = {}

    for corp_name, info in manifest["corps"].items():
        counts = {"서술형": 0, "표": 0}
        for dis in info["disclosures"]:
            rcept_no = dis["rcept_no"]
            narrative, tables = dart_document.get_disclosure_texts(rcept_no)
            for store, text, key in (
                (narrative_store, narrative, "서술형"),
                (table_store, tables, "표"),
            ):
                if not text or check_disclosure_in_db(store, rcept_no):
                    continue
                documents = split_disclosure_text(text, rcept_no, info["corp_code"])
                store_disclosure(store, documents)
                counts[key] += len(documents)
        stats[corp_name] = counts
        print(
            f"  {corp_name:6} 공시 {len(info['disclosures'])}건 -> "
            f"서술형 {counts['서술형']}개 / 표 {counts['표']}개"
        )

    return stats


if __name__ == "__main__":
    print(f"평가 전용 벡터 스토어 재빌드: {CHROMA_EVAL_PATH}")
    stats = build_eval_vectorstore(rebuild=True)
    narrative = sum(c["서술형"] for c in stats.values())
    tables = sum(c["표"] for c in stats.values())
    print(f"\n완료: 서술형 {narrative}개 / 표 {tables}개")
