"""
LangChain 컴포넌트로 구현한 프롬프트 / LLM / 리포트 생성 체인.
vanilla의 vanilla_rag/generate.py를 대체한다.

프로젝트의 유일한 유료 API가 쓰이는 곳이다. 예산 방어를 위해 실행당 1회만 호출한다.

build_context() -> 검색된 청크와 뉴스를 프롬프트용 문자열로 조립하는 함수 (LLM 호출 없음)
render_sources() -> 리포트가 인용한 근거만 모아 출처 섹션을 만드는 함수 (LLM 호출 없음)
get_llm() -> ChatAnthropic 인스턴스를 한 번만 만들어 재사용하는 함수
get_prompt() -> 리포트 생성용 ChatPromptTemplate을 만드는 함수
build_report_chain() -> context 조립부터 리포트 문자열까지 이어지는 LCEL 체인을 만드는 함수
generate_report() -> 체인을 실행해 리포트를 생성하는 함수 (유료 API 1회)
"""

import os
import re
from langchain_core.documents import Document
from langchain_core.runnables import Runnable
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
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


# 리포트에 표기된 (발췌 n) / (뉴스 n)을 다시 읽어 출처로 실을 근거를 고른다.
# evaluation/metrics.py에도 같은 패턴이 있지만, 평가가 프로덕션 정의에 묶이면
# 프롬프트를 고칠 때 과거 지표까지 흔들리므로 일부러 분리해 둔다.
_CITATION_BLOCK = re.compile(r"\(([^()]*(?:발췌|뉴스)[^()]*)\)")
_CITATION_TOKEN = re.compile(r"(발췌|뉴스)|(\d+)")


def cited_numbers(report: str) -> tuple[set[int], set[int]]:
    """
    리포트가 인용한 (발췌 번호, 뉴스 번호)를 뽑는다.
    """
    excerpts, news = set(), set()
    for block in _CITATION_BLOCK.findall(report):
        label = None
        for match in _CITATION_TOKEN.finditer(block):
            if match.group(1):
                label = match.group(1)
            elif label:
                (excerpts if label == "발췌" else news).add(int(match.group(2)))
    return excerpts, news


def render_sources(
    report: str,
    kept_chunks: list[tuple[Document, float]],
    news: list[dict],
    disclosures: list[dict] | None = None,
) -> str:
    """
    리포트가 실제로 인용한 근거만 모아 출처 섹션을 만든다. LLM 호출은 없다.

    링크와 보고서명을 LLM이 받아쓰게 하면 환각 위험이 있어 원본 메타데이터로 직접 렌더링한다.
    build_context()가 매긴 번호를 그대로 역참조하므로 인용 번호와 출처가 어긋날 수 없다.
    인용이 하나도 없으면 빈 문자열을 돌려준다.
    """
    excerpt_nos, news_nos = cited_numbers(report)
    report_names = {d.get("rcept_no"): d.get("report_nm") for d in disclosures or []}

    lines = []
    seen_rcept = set()
    for n in sorted(excerpt_nos):
        if not 1 <= n <= len(kept_chunks):
            continue
        rcept_no = kept_chunks[n - 1][0].metadata.get("rcept_no")
        if rcept_no in seen_rcept:
            continue
        seen_rcept.add(rcept_no)
        lines.append(f"- 공시: {report_names.get(rcept_no) or '공시자료'} (접수번호 {rcept_no})")

    seen_link = set()
    for n in sorted(news_nos):
        if not 1 <= n <= len(news):
            continue
        item = news[n - 1]
        link = item.get("link")
        if link in seen_link:
            continue
        seen_link.add(link)
        lines.append(f"- 뉴스 {n}: {item.get('title')} - {link}")

    return "\n\n## 출처\n" + "\n".join(lines) if lines else ""


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
        "- 자료가 부족해 답할 수 없으면 '주어진 자료로는 확인할 수 없음'이라고 명시해라.\n"
        "\n"
        "리포트는 아래 세 섹션을 이 순서대로, 이 제목 그대로 쓴다.\n"
        "## 결론\n"
        "질문에 대한 결론을 첫 문장에 쓴다. 서론이나 배경 설명을 앞에 두지 마라.\n"
        "## 근거\n"
        "답을 뒷받침하는 자료를 설명한다.\n"
        "## 한계/유의점\n"
        "자료로 확인되지 않는 부분과 해석에 주의할 점을 쓴다. 없으면 '특이사항 없음'이라고만 쓴다.\n"
        "\n"
        "출처 목록은 쓰지 마라. 인용 표기를 읽어 시스템이 원본 링크와 보고서명을 붙인다."
    )
    human = (
        "회사명: {corp_name}\n"
        "질문: {question}\n\n"
        "{context}"
    )

    return ChatPromptTemplate.from_messages([("system", system), MessagesPlaceholder("history"), ("human", human)])

def get_followup_prompt() -> ChatPromptTemplate:
    """
    멀티턴 질문용 프롬프트.
    리포트 작성이 아니라 이전 대화와 자료를 근거로 사용자의 후속 질문에 답한다.
    """
    system = (
        "너는 기업 리서치 애널리스트다. 앞선 대화와 아래 자료(공시 발췌·뉴스)를 근거로 사용자의 후속 질문에 대화하듯 간결히 답해라.\n"
        "- 이전 답변의 맥락을 이어가되, 이번 질문에 초점을 맞춘다.\n"
        "- 근거로 쓴 발췌/뉴스는 (발췌 n)·(뉴스 n)으로 표기한다.\n"
        "- 자료에 없는 내용은 추측하지 말고 '주어진 자료로는 확인할 수 없음'이라고 밝힌다.\n"
        "- 마크다운 제목(##)이나 섹션 구분 없이, 3~6 문장 평문으로 답한다.\n"
        "- 앞선 답변이 '## 결론 / ## 근거 / ## 한계' 구조여도 그 형식을 따라 하지 마라. "
        "그건 첫 리포트의 형식이고, 후속 답변은 대화다.\n"
        "- 출처 목록은 쓰지 마라. 인용 표기를 읽어 시스템이 원본 링크와 보고서명을 붙인다."
    )
    human = (
        "회사명: {corp_name}\n"
        "질문: {question}\n\n"
        "{context}"
    )

    return ChatPromptTemplate.from_messages([("system", system), MessagesPlaceholder("history"), ("human", human)])

def build_report_chain() -> Runnable:
    """
    prompt | llm | StrOutputParser()로 이어지는 LCEL 체인을 만든다.
    체인의 입력은 {corp_name, question, context} 딕셔너리, 출력은 리포트 문자열이다.
    """
    return get_prompt() | get_llm() | StrOutputParser()

def build_followup_chain() -> Runnable:
    """
    후속(멀티턴) 질문용 체인. get_followup_prompt() | llm | StrOutputParser().
    """
    return get_followup_prompt() | get_llm() | StrOutputParser()

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
    return chain.invoke({"corp_name": corp_name, "question": question, "context": context, "history": []})
