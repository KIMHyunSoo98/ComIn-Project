"""
평가용 픽스처 스냅샷 생성기. DART / 네이버만 호출하며 유료 API는 쓰지 않는다.

수집 결과(공시 목록·뉴스)를 고정해 평가 재현성을 확보한다.
공시 원문은 rcept_no가 가리키는 불변 문서라 raw zip 그대로 저장하고,
파싱(clean_dart_xml / extract_narrative_text)은 평가 대상으로 남긴다.

실행: uv run python -m evaluation.snapshot

collect_questions() -> 데이터셋에서 회사별 첫 턴 질문 목록을 모으는 함수
snapshot_corp() -> 회사 하나의 공시 목록 / 원문 zip / 뉴스를 저장하는 함수
main() -> 전체 회사를 순회하고 manifest.json을 쓰는 함수
"""

import hashlib
from datetime import date, datetime

from data.config import check_keys
from data.collect_data import (
    NEWS_DISPLAY,
    NEWS_FILTER_DAYS,
    NEWS_FILTER_NUM,
    NEWS_SORT_KEYWORD,
    NEWS_SORT_TREND,
    fetch_disclosures,
    fetch_news,
)
from data.dart_origin_document import fetch_document_zip
from langgraph_rag.query_analysis import extract_keywords
from evaluation.fixture_store import (
    DATASET_PATH,
    MANIFEST_PATH,
    corp_dir,
    doc_path,
    load_json,
    news_key,
    save_json,
)


def collect_questions(dataset: dict) -> dict[str, list[str]]:
    """
    회사별로 뉴스 수집이 실제 일어나는 질문만 모은다.
    멀티턴은 첫 턴만 수집하고(후속 턴은 그래프가 자료를 재사용) 후속 질문은 제외한다.
    """
    questions: dict[str, list[str]] = {}
    for item in dataset["items"]:
        questions.setdefault(item["corp_name"], []).append(item["question"])
    for scenario in dataset["multiturn"]:
        questions.setdefault(scenario["corp_name"], []).append(scenario["turns"][0])
    return questions


def snapshot_corp(corp_name: str, corp_code: str, questions: list[str]) -> dict:
    """
    회사 하나의 픽스처를 저장하고 manifest 항목을 반환한다.
    이미 받아둔 zip은 다시 받지 않는다(원문은 불변).
    """
    disclosures = fetch_disclosures(corp_code)
    save_json(corp_dir(corp_name) / "disclosures.json", disclosures)

    docs = []
    for dis in disclosures:
        rcept_no = dis["rcept_no"]
        path = doc_path(corp_name, rcept_no)
        if path.exists():
            zip_bytes = path.read_bytes()
        else:
            # 원문이 없는 공시(정정 공시 등)가 있다. index 노드도 이런 건을 건너뛰므로
            # 픽스처에 '원문 없음'으로 기록해 재생 때 같은 상황을 재현한다.
            try:
                zip_bytes = fetch_document_zip(rcept_no)
            except Exception as exc:  # noqa: BLE001
                docs.append({**dis, "sha256": None, "bytes": 0, "error": str(exc).splitlines()[0]})
                print(f"    공시 {rcept_no} {dis['report_nm']} - 원문 없음, 건너뜀")
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zip_bytes)
        docs.append({
            **dis,
            "sha256": hashlib.sha256(zip_bytes).hexdigest(),
            "bytes": len(zip_bytes),
        })
        print(f"    공시 {rcept_no} {dis['report_nm']} ({len(zip_bytes) / 1_000_000:.1f}MB)")

    # A 경로(회사명+키워드, 유사도순): 질문마다 쿼리가 다르다.
    news: dict[str, list[dict]] = {}
    for question in questions:
        query = " ".join(extract_keywords(question, corp_name))
        key = news_key(corp_name, query, NEWS_DISPLAY, NEWS_SORT_KEYWORD)
        if key in news:
            continue
        news[key] = fetch_news(corp_name, query, display=NEWS_DISPLAY, sort=NEWS_SORT_KEYWORD)

    # B 경로(회사명만, 최신순): A가 0건일 때의 폴백이라 질문과 무관하게 1건만 있으면 된다.
    trend_key = news_key(corp_name, "", NEWS_DISPLAY, NEWS_SORT_TREND)
    news[trend_key] = fetch_news(corp_name, "", display=NEWS_DISPLAY, sort=NEWS_SORT_TREND)

    save_json(corp_dir(corp_name) / "news.json", news)
    print(f"    뉴스 쿼리 {len(news)}종 (A {len(news) - 1} + B 1)")

    return {"corp_code": corp_code, "disclosures": docs, "news_queries": sorted(news)}


def main() -> None:
    check_keys()
    dataset = load_json(DATASET_PATH)
    questions = collect_questions(dataset)

    manifest = {
        "as_of": date.today().isoformat(),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset_version": dataset["_meta"]["version"],
        # 재생 시 filter_news_by_date를 as_of 기준으로 이 값으로 다시 적용한다.
        "news_filter": {"days": NEWS_FILTER_DAYS, "num": NEWS_FILTER_NUM},
        "corps": {},
    }

    for corp_name, info in dataset["_meta"]["corps"].items():
        print(f"[{corp_name}] 스냅샷")
        manifest["corps"][corp_name] = snapshot_corp(
            corp_name, info["corp_code"], questions.get(corp_name, [])
        )

    save_json(MANIFEST_PATH, manifest)

    total_docs = sum(len(c["disclosures"]) for c in manifest["corps"].values())
    total_bytes = sum(d["bytes"] for c in manifest["corps"].values() for d in c["disclosures"])
    print(
        f"\n완료: 회사 {len(manifest['corps'])} / 공시 원문 {total_docs}건 "
        f"({total_bytes / 1_000_000:.0f}MB) / as_of={manifest['as_of']}"
    )
    print(f"manifest: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
