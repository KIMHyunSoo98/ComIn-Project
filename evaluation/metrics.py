"""
1층 지표 계산기. LLM을 쓰지 않고 리포트 텍스트와 그래프 최종 상태만으로 계산한다.

저장된 리포트만 있으면 나중에 지표를 새로 정의해도 과거 실행에 소급 적용할 수 있다.
그래서 러너는 리포트 전문과 상태를 결과 파일에 함께 남긴다.

parse_citations() -> 리포트에서 (발췌 n) / (뉴스 n) 인용을 뽑는 함수
evaluate_item() -> 문항 하나의 지표를 계산하는 함수
aggregate() -> 문항별 지표를 전체 / 카테고리별로 집계하는 함수
"""

import re


# "(발췌 2)", "(발췌 1, 2)", "(발췌 2, 뉴스 3)", "(뉴스 1)(발췌 4)" 형태를 모두 잡는다.
_CITATION_BLOCK = re.compile(r"\(([^()]*(?:발췌|뉴스)[^()]*)\)")
_CITATION_TOKEN = re.compile(r"(발췌|뉴스)|(\d+)")

# 문장 분리. 마크다운 제목과 목록 기호는 인용 대상에서 제외한다.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+\.)\s*")

# 자료 부족을 밝히는 표현. 프롬프트가 지시한 문구와 그 변형을 함께 본다.
_ABSTAIN_PATTERNS = ("확인할 수 없", "확인이 불가", "확인되지 않", "알 수 없")


def parse_citations(report: str) -> list[tuple[str, int]]:
    """
    리포트에서 인용을 ("발췌"|"뉴스", 번호) 리스트로 뽑는다.
    한 괄호에 번호가 여러 개면 직전 라벨에 이어 붙인다. ("(발췌 1, 2)" -> 발췌 1, 발췌 2)
    """
    citations = []
    for block in _CITATION_BLOCK.findall(report):
        label = None
        for match in _CITATION_TOKEN.finditer(block):
            if match.group(1):
                label = match.group(1)
            elif label:
                citations.append((label, int(match.group(2))))
    return citations


def split_sentences(report: str) -> list[str]:
    """
    인용 커버리지를 재기 위한 문장 분리. 제목(#)과 빈 줄은 버린다.
    마크다운 리포트라 완벽한 분리는 아니고, 같은 규칙을 모든 실험에 적용해 비교 가능성만 유지한다.
    """
    sentences = []
    for raw in _SENTENCE_SPLIT.split(report):
        text = _LIST_MARKER.sub("", raw).strip()
        if not text or text.startswith("#"):
            continue
        sentences.append(text)
    return sentences


def is_abstained(report: str) -> bool:
    """자료가 없어 답할 수 없다고 밝혔는지 확인한다."""
    return any(pattern in report for pattern in _ABSTAIN_PATTERNS)


