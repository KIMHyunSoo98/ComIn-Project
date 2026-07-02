"""
DART API에 특정 기업에 대한 요청을 보내기 위해서는 해당 기업의 고유번호가 필요하다.
요청을 할때마다 고유번호를 매핑하면 비효율적이니,
각 회사별 고유번호가 담겨있는 zip파일을 DART API를 통해 가져와 매핑된 테이블을 만든다.
테이블은 로컬 sqlite에 저장한다.

build_corp_code_db() -> 회사명과 고유번호 매핑 테이블 만드는 함수
find_corp_code() -> 기업명으로 고유번호 조회하는 함수
"""

import io
import zipfile
import sqlite3
import xml.etree.ElementTree as ET

import requests

import os
from config import (
    DART_API_KEY,
    CACHE_DIR,
    check_keys
)

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml" # 요청 url
DB_PATH = os.path.join(CACHE_DIR, "corp_code.db") # 매핑된 테이블을 저장할 위치


# 회사명과 고유번호 매핑 테이블 만드는 함수
def build_corp_code_db():
    """
    전체 고유번호가 들어있는 zip 파일을 다운받아 파싱 후 sqlite에 저장하는 함수
    """
    check_keys()
    
    # 1. 전체 고유번호가 담긴 zip 다운로드
    response = requests.get(CORP_CODE_URL, params={"crtfc_key":DART_API_KEY}, timeout=30)
    response.raise_for_status()
    
    # 키가 잘못 입력되면 zip이 아닌 에러 XML을 받아온다. zip의 시그니처인 PK 확인.
    if response.content[:2] != b"PK":
        raise RuntimeError(
            "zip이 아닌 응답을 받았습니다. DART 키가 유효한지 확인하세요."
        )
    
    # 2. zip 안의 CORPCODE.xml 파싱
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        xml_name = zf.namelist()[0]
        with zf.open(xml_name) as f:
            print("f", f)
            tree = ET.parse(f)
    root = tree.getroot()

    rows = []
    for item in root.iter("list"): # 노드들중 "list" 태그를 가진 노드만 사용
      # 고유번호, 정식명칭, 종목코드, 최종변경일자 파싱 후 추가
      rows.append((
          (item.findtext("corp_code") or "").strip(),
          (item.findtext("corp_name") or "").strip(),
          (item.findtext("stock_code") or "").strip(),
          (item.findtext("modify_date") or "").strip(),
      ))  

    # 3. sqlite에 저장
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS corp") # 이미 corp 테이블이 존재하면 테이블 드랍
    cur.execute( # 테이블 생성
        """
        CREATE TABLE corp (
            corp_code TEXT,
            corp_name TEXT,
            stock_code TEXT,
            modify_date TEXT
        )
        """
    )
    cur.executemany("INSERT INTO corp VALUES (?, ?, ?, ?)", rows)
    cur.execute("CREATE INDEX idx_cor_name ON corp(corp_name)") # 회사명으로 검색했을 때 속도를 빠르게 하기 위해 인덱스로 설정
    conn.commit()
    conn.close()
    print(f"corp 테이블 구축 완료: 총 {len(rows):,}개 회사")

# 회사명을 받아 고유코드, 정식회사명칭, 종목코드를 반환하는 함수
def find_corp_code(corp_name: str) -> dict:
    """
    회사명으로 corp_code를 조회한다.
    같은 이름이 여러 개 존재할 수 있는데, 이 때는 상장사를 우선 반환한다.
    (stock_code가 있는 회사)
    """
    if not os.path.exists(DB_PATH):
        raise RuntimeError(
            f"{DB_PATH}가 없습니다. 먼저 매핑 테이블을 만들어주세요."
        )

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 회사명과 일치하는 결과가 있으면 가져오기
    cur.execute(
        "SELECT corp_code, corp_name, stock_code FROM corp WHERE corp_name = ?",
        (corp_name, )
    )
    matches = cur.fetchall()
    conn.close()

    # 결과값이 없을 때
    if not matches:
        return None

    listed = [match for match in matches if match[2]] # 종목코드가 있는 회사
    corp_code, name, stock_code = listed[0] if listed else matches[0] # 종목코드 있으면 상장사, 없으면 그냥 결과값에서 반환

    return {"corp_code":corp_code, "corp_name":name, "stock_code":stock_code}

if __name__ == "__main__":
    # build_corp_code_db()
    # 정상적으로 실행됐는지 확인
    print(find_corp_code("삼성전자"))
    