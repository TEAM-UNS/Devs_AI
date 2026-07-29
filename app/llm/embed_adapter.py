"""EmbedderPort 구현 — 임베딩 API 어댑터 (Voyage).

책임
    - 배치 호출: 최대 EMBED_BATCH_SIZE(96) 개씩 묶어 1회 호출
    - 입력 타입 구분: document(적재용) / query(검색용)
    - 재시도 EMBED_MAX_RETRY(3) 회. 최종 실패는 UpstreamError
    - 반환 벡터 차원이 EMBED_DIM(1024) 과 다르면 즉시 실패시킨다
      (DB 컬럼 vector(1024) 와 어긋나면 INSERT 단계에서야 터진다)

호출 지점: crawler/embed_service.py, chat/tools/search.py(질의 임베딩)
"""