def evaluate_item(item: dict, state: dict) -> dict:
    """
    문항 하나의 지표를 계산한다. state는 그래프 최종 상태.
    회사명이 해석되지 않아 리포트가 없으면 후보 반환 여부만 본다.
    """
    report = state.get("report") or ""
    kept_chunks = state.get("kept_chunks") or []
    news = state.get("news") or []

    result = {
        "id": item["id"],
        "category": item["category"],
        "corp_name": item["corp_name"],
        "has_report": bool(report),
        "candidates": len(state.get("corp_candidates") or []),
        "news_mode": state.get("news_mode"),
        "news_count": len(news),
        "kept_chunks": len(kept_chunks),
        "retrieve_attempts": state.get("retrieve_attempts", 0),
        "report_chars": len(report),
    }

    if not report:
        # resolve_fail 문항은 후보만 돌려주고 끝난다.
        result["resolve_ok"] = result["candidates"] > 0 if not item["expects_answer"] else False
        return result

    citations = parse_citations(report)
    excerpt_cites = [n for label, n in citations if label == "발췌"]
    news_cites = [n for label, n in citations if label == "뉴스"]

    valid = sum(1 for n in excerpt_cites if 1 <= n <= len(kept_chunks))
    valid += sum(1 for n in news_cites if 1 <= n <= len(news))

    sentences = split_sentences(report)
    cited_sentences = sum(1 for s in sentences if _CITATION_BLOCK.search(s))

    result.update({
        "citations_total": len(citations),
        "citations_valid": valid,
        "citations_excerpt": len(excerpt_cites),
        "citations_news": len(news_cites),
        # 검색된 발췌 중 실제로 인용된 비율. 낮으면 k를 줄이거나 임계값을 올릴 근거가 된다.
        "context_used_excerpt": len({n for n in excerpt_cites if 1 <= n <= len(kept_chunks)}),
        "context_used_news": len({n for n in news_cites if 1 <= n <= len(news)}),
        "sentences": len(sentences),
        "cited_sentences": cited_sentences,
        "abstained": is_abstained(report),
    })
    # 답이 있어야 하는 문항은 회피하지 않아야, 없는 문항은 회피해야 정답이다.
    result["abstain_ok"] = result["abstained"] != item["expects_answer"]

    answer_doc = item.get("answer_doc")
    if answer_doc and item.get("recall_applicable", True):
        found = any(doc.metadata.get("rcept_no") == answer_doc for doc, _ in kept_chunks)
        result["recall_hit"] = found

    return result


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _summarize(rows: list[dict]) -> dict:
    """문항 지표 리스트 하나를 요약한다."""
    reports = [r for r in rows if r["has_report"]]
    recall_rows = [r for r in rows if "recall_hit" in r]

    return {
        "items": len(rows),
        "reports": len(reports),
        # 존재하지 않는 발췌/뉴스 번호를 인용하지 않은 비율
        "citation_validity": _rate(
            sum(r.get("citations_valid", 0) for r in reports),
            sum(r.get("citations_total", 0) for r in reports),
        ),
        # 사실 문장 중 근거를 표기한 비율
        "citation_coverage": _rate(
            sum(r.get("cited_sentences", 0) for r in reports),
            sum(r.get("sentences", 0) for r in reports),
        ),
        # 제공한 발췌 중 실제 인용된 비율
        "context_utilization": _rate(
            sum(r.get("context_used_excerpt", 0) for r in reports),
            sum(r["kept_chunks"] for r in reports),
        ),
        # 전체 인용 중 공시 발췌 비중 (표 파싱 실험에서 올라가야 한다)
        "excerpt_citation_share": _rate(
            sum(r.get("citations_excerpt", 0) for r in reports),
            sum(r.get("citations_total", 0) for r in reports),
        ),
        "abstain_accuracy": _rate(sum(1 for r in reports if r.get("abstain_ok")), len(reports)),
        "abstain_rate": _rate(sum(1 for r in reports if r.get("abstained")), len(reports)),
        "news_fallback_rate": _rate(
            sum(1 for r in reports if r["news_mode"] == "trend"), len(reports)
        ),
        "retrieve_gap_rate": _rate(sum(1 for r in reports if r["kept_chunks"] == 0), len(reports)),
        "rewrite_rate": _rate(sum(1 for r in reports if r["retrieve_attempts"] > 1), len(reports)),
        "recall": _rate(sum(1 for r in recall_rows if r["recall_hit"]), len(recall_rows)),
        "avg_report_chars": round(sum(r["report_chars"] for r in reports) / len(reports)) if reports else None,
    }


def aggregate(rows: list[dict]) -> dict:
    """전체와 카테고리별로 집계한다. 전체 평균만 보면 개선이 가려지므로 카테고리를 함께 낸다."""
    by_category = {}
    for row in rows:
        by_category.setdefault(row["category"], []).append(row)

    return {
        "overall": _summarize(rows),
        "by_category": {name: _summarize(items) for name, items in sorted(by_category.items())},
    }
