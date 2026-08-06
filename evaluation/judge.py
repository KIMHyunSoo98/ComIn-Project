"""
LLM-as-judge. 저장된 리포트의 각 주장이 '인용한 근거로' 뒷받침되는지 판정한다.

정규식 지표는 "인용이 달려 있는가"만 잰다. 그 인용이 문장을 실제로 뒷받침하는지는 못 잰다
- (발췌 2)를 아무 데나 붙여도 citation_coverage는 만점이다. 그 공백을 메운다.

그래프 실행과 무관한 오프라인 판정이라 결과 파일만 있으면 언제든 소급 적용된다.
리포트 전문과 컨텍스트가 결과 파일에 남아 있어 유료 재생성이 필요 없다.

유료 호출은 리포트 1개당 1회. 주장마다 부르면 문항당 5~6회가 되어 배보다 배꼽이 커진다.

judge 자체도 결정론적이지 않다. 같은 리포트를 두 번 판정하면 결과가 달라질 수 있으므로,
judge 점수도 단일 실행으로 비교하지 않는다. 판정자 모델을 바꾸면 편차가 15~21%p로 훨씬 커진다
(측정: claude-opus-5 vs gpt-5.6 계열). 비교하려는 버전은 반드시 같은 판정자로 재판정할 것.

실행:
  uv run python -m evaluation.judge <결과파일> --limit 3          # 호출 수만 보여주고 중단
  uv run python -m evaluation.judge <결과파일> --limit 3 --confirm # 3건만 판정 (프롬프트 확인용)
  uv run python -m evaluation.judge <결과파일> --confirm --write   # 전체 판정 후 파일에 반영
  uv run python -m evaluation.judge <결과파일> --export-labels labels.csv   # 무과금
  uv run python -m evaluation.judge <결과파일> --compare labels.csv         # 무과금

collect_evidence() -> 컨텍스트에서 번호별 발췌/뉴스 원문을 뽑는 함수
extract_claims() -> 리포트에서 인용이 달린 문장을 뽑는 함수
judge_report() -> 리포트 하나를 판정하는 함수 (유료 1회)
export_labels() -> 사람이 채울 라벨링 CSV를 뽑는 함수 (무과금)
compare_labels() -> 사람 라벨과 judge 판정의 일치율을 내는 함수 (무과금)
"""

import argparse
import csv
import hashlib
import os
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from evaluation.fixture_store import load_json, save_json
from evaluation.metrics import _CITATION_BLOCK, aggregate, report_body, split_sentences
from observability.tracing import setup_tracing, trace_config

load_dotenv()

# 판정자. 2026-08-06 claude-opus-5 -> gpt-5.6-luna 전환(비용 + Anthropic 워크스페이스 한도).
# 인간 라벨 30개 일치율은 86.7% -> 80.0%로 떨어졌다. 대신 전 버전을 같은 판정자로 정렬했다.
# 판정자를 바꾸면 과거 수치와 비교가 끊긴다 - 바꿀 때는 비교 대상 버전을 전부 다시 판정할 것.
JUDGE_MODEL = "gpt-5.6-luna"
MAX_TOKENS = 4096

# build_context()가 쓰는 머리말. 원문을 번호별로 되찾는 데 쓴다.
# 공시번호는 근거에 남긴다 - 리포트가 "공시번호 20260310002820에 따르면"처럼 인용하는데
# 머리말째 버리면 판정자가 확인할 방법이 없어 멀쩡한 문장이 partial로 깎인다.
_EXCERPT_HEAD = re.compile(r"발췌 (\d+) \(공시번호: ([^,)]*)[^)]*\):")
_NEWS_HEAD = re.compile(r"뉴스 (\d+):")
_CITATION_TOKEN = re.compile(r"(발췌|뉴스)|(\d+)")

