"""HNSW 인덱스 DDL — ★ 데이터 적재 뒤에 만든다.

빈 테이블에 인덱스를 먼저 걸면 INSERT 마다 HNSW 그래프를 갱신하느라 초기
적재가 몇 배 느려진다. 게다가 한 행씩 넣어 만든 그래프는 한 번에 만든 것보다
품질도 떨어진다.

그래서 순서를 이렇게 잡는다.

    ① alembic upgrade head      스키마 + 벡터 컬럼 (HNSW 인덱스 없음)
    ② 수집 · 임베딩              대량 INSERT
    ③ python -m app.cli vector-index --build

인덱스가 없어도 검색은 동작한다(순차 스캔). 정확도는 오히려 100% 다.
느려질 뿐이므로 ③ 을 잊어도 조용히 틀리지는 않는다.

파라미터는 models.py 의 _HNSW 와 같아야 한다 (m=16, ef_construction=64).
"""

HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64

_WITH = f"WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})"

INDEXES: tuple[tuple[str, str], ...] = (
    (
        "posting_chunk_embedding_idx",
        (
            "CREATE INDEX IF NOT EXISTS posting_chunk_embedding_idx "
            f"ON market.posting_chunk USING hnsw (embedding vector_cosine_ops) {_WITH}"
        ),
    ),
    (
        "company_profile_embedding_idx",
        (
            "CREATE INDEX IF NOT EXISTS company_profile_embedding_idx "
            f"ON market.company USING hnsw (profile_embedding vector_cosine_ops) {_WITH}"
        ),
    ),
)

DROP_STATEMENTS: tuple[str, ...] = tuple(
    f"DROP INDEX IF EXISTS market.{name}" for name, _ in INDEXES
)

# ★ 인덱스 생성 전에 이 세션 설정을 먼저 건다.
BUILD_SESSION_SETUP: tuple[str, ...] = ("SET max_parallel_maintenance_workers = 0",)
