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
NAVER_NEWS_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"


# 공시정보 가지고 오는 함수
def fetch_disclosures(corp_code: str, days: int = 90, page_count: int = 10) -> list[dict]:
    end = datetime.today()
    bgn = end - timedelta(days=days)

    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bgn_de": bgn.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "last_reprt_at": "Y",
        "page_count": page_count
    }
    
    response = session.get(DART_LIST_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    
    status  = data.get("status")
    if status == "013": # 조회된 데이터가 없는 경우
        return []
    if status != "000": # 정상이 아닌 경우
        raise RuntimeError(f"DART 오류 status={status}, message={data.get('message')}")
    
    return [
        {
            "report_nm": item.get("report_nm"),
            "rcept_no": item.get("rcept_no"),
            "rcept_dt": item.get("rcept_dt"),
            "flr_nm": item.get("flr_nm")
        }
        for item in data.get("list", [])
    ]

# 네이버 응답에 존재하는 태그와 HTML 엔티티 제거 함수
def clean_text(text: str) -> str:
    text = re.sub(r"</?b>", "", text)
    return html.unescape(text) # &lt;나 &amp;처럼 HTML 엔티티로 변환된 문자열을 < 및 & 같은 원래의 특수문자로 되돌림

# 네이버 뉴스 가지고 오는 함수
def fetch_news(corp_name: str, display: int = 10, sort: str = "date") -> list[dict]:
    
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {
        "query": corp_name,
        "display": display,
        "sort": sort
    }
    
    response = session.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    
    return [
        {
            "title": clean_text(item.get("title")),
            "description": clean_text(item.get("description")),
            "link": item.get("link"),
            "pubDate": item.get("pubDate")
        }
        for item in data.get("items", [])
    ]


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
    news = fetch_news(corp_name)

    return {
        "corp_name": corp_name,
        "corp_code": corp["corp_code"],
        "stock_code": corp["stock_code"],
        "disclosures": disclosures,
        "news": news
    }


if __name__ == "__main__":
    result = research("삼성전자")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n공시 {len(result['disclosures'])}건, 뉴스 {len(result['news'])}건 수집 완료.")
