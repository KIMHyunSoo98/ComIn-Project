"""
질문에서 검색용 핵심 키워드를 뽑는 룰 기반 추출기.

형태소 분석기 없이 불용어 제거 + 조사 어미 제거 수준으로 시작한다.
유료 호출이 없어야 하는 구간이라 룰 기반이 기본이고,
한계가 확인되면 로컬 모델(형태소 분석기 등)을 사용한다.

extract_keywords() -> 질문에서 핵심 키워드를 최대 N개 뽑는 함수
"""

import re


# 질문에서 정보가 없는 말들: 시간 표현, 의문/요청 표현, 연결 표현
STOPWORDS = {
    "최근", "요즘", "지금", "현재", "올해", "작년", "내년", "앞으로",
    "어때", "어떄", "어떤", "어떻게", "어떠한", "무엇", "뭐야", "뭔가", "왜",
    "궁금해", "궁금합니다", "알려줘", "알려주세요", "말해줘", "설명해줘", "해줘",
    "있어", "있나", "있는지", "인가", "인지",
    "대해", "대한", "대해서", "관련", "관련된", "관해", "관한",
    "좀", "그리고", "그래서", "또", "및",
}

# 토큰 끝에 붙는 조사. 긴 것부터 검사한다.
JOSA = (
    "에서는", "에서", "에게", "한테", "으로", "까지", "부터", "보다", "처럼", "마다",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만", "나", "요",
)


def _strip_josa(token: str) -> str:
    """
    토큰 끝의 조사를 한 번 떼어낸다. 떼고 나서 2글자 미만이 되면 떼지 않는다.
    """
    for josa in JOSA:
        if token.endswith(josa) and len(token) - len(josa) >= 2:
            return token[: -len(josa)]
    return token


def extract_keywords(question: str, corp_name: str = "", max_keywords: int = 3) -> list[str]:
    """
    질문에서 핵심 키워드를 최대 max_keywords개 뽑는다.
    회사명 토큰은 검색 쿼리에 따로 들어가므로 제외한다.
    뽑을 것이 없으면 빈 리스트를 반환한다.
    """
    text = re.sub(r"[^\w\s]", " ", question)  # 물음표 등 문장부호 제거
    keywords = []

    for token in text.split():
        if token == corp_name or token in STOPWORDS:
            continue
        token = _strip_josa(token)
        if token == corp_name:  # "삼성전자는"처럼 조사가 붙은 회사명도 제외
            continue
        if len(token) >= 2 and token not in STOPWORDS and token not in keywords:
            keywords.append(token)
        if len(keywords) == max_keywords:
            break

    return keywords
