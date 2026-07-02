"""
API 키 로드, HTTP 캐시 세션 설정


"""

import os
from datetime import timedelta

from dotenv import load_dotenv
from requests_cache import CachedSession


load_dotenv() # 환경변수 파일 불러오기

# 필요한 API 키들 가져오기
DART_API_KEY = os.getenv("DART_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# 캐시 파일을 둘 폴더 만들기
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# 공시는 잘 안바뀌고 뉴스도 많이 바뀌진 않지만 너무 텀을 길게 했다가 어제 나온 정보를 못보여주면 안되니 일단 12시간 캐싱
# 처음 검색할 땐 직접 페이지에 접근해서 값을 가지고 오지만 그 후 expire_after에 설정된 시간동안은 같은 요청이 들어오면 로컬에서 돌려준다.
session = CachedSession(
    cache_name=os.path.join(CACHE_DIR, "http_cache"), # 캐시 데이터가 저장될 위치
    backend="sqlite", # 캐시를 저장할 엔진. 기본값 sqlite
    expire_after=timedelta(hours=12), # 캐시를 유지할 시간
    allowable_codes=[200] # 캐시에 저장할 코드 목록 / 200 - 성공 / 성공 응답만 캐시에 저장
)

def check_keys():
    """
    키가 전부 제대로 들어가 있는지 확인하고, 키가 하나라도 누락됐다면 누락된 키의 이름과 함께 오류를 띄우는 함수.
    """

    missing_keys = [
        name
        for name, key in [
            ("DART_API_KEY", DART_API_KEY),
            ("NAVER_CLIENT_ID", NAVER_CLIENT_ID),
            ("NAVER_CLIENT_SECRET", NAVER_CLIENT_SECRET)
        ]
        if not key
    ]

    if missing_keys:
        raise RuntimeError(
            f"키 누락: {', '.join(missing_keys)}."
        )