SYSTEM = (
    "너는 리서치 리포트의 근거를 검증하는 심사자다. "
    "각 주장이 '그 주장이 인용한 근거'만으로 뒷받침되는지 판정해라.\n"
    "- supported: 주장의 핵심 내용이 인용된 근거에서 직접 확인된다.\n"
    "- partial: 일부만 확인되거나, 근거를 넘어선 해석·일반화·수치 변형이 섞였다.\n"
    "- unsupported: 인용된 근거로는 확인되지 않는다.\n"
    "\n"
    "판정 기준\n"
    "- 사실관계만 본다. 문장이 잘 쓰였는지, 질문에 유용한지는 판정 대상이 아니다.\n"
    "- 인용하지 않은 다른 근거에 그 내용이 있더라도 supported로 보지 마라. "
    "'인용이 올바른가'를 재는 것이다.\n"
    "- '주어진 자료로는 확인할 수 없음'처럼 한계를 밝힌 문장은, 그 판단이 근거와 맞으면 supported다.\n"
    "- reason은 한 문장으로 짧게 쓴다."
)


class ClaimVerdict(BaseModel):
    """주장 하나에 대한 판정."""

    index: int = Field(description="주장 번호 (1부터)")
    verdict: Literal["supported", "partial", "unsupported"]
    reason: str = Field(description="판정 이유 한 문장")


class JudgeResult(BaseModel):
    """리포트 하나의 판정 결과."""

    verdicts: list[ClaimVerdict]


def collect_evidence(context: str) -> dict[tuple[str, int], str]:
    """
    컨텍스트를 ("발췌"|"뉴스", 번호) -> 원문 딕셔너리로 되돌린다.
    build_context()가 붙인 머리말을 경계로 자른다.
    """
    evidence: dict[tuple[str, int], str] = {}
    body, _, news_part = context.partition("[최근 뉴스]")

    for label, section, pattern in (
        ("발췌", body, _EXCERPT_HEAD),
        ("뉴스", news_part, _NEWS_HEAD),
    ):
        marks = list(pattern.finditer(section))
        for i, mark in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(section)
            text = section[mark.end():end].strip()
            # 유사도는 검색 산물이라 뺀다. 공시번호는 근거의 일부이므로 남긴다.
            if label == "발췌":
                text = f"(공시번호: {mark.group(2)})\n{text}"
            evidence[(label, int(mark.group(1)))] = text
    return evidence


def _cited_in(sentence: str) -> list[tuple[str, int]]:
    """문장 하나에 표기된 (라벨, 번호) 인용을 뽑는다."""
    found = []
    for block in _CITATION_BLOCK.findall(sentence):
        label = None
        for match in _CITATION_TOKEN.finditer(block):
            if match.group(1):
                label = match.group(1)
            elif label:
                found.append((label, int(match.group(2))))
    return found


def extract_claims(report: str) -> list[tuple[str, list[tuple[str, int]]]]:
    """리포트 본문에서 인용이 달린 문장만 (문장, 인용목록)으로 뽑는다."""
    return [
        (sentence, _cited_in(sentence))
        for sentence in split_sentences(report_body(report))
        if _CITATION_BLOCK.search(sentence)
    ]


def build_judge_prompt(question: str, claims: list, evidence: dict) -> str:
    """
    판정용 프롬프트를 만든다. 근거는 한 번만 싣고 주장이 번호로 참조한다
    (같은 발췌를 여러 주장이 인용해도 원문이 중복되지 않는다).
    """
    used = sorted({key for _, cites in claims for key in cites if key in evidence})

    lines = [f"질문: {question}", "", "[근거 자료]"]
    for label, number in used:
        lines.append(f"\n{label} {number}:\n{evidence[(label, number)]}")

    lines += ["", "[판정할 주장]"]
    for i, (sentence, cites) in enumerate(claims, start=1):
        tags = ", ".join(f"{label} {n}" for label, n in cites) or "인용 없음"
        lines.append(f"{i}. (인용: {tags}) {sentence}")

    lines.append("")
    lines.append(f"주장 {len(claims)}개 각각을 판정해라. 번호를 빠뜨리지 마라.")
    return "\n".join(lines)


def _strict_schema(schema: dict) -> dict:
    """OpenAI structured outputs는 모든 object에 additionalProperties=false와 전체 required를 요구한다."""
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}))
    for key in ("properties", "$defs"):
        for value in schema.get(key, {}).values():
            _strict_schema(value)
    if "items" in schema:
        _strict_schema(schema["items"])
    return schema


