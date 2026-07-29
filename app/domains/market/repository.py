"""쓰기 담당 — 크롤러/임베딩 전용. (챗봇은 접근 금지, R3)

기능
    upsert_company           name_key 기준 병합 + company_source 기록
    upsert_posting           (source, source_job_id) 충돌 시 갱신.
                             content_hash 동일하면 collected_at 만 갱신하고 skip 반환
    replace_posting_skills   공고의 스킬 목록 전체 교체
    upsert_chunks            chunk_hash 다른 청크만 갱신 (posting_id, section, seq)
    save_embeddings          청크/기업/스킬 임베딩 반영 + embed_hash 갱신
    start_run / finish_run   crawl_run 기록
    fetch_embed_targets      embed_hash IS DISTINCT FROM content_hash 인 공고
                             (body_is_image · description IS NULL 제외)

INSERT ... ON CONFLICT DO UPDATE (postgres) 를 사용한다.
"""
