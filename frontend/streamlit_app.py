"""
ComIn 기업 리서치 MVP — FastAPI(api.main:app)를 호출하는 Streamlit 프론트.

실행:
  1) uv run uvicorn api.main:app --reload        # 백엔드 먼저
  2) uv run streamlit run frontend/streamlit_app.py

Streamlit은 파이썬 서버에서 FastAPI를 requests로 호출하므로 브라우저 CORS와 무관하다.
'조사 시작' 1회 = 그래프 invoke 1회 = 유료 호출 ≤1회.
"""

import time

import requests
import streamlit as st


DEFAULT_API_URL = "http://127.0.0.1:8000"
TIMEOUT = 300  # 첫 요청은 KURE 임베딩 로드 + LLM 생성으로 오래 걸릴 수 있다


def api_url(path: str) -> str:
    return f"{st.session_state.api_url.rstrip('/')}{path}"


def call_research(corp_name: str, question: str):
    """POST /research 한 번. (status, data, elapsed, error)를 반환한다."""
    try:
        started = time.perf_counter()
        resp = requests.post(
            api_url("/research"),
            json={"corp_name": corp_name, "question": question},
            timeout=TIMEOUT,
        )
        elapsed = time.perf_counter() - started
    except requests.RequestException as exc:
        return None, None, None, f"API 연결 실패: {exc}\n\n백엔드를 먼저 실행하세요: uv run uvicorn api.main:app --reload"

    if resp.status_code == 422:
        return None, None, elapsed, resp.json().get("detail", "리서치를 생성할 수 없습니다.")
    if not resp.ok:
        return None, None, elapsed, f"오류 {resp.status_code}: {resp.text}"

    data = resp.json()
    return data["status"], data, elapsed, None


def run_and_store(corp_name: str, question: str) -> None:
    with st.spinner(f"'{corp_name}' 조사 중… (임베딩·LLM으로 시간이 걸립니다)"):
        status, data, elapsed, error = call_research(corp_name, question)

    ss = st.session_state
    ss.last_elapsed = elapsed
    ss.last_question = question
    ss.last_error = error
    if error:
        ss.last_status = None
    elif status == "ok":
        ss.last_status = "ok"
        ss.last_record = data["result"]
        ss.candidates = []
    else:
        ss.last_status = "candidates"
        ss.candidates = data.get("candidates", [])
        ss.last_record = None


def render_report(record: dict, elapsed: float | None) -> None:
    left, right = st.columns([3, 1])
    with left:
        st.success(f"{record['corp_name']}  ·  코드 {record['corp_code']}  ·  뉴스모드 {record['news_mode']}")
    with right:
        if elapsed is not None:
            st.metric("응답 시간", f"{elapsed:.1f}s")
    st.markdown(record["report"])


def render_candidates() -> None:
    st.warning("회사명을 특정하지 못했어요. 아래 후보 중에서 고르면 그 회사로 다시 조사합니다.")
    for cand in st.session_state.candidates:
        if st.button(cand, key=f"cand-{cand}", use_container_width=True):
            run_and_store(cand, st.session_state.last_question)
            st.rerun()


def tab_research() -> None:
    st.caption("회사명과 질문을 입력하면 공시·뉴스 기반 리포트를 생성합니다. 조사 1회 = 유료 API 1회 호출.")

    with st.form("research_form"):
        corp_name = st.text_input("회사명", placeholder="예: 삼성전자")
        question = st.text_area("질문", placeholder="예: 주요 사업과 최근 이슈는?", height=80)
        submitted = st.form_submit_button("조사 시작")

    if submitted:
        if not corp_name.strip() or not question.strip():
            st.warning("회사명과 질문을 모두 입력하세요.")
        else:
            run_and_store(corp_name.strip(), question.strip())

    ss = st.session_state
    if ss.last_error:
        st.error(ss.last_error)
    elif ss.last_status == "ok":
        render_report(ss.last_record, ss.last_elapsed)
    elif ss.last_status == "candidates":
        render_candidates()


def tab_history() -> None:
    if st.button("새로고침"):
        st.rerun()

    try:
        resp = requests.get(api_url("/research"), timeout=TIMEOUT)
    except requests.RequestException as exc:
        st.error(f"API 연결 실패: {exc}")
        return

    if not resp.ok:
        st.error(f"이력을 불러오지 못했습니다: {resp.status_code}")
        return

    records = resp.json()
    if not records:
        st.info("아직 조사 이력이 없습니다.")
        return

    st.caption(f"총 {len(records)}건 (최신순)")
    for r in records:
        with st.expander(f"#{r['id']}  ·  {r['corp_name']} — {r['question']}"):
            st.caption(f"코드 {r['corp_code']}  ·  뉴스모드 {r['news_mode']}  ·  {r['created_at']}")
            st.markdown(r["report"])


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("api_url", DEFAULT_API_URL)
    ss.setdefault("last_status", None)
    ss.setdefault("last_record", None)
    ss.setdefault("candidates", [])
    ss.setdefault("last_error", None)
    ss.setdefault("last_elapsed", None)
    ss.setdefault("last_question", "")


def main() -> None:
    st.set_page_config(page_title="ComIn 기업 리서치", page_icon="🔎", layout="centered")
    init_state()

    st.title("🔎 ComIn 기업 리서치")

    with st.sidebar:
        st.header("설정")
        st.session_state.api_url = st.text_input("API 주소", value=st.session_state.api_url)
        st.markdown(f"[API 문서 열기]({st.session_state.api_url.rstrip('/')}/docs)")
        st.caption("⚠️ '조사 시작' 1회 = 유료 API 1회 호출")

    research, history = st.tabs(["조사하기", "조사 이력"])
    with research:
        tab_research()
    with history:
        tab_history()


if __name__ == "__main__":
    main()
