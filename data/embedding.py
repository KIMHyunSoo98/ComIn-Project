"""
임베딩과 벡터 DB, 검색과 관련된 함수들이 있는 파일

get_collection() -> 로컬 저장소의 컬렉션을 가지고 오는 함수
get_model() -> 임베딩 모델을 로드하는 함수
embed() -> 청크들을 입력받아 임베딩하는 함수
store_chroma_db() -> 청크들을 메타데이터와 함께 벡터 DB에 저장하는 함수
check_disclosure_in_db() -> 현재 공시보고서가 DB에 있는지 확인하는 함수. 현재 벡터 DB는 chroma
search() -> 질의를 입력 받아 유사 청크를 반환하는 함수
filter_disclosure_by_relevance() -> 유사도가 임계값 이상인 공시 청크만 고르는 함수
"""

import chromadb
from sentence_transformers import SentenceTransformer


MODEL_NAME = "jhgan/ko-sroberta-multitask" # 한국어 경량 SBERT (768차원)
CHROMA_PATH = "chroma_db"
COLLECTION_NAME = "disclosures"
RELEVANCE_THRESHOLD = 0.3 # 청크 반환 시 임계점. cosine 유사도 기준

_model = None


def get_collection():
    """
    로컬 저장소의 컬렉션을 가지고 온다.
    컬렉션의 저장 방식은 코사인 거리.
    """
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def get_model():
    """
    모델을 한 번만 로드해 재사용한다.
    나중에 fastAPI로 서버 띄울 때 유용.
    """
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(chunks: list[str]):
    """
    청크들을 입력으로 받아 임베딩 후 반환한다.
    적재와 검색이 반드시 이 함수를 함께 써야 임베딩 공간이 일치한다.
    """
    vectors = get_model().encode(chunks)
    return vectors.tolist()


def store_chroma_db(collection, chunks: list[str], rcept_no: str, corp_code: str):
    """
    청크들을 입력으로 받아 메타데이터와 함께 벡터 DB에 저장한다.
    현재 메타 데이터는 회사코드, 서류접수번호, 청크 번호이다.
    """
    embeddings = embed(chunks)

    ids = [f"{rcept_no}_{i}" for i in range(len(chunks))]
    metadatas = [{"corp_code": corp_code, "rcept_no": rcept_no, "chunk_index": i} for i in range(len(chunks))]

    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=chunks,
        metadatas=metadatas,
    )


def check_disclosure_in_db(collection, rcept_no: str) -> bool:
    """
    해당 공시보고서가 DB에 존재하는지 확인한다.
    """

    # 이미 적재된 문서면 재임베딩 생략 (메타데이터 rcept_no로 확인)
    existing = collection.get(where={"rcept_no": rcept_no}, limit=1)
    if existing["ids"]:
        return True
    
    return False
    

def search(collection, query: str, corp_code: str = None, n_results: int = 3):
    """
    질의를 입력받아 유사 청크를 반환한다.
    문서를 임베딩 할 떄와 같은 embed()를 써서 임베딩 공간을 일치시킴.
    """
    query_emb = embed([query])
    where = {"corp_code": corp_code} if corp_code else None
    return collection.query(query_embeddings=query_emb, n_results=n_results, where=where)


def filter_disclosure_by_relevance(results, threshold: float = RELEVANCE_THRESHOLD):
    """
    검색 결과에서 유사도가 임계값 미만인 청크를 걸러낸다.
    전부 걸러지면 유사도가 제일 높은 값 하나만 반환한다.
    """
    documents = results.get("documents", [[]])[0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    kept = [
        {"document": doc, "similarity": 1-dist, "metadata": meta}
        for doc, dist, meta in zip(documents, distances, metadatas)
        if (1-dist) >= threshold
    ]

    # 전부 걸러졌지만 검색 결과 자체는 있으면, 가장 유사한 1개는 남긴다.
    if not kept and documents:
        best_idx = min(range(len(distances)), key=lambda i: distances[i])
        kept = [{
            "document": documents[best_idx],
            "similarity": 1 - distances[best_idx],
            "metadata": metadatas[best_idx],
        }]

    return kept
