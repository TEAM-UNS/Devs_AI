"""공고 벡터 검색 툴. → market.queries

search_postings(query, field?, career_max?, section?, top=10)
    질의를 EmbedderPort 로 임베딩 → posting_chunk.embedding 코사인 검색
    (HNSW). 메타 필터(field · career · 수집기간)를 같은 쿼리에서 건다.
    반환: 공고 제목 · 회사 · 매칭 청크 · URL · 유사도

section 으로 검색 범위를 좁힐 수 있다
    required        "이 기술을 필수로 요구하는 공고"
    preferred       "우대사항에 있는 공고"
    responsibility  "이런 일을 하는 공고"

주의: 순위·비율 질문에는 쓰지 않는다(벡터 상위 N 은 통계가 아니다).
      그런 질문은 trend 계열 툴로 유도하도록 description 에 명시한다.
"""
