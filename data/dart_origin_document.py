"""
DART 공시원문에서 본문 텍스트 추출.
해당 파일은 AI(클로드 Opus 4.8)로 작성한 코드.
"""

import io
import os
import re
import zipfile

from lxml import etree
import requests
from dotenv import load_dotenv

load_dotenv()
DART_API_KEY = os.getenv("DART_API_KEY")
DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"

# 유효한 XML 엔티티(&amp; &lt; &gt; &quot; &apos; &#123; &#x1F;)가 아닌
# raw '&'만 매칭한다.
_RAW_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9A-Fa-f]+);)")

# 태그의 시작이 아닌 raw '<'만 매칭한다.
# 진짜 태그는 '<' 다음에 문자([A-Za-z]) / 닫는태그(/) / 선언·처리명령(? !)이 오고,
# 텍스트로 쓰인 '<'(예: "< TV 시장점유율 추이 >")는 뒤에 공백·비문자가 온다.
_RAW_LT = re.compile(r"<(?![A-Za-z/?!])")

# XML 1.0에서 허용되지 않는 제어문자(탭·개행·복귀 제외). 원문에 섞여 있으면
# 파서가 "not well-formed (invalid token)"으로 죽으므로 제거한다.
_ILLEGAL_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def fetch_document_zip(rcept_no: str) -> bytes:
    """document.xml API로 공시원문 zip을 받는다."""
    if not DART_API_KEY:
        raise RuntimeError("DART_API_KEY가 없습니다. .env를 확인하세요.")
    resp = requests.get(
        DOCUMENT_URL,
        params={"crtfc_key": DART_API_KEY, "rcept_no": rcept_no},
        timeout=30,
    )
    resp.raise_for_status()
    if resp.content[:2] != b"PK":
        raise RuntimeError(
            "zip이 아닌 응답을 받았습니다. rcept_no/키 권한을 확인하세요.\n"
            + resp.content[:300].decode("utf-8", errors="replace")
        )
    return resp.content


def extract_main_xml(zip_bytes: bytes) -> str:
    """zip에서 가장 큰 파일(=메인 본문)을 UTF-8 문자열로 반환."""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    main = max(zf.namelist(), key=lambda n: zf.getinfo(n).file_size)
    return zf.read(main).decode("utf-8", errors="replace")


def clean_dart_xml(xml_str: str) -> str:
    """
    DART 원문의 이스케이프 누락/금지문자를 보정한다.
      0) XML 금지 제어문자 제거 ("invalid token" 원인)
      1) raw '&'  -> '&amp;'  (예: "R&D")
      2) raw '<'  -> '&lt;'   (예: "< TV 시장점유율 추이 >")

    순서 주의: '&'를 먼저 처리한다. '<' 치환으로 생기는 '&lt;'의 '&'는
    이미 유효 엔티티이므로 다시 건드려선 안 되는데, '&' 치환을 먼저 끝내두면
    그 뒤 생성된 '&lt;'는 재처리되지 않는다.

    이 함수로도 못 잡는 깨짐은 get_disclosure_text의 관대한 파서(lxml recover)가
    최종적으로 건너뛴다.
    """
    xml_str = _ILLEGAL_XML_CHARS.sub("", xml_str)
    xml_str = _RAW_AMP.sub("&amp;", xml_str)
    xml_str = _RAW_LT.sub("&lt;", xml_str)
    return xml_str


# 표 서브트리는 통째로 제외한다(숫자 셀이 서술형에 섞이지 않도록).
_SKIP_SUBTREE = {"TABLE", "TABLE-GROUP"}
# 서술형 텍스트를 담고 있는 태그.
_TEXT_TAGS = {"P", "TITLE"}


def extract_narrative_text(root) -> str:
    """
    표(TABLE) 서브트리를 제외하고 서술형 태그(P, TITLE)의 텍스트만 수집한다.

    이유: 사업보고서는 표 관련 태그(TD/TR/TH...)가 압도적으로 많고,
    표를 평면화하면 숫자가 맥락 없이 나열되어 RAG 검색 품질을 해친다.
    재무/실적 수치는 이후 DART 정형 API로 따로 가져오는 편이 정확하다.

    TABLE 서브트리를 통째로 건너뛰므로, TD 안에 중첩된 P도 함께 제외된다.
    P/TITLE에 도달하면 그 하위 텍스트(SPAN 등 인라인 포함)를 itertext로 모은다.
    """
    out = []

    def walk(elem):
        tag = elem.tag
        if tag in _SKIP_SUBTREE:
            return
        if tag in _TEXT_TAGS:
            text = "".join(elem.itertext()).strip()
            if text:
                out.append(text)
            return  # 이미 하위 텍스트를 다 모았으니 더 내려가지 않음
        for child in elem:
            walk(child)

    walk(root)
    return "\n\n".join(out)


def get_disclosure_text(rcept_no: str) -> str:
    """
    rcept_no -> 서술형 본문 텍스트(표 제외).
    """
    zip_bytes = fetch_document_zip(rcept_no)
    xml_str = clean_dart_xml(extract_main_xml(zip_bytes))
    # DART 원문은 표준 XML을 벗어난 토큰(SGML 잔재·특수문자)이 섞여 있을 때가 많아,
    # 표준 파서 대신 관대한 recover 파서로 깨진 토큰을 건너뛰며 파싱한다.
    # huge_tree=True: 사업보고서 등 대용량 문서의 크기/깊이 제한을 푼다.
    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.fromstring(xml_str.encode("utf-8"), parser=parser)
    if root is None:
        return ""
    return extract_narrative_text(root)
