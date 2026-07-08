"""
공시 원본 텍스트를 청크로 나눈다.

입력: 공시 서류 원본 파일의 서술형 텍스트
    (문단이 '\n\n'으로 구분되어 있음)
"""

from dart_origin_document import get_disclosure_text


def split_long_paragraph(para: str, chunk_size: int, overlap: int):
    """
    목표 크기보다 큰 문단을 문자 단위 슬라이딩 윈도우로 분할.
    """

    chunks = []
    start = 0
    step = chunk_size - overlap  # overlap만큼 겹치며 전진
    while start < len(para):
        chunks.append(para[start : start + chunk_size])
        start += step

    return chunks


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50):
    """
    서술형 텍스트를 청크 리스트로 나눈다. chunk_size, overlap은 글자 수 기준.
    바닐라 RAG에서는 최적값을 찾기보다 제대로 동작한다는 것에 의의를 둔다.
    """
    
    if overlap >= chunk_size:
        raise ValueError("overlap은 chunk_size보다 작아야 합니다.")

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""

    for para in paragraphs:
        # 문단이 목표보다 클 때 -> 현재 버퍼를 비우고 그 문단만 따로 분할
        if len(para) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(split_long_paragraph(para, chunk_size, overlap))
            continue

        # 현재 버퍼에 이어 붙일 수 있으면 붙이고 초과하면 끊기
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = para
    
    # 버퍼에 남은거 있으면 청크에 추가하기
    if current:
        chunks.append(current)
    return chunks
