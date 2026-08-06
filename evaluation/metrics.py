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

# 출처 섹션 제목. "## 출처", "**출처**", "출처:" 형태를 잡는다.
_SOURCE_HEADING = re.compile(r"^[ \t]*(?:#+[ \t]*|\*\*)?출처\b", re.MULTILINE)

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


def report_body(report: str) -> str:
    """
    출처 섹션을 잘라내고 본문(답 / 근거 / 한계)만 남긴다.

    출처 목록은 인용 메타데이터를 나열할 뿐이라 문장으로 세면 citation_coverage 분모만 부푼다.
    출처 섹션이 없던 v1 리포트는 원문 그대로 반환되므로 과거 실행과 비교 가능성이 유지된다.
    """
    match = _SOURCE_HEADING.search(report)
    # 자를 때만 뒤 공백을 턴다. 출처가 없는 리포트는 원문 그대로여야 v1 지표가 흔들리지 않는다.
    return report[: match.start()].rstrip() if match else report


def excerpt_section(context: str) -> str:
    """
    컨텍스트에서 [공시 발췌] 부분만 잘라낸다.
    뉴스에 우연히 같은 단어가 있으면 검색 품질을 실제보다 좋게 보이게 하므로 분리한다.
    (베이스라인에서 '배당성향'이 뉴스에만 있었는데 검색 성공으로 잡히던 문제)
    """
    return context.split("[최근 뉴스]")[0]


def has_abstain_phrase(text: str) -> bool:
    """자료 부족을 밝히는 표현이 들어 있는지 확인한다."""
    return any(pattern in text for pattern in _ABSTAIN_PATTERNS)


def classify_abstention(report: str) -> str:
    """
    회피를 세 상태로 나눈다.

    프롬프트가 "질문에 대한 답을 먼저 제시하라"고 지시하므로, 첫 문장에 회피 표현이 오면
    답 자체를 못 한 것("full")이고, 뒤쪽에만 나오면 답은 하고 한계를 밝힌 것("caveat")이다.
    베이스라인 v1의 리포트 18건을 보고 정한 규칙이라 완벽하지 않지만,
    모든 실험에 같은 규칙을 적용하므로 비교 가능성은 유지된다.
    """
    if not has_abstain_phrase(report):
        return "none"
    sentences = split_sentences(report)
    first = sentences[0] if sentences else ""
    return "full" if has_abstain_phrase(first) else "caveat"