class _OpenAIJudge:
    """
    OpenAI 모델용 어댑터. ChatAnthropic 체인과 같은 invoke 시그니처를 맞춰
    judge_report()를 고치지 않고 판정자만 갈아끼운다. 새 의존성 없이 httpx로 직접 호출한다.
    """

    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, model: str):
        import httpx

        self._httpx = httpx
        self.model = model
        self.key = os.getenv("OPENAI_API_KEY")
        if not self.key:
            raise RuntimeError("OPENAI_API_KEY가 없습니다. .env를 확인하세요")

    def invoke(self, messages, config=None) -> JudgeResult:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system" if role == "system" else "user", "content": text}
                for role, text in messages
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "judge_result",
                    "strict": True,
                    "schema": _strict_schema(JudgeResult.model_json_schema()),
                },
            },
        }
        response = self._httpx.post(
            self.URL,
            headers={"Authorization": f"Bearer {self.key}"},
            json=payload,
            timeout=180,
        )
        response.raise_for_status()
        return JudgeResult.model_validate_json(response.json()["choices"][0]["message"]["content"])


def get_judge_chain(model: str = JUDGE_MODEL):
    """판정용 체인. 스키마를 강제해 파싱 실패를 없앤다."""
    if not model.startswith("claude"):
        return _OpenAIJudge(model)

    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model_name=model, max_tokens=MAX_TOKENS)
    return llm.with_structured_output(JudgeResult)


def judge_report(chain, question: str, report: str, context: str, run_name: str = "judge") -> dict | None:
    """
    리포트 하나를 판정한다. 유료 호출 1회.
    인용이 달린 문장이 없으면 None (판정 대상 아님).
    """
    claims = extract_claims(report)
    if not claims:
        return None

    evidence = collect_evidence(context)

    # 인용 번호가 범위 밖이면 근거가 없으므로 호출 없이 unsupported로 둔다.
    resolvable = [i for i, (_, cites) in enumerate(claims, start=1)
                  if any(key in evidence for key in cites)]
    if not resolvable:
        verdicts = [
            {"index": i, "verdict": "unsupported", "reason": "인용한 근거가 컨텍스트에 없음"}
            for i in range(1, len(claims) + 1)
        ]
    else:
        result = chain.invoke(
            [("system", SYSTEM), ("human", build_judge_prompt(question, claims, evidence))],
            config=trace_config(run_name, judge_model=JUDGE_MODEL),
        )
        verdicts = [v.model_dump() for v in result.verdicts]

    counts = {"supported": 0, "partial": 0, "unsupported": 0}
    for v in verdicts:
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1

    # 판정이 빠진 주장은 unsupported로 세지 않는다. 분모를 실제 판정 수로 맞춘다.
    for i, (sentence, _) in enumerate(claims, start=1):
        for v in verdicts:
            if v["index"] == i:
                v["claim"] = sentence[:200]
                break

    return {"claims": len(verdicts), **counts, "verdicts": verdicts}


LABEL_COLUMNS = [
    "label_id", "item_id", "claim_index", "category", "question",
    "claim", "cited", "evidence", "human_verdict", "note",
]


