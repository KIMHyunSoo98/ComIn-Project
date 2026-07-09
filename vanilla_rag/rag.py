"""
LangChain 라이브러리를 사용하지 않고 파이썬으로 구현한 RAG 파이프라인.


"""


from data.collect_data import research
from data.dart_origin_document import get_disclosure_text
from data.chunking import chunk_text
from data.embedding import get_collection, store_chroma_db, check_disclosure_in_db, search


if __name__ == "__main__":
    
    query = input("회사명: ")
    
    information = research(query)
    corp_code = information.get("corp_code")
    collection = get_collection()
    for dis in information.get("disclosures", []):
        rcept_no = dis.get("rcept_no")
        
        # 해당 공시가 이미 적재되어있는지 확인
        if check_disclosure_in_db(collection=collection, rcept_no=rcept_no) == True:
            continue

        # 적재 안 되어있을 떄
        texts = get_disclosure_text(rcept_no) # 원본 공시 파일의 텍스트 추출
        chunks = chunk_text(text=texts, chunk_size=500, overlap=50) # 청킹
        store_chroma_db(collection=collection, chunks=chunks, rcept_no=rcept_no, corp_code=corp_code) # 임베딩 후 저장
    
    # 검색
    results = search(collection=collection, query=query, corp_code=corp_code, n_results=3)

    print("결과: \n", results)
