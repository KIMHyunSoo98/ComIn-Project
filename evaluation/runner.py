"""
평가 러너. 픽스처를 재생해 데이터셋을 실행하고 지표를 계산해 결과 파일로 남긴다.

한 번 실행 = 한 패스 = 결과 파일 하나. 노이즈 폭을 보려면 같은 설정으로 여러 번 돌린다.
결과에는 지표뿐 아니라 리포트 전문과 컨텍스트를 함께 저장한다.
지표 정의가 바뀌어도 유료 재실행 없이 소급 계산할 수 있어야 하기 때문이다.

실행:
  uv run python -m evaluation.runner --dry-run              # 무과금 (스텁 리포트)
  uv run python -m evaluation.runner --label baseline-v1 --confirm   # 유료

run_item() -> 문항 하나를 실행해 상태와 실행 정보를 돌려주는 함수
run_dataset() -> 데이터셋 전체를 실행하고 결과 dict를 만드는 함수
"""

import argparse
import subprocess
import time
import uuid
from datetime import datetime

from langgraph.checkpoint.memory import MemorySaver

from langchain_rag import chain as report_chain
from langchain_rag.vectorstore import RELEVANCE_THRESHOLD, search_disclosure
from langgraph_rag.graph import build_graph
from langgraph_rag.state import initial_state
from observability.tracing import setup_tracing, trace_config
from evaluation import replay
from evaluation.fixture_store import DATASET_PATH, EVAL_DIR, MANIFEST_PATH, load_json, save_json
from evaluation.metrics import aggregate, evaluate_item

RESULTS_DIR = EVAL_DIR / "results"


def git_commit() -> dict:
    """결과가 어느 코드에서 나왔는지 못 박는다. 커밋되지 않은 변경이 있으면 dirty로 표시한다."""
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
        changed = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        files = [line[3:] for line in changed.splitlines()] if changed else []
        return {"commit": sha, "dirty": bool(files), "dirty_files": files}
    except Exception:  # noqa: BLE001
        return {"commit": None, "dirty": None, "dirty_files": []}


def prompt_fingerprint() -> str | None:
    """프롬프트만 바꾼 실험을 구분하기 위한 해시."""
    import hashlib

    try:
        texts = [
            report_chain.get_prompt().messages[0].prompt.template,
            report_chain.get_followup_prompt().messages[0].prompt.template,
        ]
        return hashlib.sha256("".join(texts).encode()).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return None


def run_config() -> dict:
    """실험 간 비교 시 '무엇이 달랐나'의 답이 되는 설정값들."""
    from data.collect_data import NEWS_DISPLAY, NEWS_FILTER_DAYS, NEWS_FILTER_NUM

    return {
        "relevance_threshold": RELEVANCE_THRESHOLD,
        "search_k": 3,
        "chunk_size": 500,
        "chunk_overlap": 50,
        "model": report_chain.REPORT_MODEL,
        "max_tokens": report_chain.MAX_TOKENS,
        "news_display": NEWS_DISPLAY,
        "news_filter_days": NEWS_FILTER_DAYS,
        "news_filter_num": NEWS_FILTER_NUM,
        "prompt_sha": prompt_fingerprint(),
    }


def warm_up() -> float:
    """
    임베딩 모델(KURE) 로딩이 첫 문항의 지연 시간에 섞이지 않도록 미리 한 번 검색한다.
    무과금이고 몇 초 걸린다.
    """
    started = time.perf_counter()
    search_disclosure(replay.get_eval_vectorstore(), "워밍업", k=1)
    return round(time.perf_counter() - started, 2)


def run_item(graph, corp_name: str, question: str, config: dict, followup: bool = False) -> dict:
    """
    문항 하나를 실행한다. 실행된 노드와 소요 시간을 함께 기록한다.
    후속 턴은 새 질문과 리셋 필드만 넣어 checkpointer가 나머지 상태를 복원하게 한다.
    """
    graph_input = (
        {"question": question, "retrieve_attempts": 0, "paid_call_count": 0}
        if followup
        else initial_state(corp_name, question)
    )

    nodes_run = []
    started = time.perf_counter()
    for chunk in graph.stream(graph_input, config=config, stream_mode="updates"):
        nodes_run.extend(chunk.keys())
    elapsed = time.perf_counter() - started

    return {
        "state": graph.get_state(config).values,
        "nodes_run": nodes_run,
        "elapsed_sec": round(elapsed, 2),
    }


