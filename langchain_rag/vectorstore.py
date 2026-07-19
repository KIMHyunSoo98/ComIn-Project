"""
LangChain 컴포넌트로 구현한 임베딩 / 벡터 스토어 / 청킹 / 검색.
vanilla의 data/embedding.py와 data/chunking.py를 대체한다.

임베딩 모델은 nlpai-lab/KURE-v1(한국어 검색 특화, 8192토큰, 1024차원)을 쓴다.
Phase 2 초기에는 vanilla와 같은 ko-sroberta(128토큰)로 chroma_db를 재사용했으나,
긴 문서/표 수용과 검색 품질을 위해 2026-07-16 KURE-v1로 교체하며 새 컬렉션에 재적재했다.
(배경: docs/2026-07-16-embedding-model-change.md)

get_embeddings() -> 임베딩 모델을 한 번만 로드해 재사용하는 함수
get_vectorstore() -> Chroma 벡터 스토어를 가지고 오는 함수
split_disclosure_text() -> 공시 원본 텍스트를 Document 청크 리스트로 나누는 함수
store_disclosure() -> 청크 Document들을 메타데이터와 함께 벡터 스토어에 저장하는 함수
check_disclosure_in_db() -> 현재 공시보고서가 DB에 있는지 확인하는 함수
search_disclosure() -> 질의를 입력받아 유사 청크를 유사도와 함께 반환하는 함수
filter_disclosure_by_relevance() -> 유사도가 임계값 이상인 공시 청크만 고르는 함수
"""

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter


# 한국어 검색 특화 임베딩. bge-m3 기반이라 최대 8192토큰 / 1024차원.
MODEL_NAME = "nlpai-lab/KURE-v1"
CHROMA_PATH = "chroma_db"
# 임베딩 차원이 768->1024로 바뀌어 기존 'disclosures' 컬렉션과 호환되지 않으므로 새 컬렉션을 쓴다.
COLLECTION_NAME = "disclosures_kure_v1"
RELEVANCE_THRESHOLD = 0.3  # 청크 반환 시 임계점. cosine 유사도 기준

_embeddings = None
_vectorstore = None


def get_embeddings():
    """
    HuggingFaceEmbeddings 인스턴스를 한 번만 만들어 재사용한다.
    적재와 검색이 반드시 같은 임베딩을 써야 임베딩 공간이 일치한다.

    normalize_embeddings=True로 임베딩을 L2 정규화한다.
    - KURE-v1(bge-m3 계열)은 정규화 + cosine 사용을 전제로 학습됐다.
    - 정규화하면 cosine과 L2가 같은 순위를 주므로 거리 지표 선택에서 자유로워진다.
    """
    global _embeddings

    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(
            model_name=MODEL_NAME,
            encode_kwargs={"normalize_embeddings": True},
        )
    return _embeddings


def get_vectorstore():
    """
    로컬 chroma_db의 컬렉션을 Chroma 벡터 스토어로 감싸서 가지고 온다.
    거리 지표는 vanilla와 동일하게 cosine.
    """
    global _vectorstore

    if _vectorstore is None:
        _vectorstore = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=get_embeddings(),
            persist_directory=CHROMA_PATH,
            collection_metadata={"hnsw:space": "cosine"},
            )
    return _vectorstore


def split_disclosure_text(
    text: str,
    rcept_no: str,
    corp_code: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[Document]:
    """
    공시 원본 텍스트를 RecursiveCharacterTextSplitter로 청킹해 Document 리스트로 반환한다.
    문단('\\n\\n') -> 줄('\\n') -> 공백 순으로 끊어, vanilla의 '문단 우선, 초과하면 분할' 전략을 따른다.
    각 Document의 metadata에는 corp_code, rcept_no, chunk_index를 넣는다.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", " ", ""],    
    )
    
    chunks = splitter.split_text(text)

    return [
        Document(
            page_content=chunk,
            metadata={"corp_code":corp_code, "rcept_no":rcept_no, "chunk_index": i}
        )
        for i, chunk in enumerate(chunks)
    ]


def store_disclosure(vectorstore, documents: list[Document]) -> None:
    """
    청크 Document들을 벡터 스토어에 저장한다. 임베딩은 벡터 스토어가 내부에서 처리한다.
    id는 vanilla와 동일하게 '{rcept_no}_{chunk_index}' 형식으로 만든다.
    """
    ids = [ f"{doc.metadata['rcept_no']}_{doc.metadata['chunk_index']}" for doc in documents]

    vectorstore.add_documents(documents=documents, ids=ids)


def check_disclosure_in_db(vectorstore, rcept_no: str) -> bool:
    """
    해당 공시보고서가 DB에 존재하는지 메타데이터 rcept_no로 확인한다.
    이미 적재된 문서면 재임베딩을 생략하기 위함이다.
    """
    existing = vectorstore.get(where={"rcept_no": rcept_no}, limit=1)
    return len(existing["ids"]) > 0


def search_disclosure(
    vectorstore,
    query: str,
    corp_code: str = None,
    k: int = 3,
) -> list[tuple[Document, float]]:
    """
    질의를 입력받아 유사 청크를 (Document, 유사도) 튜플 리스트로 반환한다.
    corp_code로 메타데이터 필터를 걸어 해당 회사의 청크만 검색한다.
    Chroma의 cosine relevance score는 1 - distance라서 vanilla의 유사도와 같은 값이다.
    """
    _filter = {"corp_code": corp_code} if corp_code else None
    return vectorstore.similarity_search_with_relevance_scores(query=query, k=k, filter=_filter)


def filter_disclosure_by_relevance(
    results: list[tuple[Document, float]],
    threshold: float = RELEVANCE_THRESHOLD,
) -> list[tuple[Document, float]]:
    """
    검색 결과에서 유사도가 임계값 미만인 청크를 걸러낸다.
    전부 걸러지면 유사도가 제일 높은 값 하나만 반환한다.
    LangChain 리트리버에는 이 fallback이 없어서 직접 유지한다.
    """

    kept = [(doc, score) for doc, score in results if score >= threshold]
    
    if not kept and results:
        kept = [max(results, key=lambda r: r[1])]

    return kept
