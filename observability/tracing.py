"""
LangSmith 추적 설정.
LangChain/LangGraph는 환경변수로 추적 여부를 판단하므로, 진입점에서 setup_tracing()만 호출하면 된다.

setup_tracing() -> .env 값을 확인해 LangSmith 추적을 켜는 함수
trace_config() -> 런 이름/메타데이터를 담은 RunnableConfig를 만드는 함수
"""

import os

from dotenv import load_dotenv


load_dotenv()

DEFAULT_PROJECT = "comin"

_TRUE_VALUES = ("true", "1", "yes")


def setup_tracing(project: str | None = None) -> bool:
    """
    LangSmith 추적을 켜고 활성 여부를 반환한다.

    LANGSMITH_TRACING이 false이거나 LANGSMITH_API_KEY가 없으면 추적만 끄고 그대로 진행한다.
    """
    if os.getenv("LANGSMITH_TRACING", "true").lower() not in _TRUE_VALUES:
        os.environ["LANGSMITH_TRACING"] = "false"
        return False

    if not os.getenv("LANGSMITH_API_KEY"):
        os.environ["LANGSMITH_TRACING"] = "false"
        print("[LangSmith] LANGSMITH_API_KEY가 없어 추적을 끕니다. (.env 확인)")
        return False

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_PROJECT"] = project or os.getenv("LANGSMITH_PROJECT") or DEFAULT_PROJECT
    return True


def trace_config(run_name: str, **metadata) -> dict:
    """
    LangSmith에서 런을 구분할 이름과 메타데이터를 만든다.
    추적이 꺼져 있으면 LangChain이 무시하므로 항상 붙여도 안전하다.
    """
    return {
        "run_name": run_name,
        "metadata": {key: value for key, value in metadata.items() if value is not None},
    }