def export_labels(result: dict, out_path: Path, sample: int, seed: int, table_only: bool = False) -> int:
    """
    사람이 직접 판정할 CSV를 뽑는다. 유료 호출 없음.

    judge 판정은 일부러 넣지 않는다. 먼저 보면 그 판정에 끌려가(anchoring) 검증이 무의미해진다.
    카테고리별로 고르게 뽑아, 한 종류 문항에서만 맞는 judge를 걸러낼 수 있게 한다.

    table_only=True면 표 근거를 인용한 주장만 뽑는다. 발췌 번호가 서술형 개수를 넘으면 표다
    (build_context가 서술형 다음에 표를 이어 붙인다). 표 행은 생김새가 산문과 전혀 달라
    ("배당성향(%) — 당기(제9기): 16.91") judge가 제대로 읽는지 따로 검증해야 한다.
    """
    pool = []
    for row in result["items"]:
        if not row.get("report"):
            continue
        evidence = collect_evidence(row.get("context", ""))
        narrative_count = row.get("kept_chunks", 0)
        for i, (sentence, cites) in enumerate(extract_claims(row["report"]), start=1):
            if table_only and not any(
                label == "발췌" and n > narrative_count for label, n in cites
            ):
                continue
            texts = [f"[{label} {n}]\n{evidence[(label, n)]}"
                     for label, n in cites if (label, n) in evidence]
            if not texts:
                continue  # 근거가 없으면 사람도 판정할 수 없다
            pool.append({
                "item_id": row["id"],
                "claim_index": i,
                "category": row["category"],
                "question": row["question"],
                "claim": sentence,
                "cited": ", ".join(f"{label} {n}" for label, n in cites),
                "evidence": "\n\n".join(texts),
            })

    by_category = defaultdict(list)
    for claim in pool:
        by_category[claim["category"]].append(claim)

    # 카테고리별 비례 배분 + 최소 1개. 부족분은 큰 카테고리에서 채운다.
    rng = random.Random(seed)
    picked = []
    for category, claims in sorted(by_category.items()):
        quota = max(1, round(sample * len(claims) / len(pool)))
        picked += rng.sample(claims, min(quota, len(claims)))
    rest = [c for c in pool if c not in picked]
    if len(picked) < sample and rest:
        picked += rng.sample(rest, min(sample - len(picked), len(rest)))
    picked = picked[:sample]
    rng.shuffle(picked)  # 카테고리 순서로 몰려 있으면 라벨링이 한쪽으로 쏠린다

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for n, claim in enumerate(picked, start=1):
            writer.writerow({**claim, "label_id": n, "human_verdict": "", "note": ""})

    print(f"라벨링 CSV: {out_path}  ({len(picked)}개 / 전체 주장 {len(pool)}개)")
    print("  카테고리 분포:", dict(Counter(c["category"] for c in picked)))
    return len(picked)


def compare_labels(result: dict, csv_path: Path) -> None:
    """사람이 채운 라벨과 judge 판정의 일치율을 낸다. 유료 호출 없음."""
    judged = {row["id"]: row.get("judge") for row in result["items"] if row.get("judge")}
    if not judged:
        raise SystemExit("judge 판정이 없습니다. --confirm --write로 먼저 판정하세요.")

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    labeled = [r for r in rows if r["human_verdict"].strip()]
    if not labeled:
        raise SystemExit(f"human_verdict가 비어 있습니다: {csv_path}")

    pairs, missing = [], 0
    for row in labeled:
        verdict = judged.get(row["item_id"])
        match = next(
            (v for v in (verdict or {}).get("verdicts", [])
             if v["index"] == int(row["claim_index"])), None
        )
        if match is None:
            missing += 1
            continue
        pairs.append((row["human_verdict"].strip(), match["verdict"]))

    agree = sum(1 for h, j in pairs if h == j)
    # supported / not-supported 2분류 일치율. 3분류는 partial 경계가 모호해 낮게 나온다.
    binary = sum(1 for h, j in pairs if (h == "supported") == (j == "supported"))

    print(f"비교 대상 {len(pairs)}개" + (f" (judge 판정 없음 {missing}개 제외)" if missing else ""))
    print(f"  3분류 일치율        {agree}/{len(pairs)} = {agree / len(pairs):.1%}")
    print(f"  supported 여부 일치  {binary}/{len(pairs)} = {binary / len(pairs):.1%}")
    print("\n  혼동 행렬 (행=사람, 열=judge)")
    labels = ["supported", "partial", "unsupported"]
    print(f"    {'':14}" + "".join(f"{l:>13}" for l in labels))
    for h in labels:
        counts = [sum(1 for a, b in pairs if a == h and b == j) for j in labels]
        print(f"    {h:14}" + "".join(f"{c:>13}" for c in counts))


def prompt_fingerprint() -> str:
    """판정 기준이 바뀐 것을 나중에 알아볼 수 있도록 프롬프트 해시를 남긴다."""
    return hashlib.sha256(SYSTEM.encode("utf-8")).hexdigest()[:12]


