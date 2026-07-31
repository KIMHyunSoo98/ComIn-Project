"""
API 키 로드, HTTP 세션 설정

check_keys() -> API 키들이 제대로 들어가 있는지 확인하는 함수
"""

import os

import requests
from dotenv import load_dotenv


load_dotenv() # 환경변수 파일 불러오기

# 필요한 API 키들 가져오기
DART_API_KEY = os.getenv("DART_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# corp_code.db를 둘 폴더 만들기
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# 방금 올라온 공시·뉴스가 누락되면 안 되므로 응답은 캐싱하지 않는다.
session = requests.Session()

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
