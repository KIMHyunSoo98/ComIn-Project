"""
DART 공시원문에서 본문 텍스트 추출.
해당 파일은 AI(클로드 Opus 4.8)로 작성한 코드.

expand_table() -> COLSPAN/ROWSPAN을 전개해 표를 직사각형 격자로 복원하는 함수
linearize_table() -> 격자를 행 단위 평문으로 바꾸는 함수
extract_narrative_text() -> 표를 뺀 서술형 텍스트만 모으는 함수
extract_table_text() -> 표만 행 단위 평문으로 모으는 함수
get_disclosure_text() -> rcept_no로 서술형 텍스트를 얻는 함수
get_disclosure_texts() -> rcept_no로 (서술형, 표) 텍스트를 한 번에 얻는 함수
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


# 서술형 텍스트를 담고 있는 태그.
_TEXT_TAGS = {"P", "TITLE"}
# 표의 셀 태그. DART 원문은 TD 외에 TE(입력셀)도 쓴다.
_CELL_TAGS = {"TD", "TE", "TH"}


def _cell_text(cell) -> str:
    """셀 텍스트를 공백 정규화해서 돌려준다."""
    return " ".join("".join(cell.itertext()).split())


def _own_rows(table) -> list:
    """
    이 표에 직접 속한 TR만 고른다.
    TD 안에 표가 중첩될 수 있어, 가장 가까운 TABLE 조상이 자기 자신인 것만 남긴다.
    """
    rows = []
    for tr in table.iter("TR"):
        owner = next((a for a in tr.iterancestors() if a.tag == "TABLE"), None)
        if owner is table:
            rows.append(tr)
    return rows


def expand_table(table) -> list[list[str]]:
    """
    COLSPAN / ROWSPAN을 전개해 직사각형 격자로 복원한다.

    병합 셀을 풀지 않으면 행마다 셀 개수가 달라 열이 밀린다.
    (배당 표: 헤더가 ROWSPAN=2라 헤더행 5칸 / 데이터행 4칸 -> 숫자가 옆 열로 들어감)
    병합된 셀의 값은 덮는 칸마다 복제한다. 각 행이 헤더를 온전히 갖게 하려는 것이다.
    """
    grid: list[list[str | None]] = []

    def ensure(r: int, c: int) -> None:
        while len(grid) <= r:
            grid.append([])
        while len(grid[r]) <= c:
            grid[r].append(None)

    for r, tr in enumerate(_own_rows(table)):
        ensure(r, 0)
        col = 0
        for cell in tr:
            if cell.tag not in _CELL_TAGS:
                continue
            # 위 행의 ROWSPAN이 이미 채워둔 칸은 건너뛴다.
            while col < len(grid[r]) and grid[r][col] is not None:
                col += 1

            text = _cell_text(cell)
            colspan = max(1, int(cell.get("COLSPAN") or cell.get("colspan") or 1))
            rowspan = max(1, int(cell.get("ROWSPAN") or cell.get("rowspan") or 1))

            for dr in range(rowspan):
                for dc in range(colspan):
                    ensure(r + dr, col + dc)
                    grid[r + dr][col + dc] = text
            col += colspan

    width = max((len(row) for row in grid), default=0)
    return [[(v or "") for v in row] + [""] * (width - len(row)) for row in grid]


def _header_row_count(table, grid: list[list[str]]) -> int:
    """
    헤더가 몇 행인지 센다. THEAD가 있으면 그 행 수를, 없으면 첫 행이 TH뿐인지로 판단한다.
    """
    thead = table.find("THEAD")
    if thead is not None:
        count = sum(1 for tr in thead.iter("TR"))
        if count:
            return min(count, len(grid))
    first = next(iter(_own_rows(table)), None)
    if first is not None:
        cells = [c for c in first if c.tag in _CELL_TAGS]
        if cells and all(c.tag == "TH" for c in cells):
            return 1
    return 0


def _merge_header(column: list[str]) -> str:
    """
    다단 헤더를 한 줄로 합친다. ROWSPAN 전개로 생긴 연속 중복은 접는다.
    ["당기", "제9기"] -> "당기(제9기)" / ["구 분", "구 분"] -> "구 분"
    """
    parts: list[str] = []
    for value in column:
        if value and (not parts or parts[-1] != value):
            parts.append(value)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}({' '.join(parts[1:])})"


def linearize_table(table) -> str:
    """
    표를 행 단위 평문으로 바꾼다. 각 행이 헤더를 안고 있어 어디서 잘려도 자립한다.

    "배당성향(%) — 당기(제9기): 16.9, 전기(제8기): 19.8"

    마크다운 표 대신 이 형식을 쓰는 이유: 청킹이 글자 수 기준이라 표 중간에서 잘리는데,
    마크다운은 헤더 행과 분리되는 순간 숫자만 남은 고아 청크가 된다.
    문장형이라 임베딩 모델(KURE)에도 더 자연스러운 입력이다.
    """
    grid = expand_table(table)
    if not grid:
        return ""

    head_count = _header_row_count(table, grid)
    body = grid[head_count:]
    if not body:
        # 헤더뿐인 표(1행짜리 레이아웃 표 등)는 셀을 이어 붙인다.
        return " ".join(v for v in grid[0] if v) if grid else ""

    headers = [_merge_header([row[c] for row in grid[:head_count]]) for c in range(len(grid[0]))]

    lines = []
    for row in body:
        label = row[0]
        pairs = [
            f"{headers[c]}: {row[c]}" if c < len(headers) and headers[c] else row[c]
            for c in range(1, len(row))
            if row[c] and row[c] != label
        ]
        if label and pairs:
            lines.append(f"{label} — {', '.join(pairs)}")
        elif label or pairs:
            lines.append(label or ", ".join(pairs))
    return "\n".join(lines)


def extract_narrative_text(root) -> str:
    """
    표(TABLE) 서브트리를 제외하고 서술형 태그(P, TITLE)의 텍스트만 수집한다.

    표를 같은 컬렉션에 섞으면 짧은 표 행 수만 개가 검색 상위를 독점해 서술형 근거가 밀려난다.
    (측정: 통합 인덱스 상위 16개 중 14~16개가 표 행)
    그래서 표는 extract_table_text로 따로 뽑아 별도 컬렉션에 넣는다.

    TABLE 서브트리를 통째로 건너뛰므로, TD 안에 중첩된 P도 함께 제외된다.
    P/TITLE에 도달하면 그 하위 텍스트(SPAN 등 인라인 포함)를 itertext로 모은다.
    """
    out = []

    def walk(elem):
        tag = elem.tag
        if tag in ("TABLE", "TABLE-GROUP"):
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


def extract_table_text(root) -> str:
    """
    표만 행 단위 평문으로 모은다. 표 묶음 안의 제목("2-1. 연결 재무상태표")도 함께 싣는다.

    표를 버리면 사업보고서 텍스트의 79~91%가 사라진다(삼성전자 810,929자 -> 73,203자).
    보수·배당·부채비율처럼 표에만 있는 수치는 답할 근거가 아예 없었다.
    """
    out = []

    def walk(elem, in_group: bool):
        tag = elem.tag
        if tag == "TABLE":
            text = linearize_table(elem)
            if text:
                out.append(text)
            return  # 중첩 표는 바깥 표의 셀 텍스트로 이미 포함된다
        if tag in _TEXT_TAGS:
            if in_group:  # 표 제목. 표와 같은 컬렉션에 있어야 문맥이 붙는다.
                text = "".join(elem.itertext()).strip()
                if text:
                    out.append(text)
            return
        for child in elem:
            walk(child, in_group or tag == "TABLE-GROUP")

    walk(root, False)
    return "\n\n".join(out)


def _parse_document(rcept_no: str):
    """공시 원문 zip을 받아 XML 트리로 만든다. 파싱은 무거우므로 한 번만 한다."""
    xml_str = clean_dart_xml(extract_main_xml(fetch_document_zip(rcept_no)))
    # DART 원문은 표준 XML을 벗어난 토큰(SGML 잔재·특수문자)이 섞여 있을 때가 많아,
    # 표준 파서 대신 관대한 recover 파서로 깨진 토큰을 건너뛰며 파싱한다.
    # huge_tree=True: 사업보고서 등 대용량 문서의 크기/깊이 제한을 푼다.
    parser = etree.XMLParser(recover=True, huge_tree=True)
    return etree.fromstring(xml_str.encode("utf-8"), parser=parser)


def get_disclosure_text(rcept_no: str) -> str:
    """
    rcept_no -> 서술형 본문 텍스트(표 제외).
    """
    root = _parse_document(rcept_no)
    return "" if root is None else extract_narrative_text(root)


def get_disclosure_texts(rcept_no: str) -> tuple[str, str]:
    """
    rcept_no -> (서술형 텍스트, 표 텍스트). 각각 다른 컬렉션에 적재한다.
    원문 다운로드와 파싱을 한 번만 하려고 둘을 함께 돌려준다.
    """
    root = _parse_document(rcept_no)
    if root is None:
        return "", ""
    return extract_narrative_text(root), extract_table_text(root)
