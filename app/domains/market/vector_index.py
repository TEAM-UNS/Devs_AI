# 적재 후에 만드는 HNSW 인덱스 DDL

# models.py 의 _HNSW 와 값이 같아야 한다
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

BUILD_SESSION_SETUP: tuple[str, ...] = ("SET max_parallel_maintenance_workers = 0",)
