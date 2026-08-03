"""
평가용 픽스처의 경로와 키 규칙. snapshot.py(생성)와 러너(재생)가 함께 쓴다.

news_key() -> fetch_news 인자로 뉴스 픽스처 키를 만드는 함수
corp_dir() / doc_path() -> 회사별 픽스처 경로를 만드는 함수
load_json() / save_json() -> UTF-8 JSON 입출력 함수
"""

import json
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVAL_DIR / "dataset.json"
FIXTURES_DIR = EVAL_DIR / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"


def corp_dir(corp_name: str) -> Path:
    return FIXTURES_DIR / corp_name


def doc_path(corp_name: str, rcept_no: str) -> Path:
    """공시 원문 zip 경로. 원문은 불변이라 raw 그대로 보관한다."""
    return corp_dir(corp_name) / "docs" / f"{rcept_no}.zip"


def news_key(corp_name: str, query: str, display: int, sort: str) -> str:
    """
    fetch_news 호출 인자를 그대로 키로 만든다.
    키워드 추출이 바뀌면 키가 달라져 픽스처 미스가 나는데, 이는 의도된 동작이다.
    (수집 조건이 달라졌다는 뜻이라 조용히 다른 데이터로 비교하면 안 된다)
    """
    return f"{corp_name}|{query}|{display}|{sort}"


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
