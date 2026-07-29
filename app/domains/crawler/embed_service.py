"""임베딩 오케스트레이션 — 청크 · 기업 프로필 · 스킬.

공고 임베딩
    대상: embed_hash IS DISTINCT FROM content_hash
    chunker 로 청크 생성 → chunk_hash 가 바뀐 청크만 EmbedderPort 호출
    배치: EMBED_BATCH_SIZE(96) 개 단위로 API 1회 (태스크당 1~2회)
    성공 시에만 embed_hash = content_hash 로 갱신
    실패: 3회 재시도 후에도 실패하면 embed_hash 를 갱신하지 않는다
          → 다음 백필에서 자연히 재처리된다

기업 프로필 임베딩
    description + business_content + industry 를 합쳐 임베딩
    셋 다 비어 있으면 스킵

스킬 임베딩
    skill.name + aliases. 200행 규모라 앱 시작 시 메모리로 로드해 쓴다

백필
    누락·실패분 최대 EMBED_BACKFILL_LIMIT(500)건/회
    body_is_image=true · description IS NULL 제외
"""