def main() -> None:
    parser = argparse.ArgumentParser(description="ComIn LLM-as-judge (오프라인 근거 검증)")
    parser.add_argument("path", help="평가 결과 파일 경로")
    parser.add_argument("--limit", type=int, help="앞에서 N개 리포트만 판정 (프롬프트 확인용)")
    parser.add_argument("--confirm", action="store_true", help="유료 실행 승인")
    parser.add_argument("--write", action="store_true", help="결과 파일에 반영")
    parser.add_argument("--export-labels", metavar="CSV", help="사람이 채울 라벨링 CSV를 뽑는다 (무과금)")
    parser.add_argument("--sample", type=int, default=30, help="라벨링 표본 수 (기본 30)")
    parser.add_argument("--seed", type=int, default=20260804, help="표본 추출 시드")
    parser.add_argument("--compare", metavar="CSV", help="채워진 라벨과 judge 일치율 비교 (무과금)")
    parser.add_argument("--table-only", action="store_true", help="표 근거를 인용한 주장만 표본에 넣는다")
    parser.add_argument("--model", default=JUDGE_MODEL, help=f"판정 모델 (기본 {JUDGE_MODEL})")
    parser.add_argument("--skip-judged", action="store_true", help="이미 판정된 문항은 건너뛴다 (재개용)")
    args = parser.parse_args()

    result = load_json(args.path)

    if args.export_labels:
        export_labels(result, Path(args.export_labels), args.sample, args.seed, args.table_only)
        return

    if args.compare:
        compare_labels(result, Path(args.compare))
        return
    targets = [r for r in result["items"] if r.get("report") and extract_claims(r["report"])]
    if args.skip_judged:
        targets = [r for r in targets if not r.get("judge")]
    if args.limit:
        targets = targets[: args.limit]

    if not args.confirm:
        claims = sum(len(extract_claims(r["report"])) for r in targets)
        parser.error(
            f"리포트 {len(targets)}건 / 주장 {claims}개를 판정합니다. "
            f"유료 호출 {len(targets)}회. --confirm을 붙이세요."
        )

    setup_tracing()
    chain = get_judge_chain(args.model)
    by_id = {}

    def flush() -> None:
        """지금까지의 판정을 파일에 반영한다. 중간에 죽어도 산 판정을 잃지 않는다."""
        if not args.write or not by_id:
            return
        for row in result["items"]:
            if row["id"] in by_id:
                row["judge"] = by_id[row["id"]]
        result["summary"] = aggregate(result["items"])
        result["meta"]["judge"] = {
            "model": args.model,
            "prompt_fingerprint": prompt_fingerprint(),
            "judged_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reports": len(by_id),
        }
        save_json(args.path, result)

    for row in targets:
        verdict = judge_report(
            chain, row["question"], row["report"], row.get("context", ""),
            run_name=f"judge-{row['id']}",
        )
        if verdict is None:
            continue
        by_id[row["id"]] = verdict
        rate = verdict["supported"] / verdict["claims"] if verdict["claims"] else 0
        print(
            f"  {row['id']:8} {row['category']:16} "
            f"supported {verdict['supported']}/{verdict['claims']} ({rate:.0%})"
        )
        # 판정을 눈으로 검증할 수 있어야 한다. supported가 아닌 것만 사유와 함께 보여준다.
        for v in verdict["verdicts"]:
            if v["verdict"] != "supported":
                print(f"      [{v['verdict']}] {v.get('claim', '')[:70]}")
                print(f"        -> {v['reason'][:110]}")
        flush()  # 리포트 한 건 끝날 때마다 저장. 중간에 죽어도 산 판정은 남는다.

    total = sum(v["claims"] for v in by_id.values())
    supported = sum(v["supported"] for v in by_id.values())
    partial = sum(v["partial"] for v in by_id.values())
    print(f"\n[judge] 주장 {total}개 중 supported {supported} ({supported / total:.1%}), partial {partial}")
    print(f"반영 완료: {args.path}" if args.write else "미반영(--write로 저장)")


if __name__ == "__main__":
    main()
