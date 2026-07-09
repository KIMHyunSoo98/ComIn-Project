"""
기업명으로 DART 공시 데이터와 네이버 뉴스 데이터를 수집하는 파이썬 파일.
현재는 최신 사업보고서 1개, 정기보고서 1개, 주요사항보고서(존재하면) 1개, 총 3개와 네이버 최신 기사 10개를 수집한다.

fetch_disclosures() -> 공시보고서 3개 가져오는 함수
fetch_news() -> 최신 뉴스 10개를 가져오는 함수
clean_text() -> 뉴스 기사의 텍스트에 있는 태그를 제거하는 함수
research() -> 회사명을 입력받아 고유 코드로 변환하고, 공시보고서와 기사를 수집하는 함수
"""

import re
import html
import json
from datetime import datetime, timedelta

from data.config import (
    DART_API_KEY,
    NAVER_CLIENT_ID,
    NAVER_CLIENT_SECRET,
    session,
    check_keys
)

from data.corp_code import find_corp_code
from data.dart_origin_document import get_disclosure_text
from data.chunking import chunk_text


DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
NAVER_NEWS_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"


# 공시정보 가지고 오는 함수 (최신 사업보고서 1개, 정기보고서 1개, 주요사항보고서 1개)
def fetch_disclosures(corp_code: str, days: int = 730, page_count: int = 10) -> list[dict]:
    """
    회사 고유 코드를 입력받아 공시 정보를 반환한다.
    현재는 최신 사업보고서 1개, 정기보고서 1개, 주요사항보고서(존재하면) 1개를 반환한다.
    하지만 보고서에서 중요한 표에 관련된 내용을 제거하고 수집하기 때문에 후에 개선해야한다.
    """
    end = datetime.today()
    bgn = end - timedelta(days=days)
    results = []

    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bgn_de": bgn.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "last_reprt_at": "Y", # 최종보고서 검색여부
        "page_count": page_count, # 최대 100
    }


    # 정기공시 정보 가져오기 (최신 사업보고서 1개, 정기보고서 1개)
    params["pblntf_ty"] = "A"

    response = session.get(DART_LIST_URL, params=params, timeout=30)
    response.raise_for_status()
    data_A = response.json()
    
    status  = data_A.get("status")
    if status == "013": # 조회된 데이터가 없는 경우
        pass
    elif status != "000": # 정상이 아닌 경우
        raise RuntimeError(f"DART 오류 status={status}, message={data_A.get('message')}")
    else:
        flag1 = flag2 = False # flag1 = 분기 or 반기 보고서, flag2 = 사업보고서
        for item in data_A.get("list", []):
            report_name = item.get("report_nm")
            if (not flag2) and "사업" in report_name:
                results.append(item)
                flag2 = True
            elif (not flag1) and (("분기" in report_name) or ("반기" in report_name)):
                results.append(item)
                flag1 = True
            
            if flag1 and flag2:
                break      


    # 최신 주요사항보고서 정보 가져오기 (주요사항보고서 1개)
    params["pblntf_ty"] = "B"

    response = session.get(DART_LIST_URL, params=params, timeout=30)
    response.raise_for_status()
    data_B = response.json()
    
    status  = data_B.get("status")
    if status == "013": # 조회된 데이터가 없는 경우
        pass
    elif status != "000": # 정상이 아닌 경우
        raise RuntimeError(f"DART 오류 status={status}, message={data_B.get('message')}")
    else:
        results.append(data_B.get("list", [])[0])

    
    return [
        {
            "report_nm": item.get("report_nm"),
            "rcept_no": item.get("rcept_no"),
            "rcept_dt": item.get("rcept_dt"),
            "flr_nm": item.get("flr_nm")
        }
        for item in results
    ]


# 네이버 응답에 존재하는 태그와 HTML 엔티티 제거 함수
def clean_text(text: str) -> str:
    """
    네이버 뉴스 응답에 존재하는 태그를 제거하고, HTML 엔티티로 변환된 문자열을 원래의 특수문자로 되돌린다.
    """
    text = re.sub(r"</?b>", "", text)
    return html.unescape(text) # &lt나 &amp처럼 HTML 엔티티로 변환된 문자열을 < 및 & 같은 원래의 특수문자로 되돌림


# 네이버 뉴스 가지고 오는 함수
def fetch_news(corp_name: str, display: int = 10, sort: str = "date") -> list[dict]:
    """
    회사명을 받아 최신 뉴스 10개를 가져온다.
    현재는 쿼리를 단순히 회사명으로 사용하고 있어서, 나중에 쿼리를 좀 더 보완해야한다.
    그리고 API를 통한 응답에는 뉴스의 본문 내용이 전부 나와있지 않고 요약본만 있다. 
    후에 본문 내용이 필요하면 크롤링을 해서 데이터를 수집해야 한다.
    """
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

    # 공시정보나 뉴스 없으면 에러 발생
    if not disclosures:
        raise ValueError(f"{corp_name}의 공시 정보가 없습니다.")
    if not news:
        raise ValueError(f"{corp_name}의 뉴스 기사가 없습니다.")
    

    return {
        "corp_name": corp_name,
        "corp_code": corp["corp_code"],
        "stock_code": corp["stock_code"],
        "disclosures": disclosures,
        "news": news
    }


# if __name__ == "__main__":
#     result = research("삼성전자")
#     print(json.dumps(result, ensure_ascii=False, indent=2))