def evaluate_item(item: dict, state: dict) -> dict:
    """
    문항 하나의 지표를 계산한다. state는 그래프 최종 상태.
    회사명이 해석되지 않아 리포트가 없으면 후보 반환 여부만 본다.
    """
    report = state.get("report") or ""
    body = report_body(report)
    # 발췌 번호는 서술형 다음에 표가 이어지는 하나의 번호 체계다.
    # 인용 유효 범위와 활용률 분모는 둘을 합친 수가 되어야 한다.
    kept_chunks = list(state.get("kept_chunks") or []) + list(state.get("table_chunks") or [])
    news = state.get("news") or []

    result = {
        "id": item["id"],
        "category": item["category"],
        "corp_name": item["corp_name"],
        "has_report": bool(report),
        "candidates": len(state.get("corp_candidates") or []),
        "news_mode": state.get("news_mode"),
        "news_count": len(news),
        "kept_chunks": len(state.get("kept_chunks") or []),
        "table_chunks": len(state.get("table_chunks") or []),
        "excerpts": len(kept_chunks),
        "retrieve_attempts": state.get("retrieve_attempts", 0),
        "report_chars": len(body),
    }

    if not report:
        # resolve_fail 문항은 후보만 돌려주고 끝난다.
        result["resolve_ok"] = result["candidates"] > 0 if not item["expects_answer"] else False
        return result

    citations = parse_citations(body)
    excerpt_cites = [n for label, n in citations if label == "발췌"]
    news_cites = [n for label, n in citations if label == "뉴스"]

    valid = sum(1 for n in excerpt_cites if 1 <= n <= len(kept_chunks))
    valid += sum(1 for n in news_cites if 1 <= n <= len(news))

    sentences = split_sentences(body)
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
        "abstention": classify_abstention(body),
    })
    result["abstained"] = result["abstention"] == "full"
    # 답이 있어야 하는 문항은 회피하지 않아야, 없는 문항은 회피해야 정답이다.
    result["abstain_ok"] = result["abstained"] != item["expects_answer"]

    # 표에만 있는 근거 문자열이 검색된 발췌에 들어왔는지. 라벨이 있는 문항에서만 계산한다.
    table_evidence = item.get("table_evidence")
    if table_evidence:
        excerpts = excerpt_section(state.get("context") or "")
        result["table_evidence_hit"] = any(term in excerpts for term in table_evidence)

    return result


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _summarize(rows: list[dict]) -> dict:
    """문항 지표 리스트 하나를 요약한다."""
    reports = [r for r in rows if r["has_report"]]
    evidence_rows = [r for r in rows if "table_evidence_hit" in r]
    # judge는 별도 실행(evaluation/judge.py)이라 없을 수 있다. 없으면 None으로 빠진다.
    judged = [r["judge"] for r in reports if r.get("judge")]

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
        # 제공한 발췌 중 실제 인용된 비율. 분모는 서술형 + 표를 합친 발췌 수다.
        # ("excerpts"가 없는 과거 결과 파일은 kept_chunks가 곧 전체 발췌 수였다)
        "context_utilization": _rate(
            sum(r.get("context_used_excerpt", 0) for r in reports),
            sum(r.get("excerpts", r["kept_chunks"]) for r in reports),
        ),
        # 전체 인용 중 공시 발췌 비중 (표 파싱 실험에서 올라가야 한다)
        "excerpt_citation_share": _rate(
            sum(r.get("citations_excerpt", 0) for r in reports),
            sum(r.get("citations_total", 0) for r in reports),
        ),
        "abstain_accuracy": _rate(sum(1 for r in reports if r.get("abstain_ok")), len(reports)),
        "abstain_rate": _rate(sum(1 for r in reports if r.get("abstained")), len(reports)),
        # 답은 했지만 자료 한계를 덧붙인 비율. 회피와 구분해서 본다(정직한 한정은 나쁘지 않다).
        "caveat_rate": _rate(
            sum(1 for r in reports if r.get("abstention") == "caveat"), len(reports)
        ),
        "news_fallback_rate": _rate(
            sum(1 for r in reports if r["news_mode"] == "trend"), len(reports)
        ),
        # 공시 근거가 하나도 없이 생성한 비율 (서술형·표 둘 다 빈 경우)
        "retrieve_gap_rate": _rate(
            sum(1 for r in reports if r.get("excerpts", r["kept_chunks"]) == 0), len(reports)
        ),
        "rewrite_rate": _rate(sum(1 for r in reports if r["retrieve_attempts"] > 1), len(reports)),
        # 표에만 있는 근거가 검색 결과에 들어온 비율. LLM과 무관해 결정론적이다(노이즈 0).
        "table_evidence_recall": _rate(
            sum(1 for r in evidence_rows if r["table_evidence_hit"]), len(evidence_rows)
        ),
        # 인용한 근거가 실제로 그 문장을 뒷받침하는 비율. 정규식 지표가 못 재는 영역이다.
        # citation_coverage는 "인용이 달렸는가"만 보므로 아무 데나 붙여도 만점이 나온다.
        "judge_groundedness": _rate(
            sum(j["supported"] for j in judged), sum(j["claims"] for j in judged)
        ),
        "judge_unsupported_rate": _rate(
            sum(j["unsupported"] for j in judged), sum(j["claims"] for j in judged)
        ),
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