def _record(item: dict, run: dict, question: str) -> dict:
    """지표에 원자료(질문·리포트·컨텍스트)를 붙여 결과 한 줄을 만든다."""
    state = run["state"]
    row = evaluate_item(item, state)
    row.update({
        "question": question,
        "elapsed_sec": run["elapsed_sec"],
        "nodes_run": run["nodes_run"],
        "report": state.get("report", ""),
        "context": state.get("context", ""),
    })
    return row


def run_dataset(label: str, dry_run: bool) -> dict:
    dataset = load_json(DATASET_PATH)
    manifest = load_json(MANIFEST_PATH)

    replay.install_replay()
    if dry_run:
        replay.install_stub_llm()

    setup_tracing()
    print(f"  워밍업 {warm_up()}s")

    graph = build_graph(checkpointer=MemorySaver())
    rows = []

    for item in dataset["items"]:
        config = {
            "configurable": {"thread_id": uuid.uuid4().hex},
            **trace_config(f"eval-{item['id']}", corp_name=item["corp_name"], label=label),
        }
        run = run_item(graph, item["corp_name"], item["question"], config)
        row = _record(item, run, item["question"])
        rows.append(row)
        mark = "리포트" if row["has_report"] else f"후보 {row['candidates']}개"
        print(f"  {item['id']:5} {item['corp_name']:6} {row['elapsed_sec']:6.2f}s  {mark}")

    for scenario in dataset["multiturn"]:
        thread_id = uuid.uuid4().hex
        for turn_no, question in enumerate(scenario["turns"], start=1):
            config = {
                "configurable": {"thread_id": thread_id},
                **trace_config(
                    f"eval-{scenario['id']}-t{turn_no}",
                    corp_name=scenario["corp_name"],
                    label=label,
                    is_followup=turn_no > 1,
                ),
            }
            run = run_item(
                graph, scenario["corp_name"], question, config, followup=turn_no > 1
            )
            turn_item = {
                "id": f"{scenario['id']}-t{turn_no}",
                "category": "followup",
                "corp_name": scenario["corp_name"],
                "expects_answer": True,
            }
            row = _record(turn_item, run, question)
            row["turn"] = turn_no
            rows.append(row)
            print(f"  {row['id']:8} {scenario['corp_name']:6} {row['elapsed_sec']:6.2f}s  "
                  f"노드 {len(row['nodes_run'])}개")

    return {
        "meta": {
            "label": label,
            "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "dry_run": dry_run,
            "dataset_version": dataset["_meta"]["version"],
            "fixture_as_of": manifest["as_of"],
            **git_commit(),
            "config": run_config(),
        },
        "summary": aggregate(rows),
        "items": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="ComIn 평가 러너")
    parser.add_argument("--label", default="unlabeled", help="실험 이름 (예: baseline-v1)")
    parser.add_argument("--dry-run", action="store_true", help="스텁 리포트로 무과금 실행")
    parser.add_argument("--confirm", action="store_true", help="유료 실행 승인")
    args = parser.parse_args()

    dataset = load_json(DATASET_PATH)
    paid = sum(1 for i in dataset["items"] if i["category"] != "resolve_fail")
    paid += sum(len(s["turns"]) for s in dataset["multiturn"])

    if not args.dry_run and not args.confirm:
        parser.error(f"유료 호출 {paid}회가 발생합니다. --confirm을 붙이거나 --dry-run을 쓰세요.")

    print(f"[{args.label}] {'무과금(스텁)' if args.dry_run else f'유료 {paid}회'} 실행")
    result = run_dataset(args.label, args.dry_run)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"{stamp}_{args.label}.json"
    save_json(path, result)

    overall = result["summary"]["overall"]
    print(f"\n[요약] {args.label}")
    for key, value in overall.items():
        print(f"  {key:24} {value}")
    print(f"\n결과: {path}")


if __name__ == "__main__":
    main()
