"""
검색된 공시 청크와 뉴스를 컨텍스트로 LLM에게 리서치 리포트를 생성시키는 파일.
프로젝트의 유일한 유료 API가 쓰이는 곳이다. 예산 방어를 위해 실행당 1회만 호출한다.

build_context() -> 검색 결과와 뉴스를 프롬프트용 문자열로 조립하는 함수 (LLM 호출 없음)
generate_report() -> 컨텍스트를 받아 LLM으로 리포트를 생성하는 함수 (유료 API 1회)
"""

import os

import anthropic
from dotenv import load_dotenv


load_dotenv()

REPORT_MODEL = "claude-sonnet-5"  # 기본값

_client = None

# 실행당 유료 호출 횟수를 세어 1회 초과를 막는다.
_call_count = 0


def _get_client():
    """
    Anthropic 클라이언트를 한 번만 만들어 재사용한다.
    """
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY가 없습니다. .env를 확인하세요.")
        _client = anthropic.Anthropic()
    return _client


def build_context(kept_chunks, news: list[dict]) -> str:
    """
    filter_by_relevance()를 거친 공시 청크와 research()의 뉴스를 프롬프트용 문자열로 조립한다.
    공시 발췌와 뉴스를 섹션으로 구분해, LLM이 '공식 공시'와 '언론 보도'를 구분하게 한다.
    LLM 호출은 없다.
    """
    # [공시 발췌] 섹션 조립 (청크 + 유사도)
    disclosure_lines = ["[공시 발췌]"]
    if not kept_chunks:
        disclosure_lines.append("\n(질문과 관련된 공시 발췌 없음)")
    for i, item in enumerate(kept_chunks, start=1):
        rcept_no = item["metadata"].get("rcept_no")
        sim = item["similarity"]
        disclosure_lines.append(f"\n발췌 {i} (공시번호: {rcept_no}, 유사도: {sim:.3f}):\n{item['document']}")

    # [최근 뉴스] 섹션 조립 (제목 / 요약 / 링크 / 날짜)
    news_lines = ["[최근 뉴스]"]
    if not news:
        disclosure_lines.append("\n(질문과 관련된 최근 관련 뉴스 없음)")
    for i, item in enumerate(news, start=1):
        news_lines.append(
            f"\n뉴스 {i}:\n"
            f"제목: {item.get('title')}\n"
            f"요약: {item.get('description')}\n"
            f"링크: {item.get('link')}\n"
            f"날짜: {item.get('pubDate')}\n"
        )
    
    context = "\n".join(disclosure_lines) + "\n\n" + "\n".join(news_lines)
    return context


def generate_report(corp_name: str, question: str, context: str) -> str:
    """
    회사명, 질문, 조립된 컨텍스트를 받아 LLM으로 리포트를 생성한다.
    유료 API를 실행당 1회만 호출한다.
    """

    global _call_count
    if _call_count >= 1:
        raise RuntimeError("유료 API는 실행당 1회만 호출합니다.")
    _call_count += 1

    client = _get_client()

    system = (
        "너는 기업 리서치 애널리스트다. "
        "아래에 주어진 공시 발췌와 뉴스만을 근거로 리포트를 작성하고, 자료에 없는 내용은 추측하지 마라.\n"
        "- 공시 발췌는 회사가 공식 제출한 자료이고, 뉴스는 참고용 언론 보도다.\n"
        "- 근거가 되는 발췌/뉴스 번호를 문장 끝에 표기해라. (예: (발췌 2), (뉴스 3))\n"
        "- 질문에 대한 답을 먼저 제시하고, 그 뒤에 근거를 설명해라.\n"
        "- 자료가 부족해 답할 수 없으면 '주어진 자료로는 확인할 수 없음'이라고 명시해라."
    )

    user_content = (
        f"회사명: {corp_name}\n"
        f"질문: {question}\n\n"
        f"{context}"
    )

    response = client.messages.create(
        model=REPORT_MODEL,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )

    # 실제 토큰 사용량 확인
    # print(f"[usage] input={response.usage.input_tokens}, output={response.usage.output_tokens}")

    # 응답은 블록 리스트. text 블록만 꺼낸다.
    return next((b.text for b in response.content if b.type == "text"), "")
