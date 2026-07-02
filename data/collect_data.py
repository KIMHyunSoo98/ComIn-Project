"""
기업명 하나로 DART 공시 데이터와 네이버 뉴스 데이터를 수집하는 파이썬 파일.
"""

import re
import html
import json
from datetime import datetime, timedelta

from config import (
    DART_API_KEY,
    NAVER_CLIENT_ID,
    NAVER_CLIENT_SECRET,
    session,
    check_keys
)

from corp_code import find_corp_code

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
NAVER_NEWS_URL = "https://naverapihub.apigw.ntruss.com"


# 공시정보 가지고 오는 함수
def fetch_disclosures(corp_code: str, days: int = 90, page_count: int = 10) -> list[dict]:
    pass

# 네이버 뉴스 가지고 오는 함수
def fetch_news(corp_name: str, display: int = 10, sort: str = "date") -> list[dict]:
    pass

# 공시정보와 네이버 뉴스 합쳐서 반환하는 함수
def research(corp_name: str) -> dict:
    """
    회사명을 입력으로 받아 해당 회사의 공시 정보, 뉴스를 반환한다.
    """
    check_keys()

    corp = find_corp_code(corp_name)
    if corp is None: # 결과값이 없을 때
        raise ValueError(
            f"{corp_name}에 해당하는 고유번호가 없습니다. "
            f"기업명이 정확한지 확인 해주세요."
        )
    
    # 공시정보와 뉴스 가져오기
    disclosures = fetch_disclosures(corp["corp_code"])
    news = fetch_news(corp[corp_name])

    return {
        "corp_name": corp_name,
        "corp_code": corp["corp_code"],
        "stock_code": corp["stock_code"],
        "disclosures": disclosures,
        "news": news
    }


if __name__ == "__main__":
    pass
