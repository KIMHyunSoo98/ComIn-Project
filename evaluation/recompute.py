"""
저장된 결과 파일의 리포트로 지표를 다시 계산한다. 유료 호출 없음.

지표 정의가 바뀌거나 문항 분류가 바뀌었을 때, 과거 실행을 다시 돌리지 않고 같은 기준으로 맞춘다.
리포트 / 컨텍스트 원문은 그대로 두고 파생 지표만 덮어쓰므로 몇 번을 돌려도 결과가 같다.

실행:
  uv run python -m evaluation.recompute            # 변화만 출력
  uv run python -m evaluation.recompute --write    # 결과 파일에 반영

rebuild_state() -> 저장된 행에서 지표 계산에 필요한 상태를 복원하는 함수
recompute_file() -> 결과 파일 하나를 다시 계산하는 함수
"""

import argparse
import re
from datetime import datetime
from types import SimpleNamespace

from evaluation.fixture_store import DATASET_PATH, EVAL_DIR, load_json, save_json
from evaluation.metrics import aggregate, evaluate_item

RESULTS_DIR = EVAL_DIR / "results"

# build_context가 "발췌 1 (공시번호: 20260310002820, 유사도: 0.589):" 형태로 적어둔다.
_RCEPT_NO = re.compile(r"공시번호:\s*(\d+)")


def rebuild_state(row: dict) -> dict:
    """
    저장된 행에서 지표 계산용 상태를 복원한다.
    청크 본문은 없어도 되고, 개수와 rcept_no만 있으면 인용 검증과 recall을 계산할 수 있다.
    rcept_no는 컨텍스트 문자열에서 뽑는다(러너가 따로 저장하지 않아도 소급 계산이 된다).
    """
    rcept_nos = _RCEPT_NO.findall(row.get("context", ""))
    chunks = []
    for i in range(row["kept_chunks"]):
        rcept_no = rcept_nos[i] if i < len(rcept_nos) else None
        chunks.append((SimpleNamespace(metadata={"rcept_no": rcept_no}), 0.0))

    return {
        "report": row.get("report", ""),
        "context": row.get("context", ""),
        "kept_chunks": chunks,
        "news": [{}] * row["news_count"],
        "news_mode": row["news_mode"],
        "retrieve_attempts": row["retrieve_attempts"],
        "corp_candidates": [None] * row["candidates"],
    }


def _items_by_id(dataset: dict) -> dict[str, dict]:
    """데이터셋을 id로 색인한다. 멀티턴은 턴별 항목으로 펼친다."""
    items = {item["id"]: item for item in dataset["items"]}
    for scenario in dataset["multiturn"]:
        for turn_no in range(1, len(scenario["turns"]) + 1):
            turn_id = f"{scenario['id']}-t{turn_no}"
            items[turn_id] = {
                "id": turn_id,
                "category": "followup",
                "corp_name": scenario["corp_name"],
                "expects_answer": True,
            }
    return items


def recompute_file(path, dataset: dict, write: bool) -> tuple[dict, dict]:
    """결과 파일 하나를 다시 계산하고 (기존 요약, 새 요약)을 반환한다."""
    result = load_json(path)
    catalog = _items_by_id(dataset)

    rows = []
    for row in result["items"]:
        item = catalog.get(row["id"])
        if item is None:
            # 데이터셋에서 빠진 문항. 지표에서 제외하되 원자료는 남긴다.
            rows.append(row)
            continue
        fresh = evaluate_item(item, rebuild_state(row))
        # 원자료와 실행 정보는 보존하고 파생 지표만 갈아끼운다.
        preserved = {
            key: row[key]
            for key in ("question", "elapsed_sec", "nodes_run", "report", "context", "turn")
            if key in row
        }
        rows.append({**fresh, **preserved})

    before, after = result["summary"], aggregate(rows)
    if write:
        result["items"] = rows
        result["summary"] = after
        result["meta"]["recomputed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        save_json(path, result)
    return before, after


def main() -> None:
    parser = argparse.ArgumentParser(description="저장된 결과의 지표 재계산")
    parser.add_argument("--write", action="store_true", help="결과 파일에 반영")
    args = parser.parse_args()

    dataset = load_json(DATASET_PATH)
    paths = sorted(RESULTS_DIR.glob("*.json"))
    if not paths:
        print("결과 파일이 없습니다.")
        return

    for path in paths:
        before, after = recompute_file(path, dataset, args.write)
        print(f"\n[{path.name}]")
        for key, new in after["overall"].items():
            old = before["overall"].get(key)
            mark = "  <-- 변경" if old != new else ""
            print(f"  {key:24} {str(old):>8} -> {str(new):>8}{mark}")

    print(f"\n{'반영 완료' if args.write else '미반영(--write로 저장)'}: {len(paths)}개 파일")


if __name__ == "__main__":
    main()
