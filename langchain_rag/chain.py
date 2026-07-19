"""
LangChain 컴포넌트로 구현한 프롬프트 / LLM / 리포트 생성 체인.
vanilla의 vanilla_rag/generate.py를 대체한다.

프로젝트의 유일한 유료 API가 쓰이는 곳이다. 예산 방어를 위해 실행당 1회만 호출한다.

build_context() -> 검색된 청크와 뉴스를 프롬프트용 문자열로 조립하는 함수 (LLM 호출 없음)
get_llm() -> ChatAnthropic 인스턴스를 한 번만 만들어 재사용하는 함수
get_prompt() -> 리포트 생성용 ChatPromptTemplate을 만드는 함수
build_report_chain() -> context 조립부터 리포트 문자열까지 이어지는 LCEL 체인을 만드는 함수
generate_report() -> 체인을 실행해 리포트를 생성하는 함수 (유료 API 1회)
"""

import os
from langchain_core.documents import Document
from langchain_core.runnables import Runnable
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_anthropic import ChatAnthropic
from dotenv import load_dotenv


load_dotenv()

REPORT_MODEL = "claude-sonnet-5"  # 기본값
MAX_TOKENS = 4096

_llm = None

# 실행당 유료 호출 횟수를 세어 1회 초과를 막는다.
_call_count = 0


def build_context(kept_chunks: list[tuple[Document, float]], news: list[dict]) -> str:
    """
    filter_disclosure_by_relevance()를 거친 공시 청크와 research()의 뉴스를 프롬프트용 문자열로 조립한다.
    공시 발췌와 뉴스를 섹션으로 구분해, LLM이 '공식 공시'와 '언론 보도'를 구분하게 한다.
    LLM 호출은 없다. 뉴스는 벡터 검색 대상이 아니므로 Document로 만들지 않고 dict 그대로 받는다.
    """
    # [공시 발췌] 섹션 조립 (청크 + 유사도)
    disclosure_lines = ["[공시 발췌]"]
    if not kept_chunks:
        disclosure_lines.append("\n(질문과 관련된 공시 발췌 없음)")
    for i, (doc, sim) in enumerate(kept_chunks, start=1):
        rcept_no = doc.metadata.get("rcept_no")
        disclosure_lines.append(f"\n발췌 {i} (공시번호: {rcept_no}, 유사도: {sim:.3f}):\n{doc.page_content}")

    # [최근 뉴스] 섹션 조립 (제목 / 요약 / 링크 / 날짜)
    news_lines = ["[최근 뉴스]"]
    if not news:
        news_lines.append("\n(질문과 관련된 최근 관련 뉴스 없음)")
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



def get_llm() -> Runnable:
    """
    ChatAnthropic 인스턴스를 한 번만 만들어 재사용한다.
    ANTHROPIC_API_KEY가 없으면 예외를 발생시킨다.
    """
    global _llm
    
    if _llm is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY가 없습니다. .env를 확인하세요")
        _llm = ChatAnthropic(model_name=REPORT_MODEL, max_tokens=MAX_TOKENS)
    return _llm


def get_prompt() -> ChatPromptTemplate:
    """
    리포트 생성용 ChatPromptTemplate을 만든다.
    system 메시지에는 애널리스트 역할과 근거 표기 규칙을,
    human 메시지에는 회사명 / 질문 / 컨텍스트를 변수로 넣는다.
    """
    system = (
        "너는 기업 리서치 애널리스트다. "
        "아래에 주어진 공시 발췌와 뉴스만을 근거로 리포트를 작성하고, 자료에 없는 내용은 추측하지 마라.\n"
        "- 공시 발췌는 회사가 공식 제출한 자료이고, 뉴스는 참고용 언론 보도다.\n"
        "- 근거가 되는 발췌/뉴스 번호를 문장 끝에 표기해라. (예: (발췌 2), (뉴스 3))\n"
        "- 질문에 대한 답을 먼저 제시하고, 그 뒤에 근거를 설명해라.\n"
        "- 자료가 부족해 답할 수 없으면 '주어진 자료로는 확인할 수 없음'이라고 명시해라."
    )
    human = (
        "회사명: {corp_name}\n"
        "질문: {question}\n\n"
        "{context}"
    )

    return ChatPromptTemplate.from_messages([("system", system), ("human", human)])


def build_report_chain() -> Runnable:
    """
    prompt | llm | StrOutputParser()로 이어지는 LCEL 체인을 만든다.
    체인의 입력은 {corp_name, question, context} 딕셔너리, 출력은 리포트 문자열이다.
    """
    return get_prompt() | get_llm() | StrOutputParser()



def generate_report(corp_name: str, question: str, context: str) -> str:
    """
    회사명, 질문, 조립된 컨텍스트를 받아 체인을 실행해 리포트를 생성한다.
    유료 API를 실행당 1회만 호출한다. (_call_count로 가드)
    """
    global _call_count

    if _call_count >= 1:
        raise RuntimeError("유료 API는 실행당 1회만 호출합니다.")
    _call_count += 1

    chain = build_report_chain()
    return chain.invoke({"corp_name": corp_name, "question": question, "context": context})
