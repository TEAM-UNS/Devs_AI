#!/usr/bin/env bash
# market 스키마 인계용 덤프 생성.
#
#   bash scripts/export_handoff.sh            임베딩 미완료면 중단
#   bash scripts/export_handoff.sh --force    미완료여도 진행
#
# 만들어지는 것 (handoff/ 아래)
#   market_YYYYMMDD.dump      전체 데이터 (custom format, 압축) ← 이걸 넘긴다
#   market_YYYYMMDD.schema.sql 스키마만 (사람이 읽고 리뷰하는 용도)
#   00-prepare.sql            복원 전에 먼저 실행할 확장·스키마 준비
#   MANIFEST.md               복원 절차 + 검증 쿼리 + 행 수 스냅샷
#
# ★ 핵심: pg_dump 는 --schema 를 주면 CREATE EXTENSION 을 덤프하지 않는다.
#   받는 쪽에서 pgvector 를 먼저 깔지 않으면 vector(1024) 컬럼에서 복원이 깨진다.
#   그래서 00-prepare.sql 을 따로 만든다.
set -euo pipefail

CONTAINER="${CONTAINER:-jobstack-postgres}"
DB="${DB:-jobstack}"
DBUSER="${DBUSER:-jobstack}"
OUT="handoff"
STAMP="$(date +%Y%m%d)"
FORCE="${1:-}"

psql_q() { docker exec "$CONTAINER" psql -U "$DBUSER" -d "$DB" -tAc "$1"; }

mkdir -p "$OUT"

# ── 1. 임베딩이 끝났는지 확인 ────────────────────────────────────────────
PENDING=$(psql_q "SELECT COUNT(*) FROM market.job_posting
                  WHERE description IS NOT NULL AND body_is_image = false
                    AND body_extract_failed = false
                    AND (embed_hash IS NULL OR embed_hash IS DISTINCT FROM content_hash);")
echo "임베딩 미완료 공고: ${PENDING}건"
if [ "$PENDING" -gt 0 ] && [ "$FORCE" != "--force" ]; then
  echo
  echo "  아직 임베딩이 남았습니다. 벡터가 빈 채로 넘어가면 받는 쪽에서 다시 돌려야 합니다."
  echo "  기다렸다 다시 실행하거나, 의도한 것이면 --force 를 붙이세요."
  exit 1
fi

# ── 2. 복원 전 준비 스크립트 ────────────────────────────────────────────
VECTOR_VER=$(psql_q "SELECT extversion FROM pg_extension WHERE extname='vector';")
PG_VER=$(psql_q "SHOW server_version;")

cat > "$OUT/00-prepare.sql" <<SQL
-- ═══════════════════════════════════════════════════════════════════════
--  복원 전에 이걸 먼저 실행하세요.
--
--  pg_dump 는 --schema 옵션을 주면 CREATE EXTENSION 을 덤프에 넣지 않습니다.
--  pgvector 가 없는 DB 에 복원하면 vector(1024) 컬럼에서 바로 실패합니다.
--
--  원본 환경: PostgreSQL ${PG_VER} · pgvector ${VECTOR_VER}
--  이미지   : pgvector/pgvector:pg16
-- ═══════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;     -- posting_chunk.embedding 등 필수
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- company_name_trgm_idx 필수
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- market 스키마는 덤프가 직접 만든다. 여기서 미리 만들면 복원 때
-- "CREATE SCHEMA market" 이 중복으로 실패해 경고가 뜬다.

-- ★ updated_at 갱신 트리거 함수.
--   public 스키마에 있어서 --schema=market 덤프에 안 들어간다. 이게 없으면
--   복원 마지막에 CREATE TRIGGER 3개가 전부 실패한다
--   (company_touch · job_posting_touch · skill_touch).
--   데이터는 들어가지만 이후 UPDATE 에서 updated_at 이 안 갱신된다.
CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS \$function\$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
\$function\$;
SQL

# ── 2.5 pgvector 없는 환경용 변환본 ────────────────────────────────────
# 받는 쪽에 pgvector 가 없으면 vector(1024) 컬럼에서 복원이 그냥 실패한다.
# 벡터를 real[] 로 바꿔 두면 아무 postgres 에나 들어가고, 나중에 pgvector 를
# 깔았을 때 ALTER 한 줄로 되돌릴 수 있다. 왕복 무손실은 검증했다.
NOVEC_DB="jobstack_novec"
if [ "${WITH_NOVEC:-1}" = "1" ]; then
  echo "pgvector 없는 환경용 변환본 생성 중…"
  docker exec "$CONTAINER" psql -U "$DBUSER" -d postgres -q \
    -c "DROP DATABASE IF EXISTS ${NOVEC_DB};" \
    -c "CREATE DATABASE ${NOVEC_DB} TEMPLATE ${DB};" >/dev/null
  docker exec "$CONTAINER" psql -U "$DBUSER" -d "$NOVEC_DB" -q -c "
    DROP INDEX market.posting_chunk_embedding_idx;
    DROP INDEX market.company_profile_embedding_idx;
    ALTER TABLE market.posting_chunk
      ALTER COLUMN embedding TYPE real[] USING embedding::real[];
    ALTER TABLE market.company
      ALTER COLUMN profile_embedding TYPE real[] USING profile_embedding::real[];
    ALTER TABLE market.skill
      ALTER COLUMN embedding TYPE real[] USING embedding::real[];
    DROP EXTENSION vector;" >/dev/null
  docker exec "$CONTAINER" pg_dump -U "$DBUSER" -d "$NOVEC_DB" \
    --format=custom --compress=9 --schema=market --no-owner --no-privileges \
    > "$OUT/market_${STAMP}_no-pgvector.dump"
  docker exec "$CONTAINER" psql -U "$DBUSER" -d postgres -q \
    -c "DROP DATABASE ${NOVEC_DB};" >/dev/null

  cat > "$OUT/01-restore-vectors.sql" <<'SQL'
-- ═══════════════════════════════════════════════════════════════════════
--  pgvector 를 나중에 설치했을 때, real[] 로 받아둔 벡터를 되돌립니다.
--  *_no-pgvector.dump 로 복원한 경우에만 실행하세요.
--
--  왕복(vector → real[] → vector)은 무손실입니다. 재임베딩 불필요.
-- ═══════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE market.posting_chunk
  ALTER COLUMN embedding TYPE vector(1024) USING embedding::vector(1024);
ALTER TABLE market.company
  ALTER COLUMN profile_embedding TYPE vector(1024) USING profile_embedding::vector(1024);
ALTER TABLE market.skill
  ALTER COLUMN embedding TYPE vector(1024) USING embedding::vector(1024);

-- HNSW 인덱스. 병렬 빌드는 도커 기본 /dev/shm(64MB)에서 터지므로 직렬로 짓는다.
SET max_parallel_maintenance_workers = 0;
CREATE INDEX IF NOT EXISTS posting_chunk_embedding_idx
  ON market.posting_chunk USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS company_profile_embedding_idx
  ON market.company USING hnsw (profile_embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

ANALYZE market.posting_chunk;
ANALYZE market.company;
SQL
fi

# ── 3. 덤프 ──────────────────────────────────────────────────────────────
echo "덤프 생성 중… (posting_chunk 가 커서 1~2분 걸립니다)"

# 스키마만 — 리뷰용. 사람이 읽는다.
docker exec "$CONTAINER" pg_dump -U "$DBUSER" -d "$DB" \
  --schema-only --schema=market --no-owner --no-privileges \
  > "$OUT/market_${STAMP}.schema.sql"

# 전체 — 이걸 넘긴다. custom format 이라 압축되고 부분 복원도 된다.
docker exec "$CONTAINER" pg_dump -U "$DBUSER" -d "$DB" \
  --format=custom --compress=9 \
  --schema=market --no-owner --no-privileges \
  > "$OUT/market_${STAMP}.dump"

# alembic 리비전. 받는 쪽이 이후 마이그레이션을 이어가려면 필요하다.
docker exec "$CONTAINER" pg_dump -U "$DBUSER" -d "$DB" \
  --data-only --table=public.alembic_version --no-owner \
  > "$OUT/alembic_version.sql"

# ── 4. 검증용 행 수 스냅샷 ──────────────────────────────────────────────
COUNTS=$(psql_q "
  SELECT string_agg(line, E'\n' ORDER BY ord) FROM (
    SELECT 1 ord, '| tech_field     | ' || COUNT(*) || ' |' line FROM market.tech_field
    UNION ALL SELECT 2, '| company        | ' || COUNT(*) || ' |' FROM market.company
    UNION ALL SELECT 3, '| company_source | ' || COUNT(*) || ' |' FROM market.company_source
    UNION ALL SELECT 4, '| job_posting    | ' || COUNT(*) || ' |' FROM market.job_posting
    UNION ALL SELECT 5, '| posting_skill  | ' || COUNT(*) || ' |' FROM market.posting_skill
    UNION ALL SELECT 6, '| posting_chunk  | ' || COUNT(*) || ' |' FROM market.posting_chunk
    UNION ALL SELECT 7, '| skill          | ' || COUNT(*) || ' |' FROM market.skill
    UNION ALL SELECT 8, '| skill_alias    | ' || COUNT(*) || ' |' FROM market.skill_alias
    UNION ALL SELECT 9, '| skill_field    | ' || COUNT(*) || ' |' FROM market.skill_field
    UNION ALL SELECT 10,'| crawl_run      | ' || COUNT(*) || ' |' FROM market.crawl_run
  ) t;")

VEC_CHUNK=$(psql_q "SELECT COUNT(embedding) FROM market.posting_chunk;")
VEC_COMPANY=$(psql_q "SELECT COUNT(profile_embedding) FROM market.company;")
ALEMBIC=$(psql_q "SELECT version_num FROM public.alembic_version;")
DUMP_SIZE=$(du -h "$OUT/market_${STAMP}.dump" | cut -f1)

# ── 4.5 pgvector 설치 가이드 (받는 쪽이 읽는다) ────────────────────────
GUIDE_SRC="$(dirname "$0")/handoff_pgvector_guide.md"
if [ -f "$GUIDE_SRC" ]; then
  cp "$GUIDE_SRC" "$OUT/PGVECTOR-SETUP.md"
else
  echo "경고: $GUIDE_SRC 가 없어 설치 가이드를 넣지 못했습니다." >&2
fi

# ── 5. 인계 문서 ────────────────────────────────────────────────────────
cat > "$OUT/MANIFEST.md" <<MD
# market 스키마 인계 ($(date +%Y-%m-%d))

채용 공고 수집 데이터입니다. **테이블 10개**를 여러분 DB 에 그대로 이식합니다.

\`\`\`
market.tech_field      기술 분야 8종
market.company         기업 마스터
market.company_source  사이트별 기업 식별자
market.job_posting     공고 본문 · 조건 · 연봉
market.posting_skill   공고 ↔ 요구 기술 (등급 포함)
market.posting_chunk   임베딩 벡터
market.skill           스킬 사전
market.skill_alias     스킬 표기 변형
market.skill_field     스킬 ↔ 분야
market.crawl_run       수집 실행 이력
\`\`\`

**\`market\` 이라는 전용 스키마 안에만 들어갑니다.** 여러분의 기존 테이블
(\`public\` 등)은 전혀 건드리지 않습니다. 이름 충돌도 없습니다.

> pgvector 가 아직 없다면 **PGVECTOR-SETUP.md** 를 먼저 보세요.
> 지금 없어도 복원은 가능합니다 (아래 "pgvector 없이" 절차).

## 먼저 정하세요: pgvector 를 쓸 수 있습니까?

이 데이터의 핵심은 **1024차원 임베딩 벡터 ${VEC_CHUNK}개**입니다. 의미 기반 공고
검색이 여기서 나옵니다. pgvector 없이는 그 기능이 동작하지 않습니다.

| 상황 | 쓸 덤프 |
|---|---|
| pgvector 를 깔 수 있다 (권장) | \`market_${STAMP}.dump\` |
| 지금은 못 깐다 / 나중에 결정 | \`market_${STAMP}_no-pgvector.dump\` |

**두 번째를 골라도 벡터는 안 버립니다.** \`real[]\` 배열로 담겨 있어서, 나중에
pgvector 를 설치하면 \`01-restore-vectors.sql\` 한 번으로 되돌아갑니다.
왕복 무손실이라 재임베딩(6시간 + API 비용)이 필요 없습니다.

| 파일 | 용도 |
|---|---|
| \`PGVECTOR-SETUP.md\` | **pgvector 설치 가이드.** 환경별 절차 |
| \`00-prepare.sql\` | **복원 전 먼저 실행.** 확장 + 트리거 함수 |
| \`market_${STAMP}.dump\` | 전체 데이터 (pgvector 필요, ${DUMP_SIZE}) |
| \`market_${STAMP}_no-pgvector.dump\` | 같은 데이터, 벡터를 \`real[]\` 로 (pgvector 불필요) |
| \`01-restore-vectors.sql\` | 나중에 pgvector 깔았을 때 벡터 되돌리기 |
| \`market_${STAMP}.schema.sql\` | 스키마만. 읽고 리뷰하는 용도 |
| \`alembic_version.sql\` | 마이그레이션 리비전 (\`${ALEMBIC}\`) |

## 전제 조건

- **PostgreSQL 16 이상**
- **pgvector 확장 필수** (원본: ${VECTOR_VER}). 없으면 \`vector(1024)\` 컬럼에서 복원이 실패합니다.
  가장 쉬운 방법은 \`pgvector/pgvector:pg16\` 이미지를 쓰는 것입니다.
- pg_trgm (회사명 부분검색 인덱스), pgcrypto

## 복원 — pgvector 없이 (\`_no-pgvector.dump\`)

\`\`\`bash
# 1) 트리거 함수 등 준비. vector 확장 줄은 없어도 통과합니다
psql -U <user> -d <db> -f 00-prepare.sql

# 2) 복원
pg_restore -U <user> -d <db> --no-owner --no-privileges -j 4 \\
    market_${STAMP}_no-pgvector.dump

# 3) 통계
psql -U <user> -d <db> -c "ANALYZE market.posting_chunk; ANALYZE market.job_posting; ANALYZE market.posting_skill;"
\`\`\`

이 상태에서 **공고 · 기업 · 스킬 데이터는 전부 정상 동작**합니다. 벡터 검색만
못 씁니다. 나중에 pgvector 를 설치하면:

\`\`\`bash
psql -U <user> -d <db> -f 01-restore-vectors.sql
\`\`\`

## 복원 — pgvector 있을 때 (\`market_${STAMP}.dump\`)

\`\`\`bash
# 1) 확장 · 스키마 · 트리거 함수 준비  ← 빠뜨리면 반드시 실패합니다
psql -U <user> -d <db> -f 00-prepare.sql

# 2) 데이터 복원 (-j 4 = 4병렬)
pg_restore -U <user> -d <db> --no-owner --no-privileges -j 4 market_${STAMP}.dump

# 3) ★ 통계 수집. 빠뜨리면 벡터 검색이 HNSW 인덱스를 안 탑니다
psql -U <user> -d <db> -c "ANALYZE market.posting_chunk; ANALYZE market.company; ANALYZE market.job_posting; ANALYZE market.posting_skill;"

# 4) 마이그레이션 리비전 (alembic 을 이어서 쓸 경우만)
psql -U <user> -d <db> -f alembic_version.sql
\`\`\`

\`pg_restore\` 는 **파일 경로**로 주세요. 표준입력(\`< file\`)으로 주면 \`-j\` 병렬 옵션이
동작하지 않습니다.

정상이면 에러 0개입니다. \`touch_updated_at() does not exist\` 가 뜨면 1번을 건너뛴 것입니다.

## ★ random_page_cost — 이걸 안 하면 인덱스를 만들어 놓고도 안 씁니다

pgvector 의 HNSW 는 인덱스 시작비용 추정이 큽니다. PostgreSQL 기본값
\`random_page_cost = 4.0\` 에서는 플래너가 **순차 스캔이 더 싸다고 오판**합니다.
결과는 맞고 느리기만 해서 알아채기 어렵습니다.

원본 환경 실측 (청크 ${VEC_CHUNK}개):

| | 실행 시간 |
|---|---|
| 순차 스캔 (기본값 4.0 에서 플래너가 고르는 것) | **61.1 ms** |
| HNSW 인덱스 (random_page_cost=1.1) | **0.8 ms** |

**61배 차이**입니다. SSD 환경이면 1.1 이 어차피 권장값입니다.

\`\`\`sql
-- 서버 전체에 적용 (권장)
ALTER SYSTEM SET random_page_cost = 1.1;
SELECT pg_reload_conf();
\`\`\`

도커라면 compose 의 command 에 \`-c random_page_cost=1.1\` 을 넣으세요.

### 확인

\`\`\`sql
EXPLAIN SELECT id FROM market.posting_chunk
ORDER BY embedding <=> (SELECT embedding FROM market.posting_chunk LIMIT 1) LIMIT 3;
-- "Index Scan using posting_chunk_embedding_idx" 가 나와야 정상
-- "Seq Scan" 이면 ANALYZE 를 안 돌렸거나 random_page_cost 가 기본값입니다
\`\`\`

## 도커로 띄운다면 shm_size 도 필요합니다

인덱스를 직접 다시 만들 일이 있으면, 도커 기본 \`/dev/shm\`(64MB)에서 병렬 빌드가
터집니다 (\`could not resize shared memory segment ... No space left on device\`).
compose 에 \`shm_size: 1gb\` 를 넣거나, 세션에서
\`SET max_parallel_maintenance_workers = 0\` 으로 직렬 빌드하세요.
직렬이어도 청크 14,322개 기준 27초입니다.

## 복원 검증

행 수가 아래와 같아야 합니다.

| 테이블 | 행 수 |
|---|---|
${COUNTS}

\`\`\`sql
SELECT 'tech_field' t, COUNT(*) FROM market.tech_field
UNION ALL SELECT 'company', COUNT(*) FROM market.company
UNION ALL SELECT 'company_source', COUNT(*) FROM market.company_source
UNION ALL SELECT 'job_posting', COUNT(*) FROM market.job_posting
UNION ALL SELECT 'posting_skill', COUNT(*) FROM market.posting_skill
UNION ALL SELECT 'posting_chunk', COUNT(*) FROM market.posting_chunk
UNION ALL SELECT 'skill', COUNT(*) FROM market.skill
UNION ALL SELECT 'skill_alias', COUNT(*) FROM market.skill_alias
UNION ALL SELECT 'skill_field', COUNT(*) FROM market.skill_field
UNION ALL SELECT 'crawl_run', COUNT(*) FROM market.crawl_run
ORDER BY 1;
\`\`\`

벡터가 살아 있는지도 확인하세요. 각각 **${VEC_CHUNK}**, **${VEC_COMPANY}** 여야 합니다.

\`\`\`sql
SELECT COUNT(embedding) FROM market.posting_chunk;
SELECT COUNT(profile_embedding) FROM market.company;

-- 벡터 연산자가 실제로 동작하는지 (pgvector 설치 확인)
SELECT id FROM market.posting_chunk
WHERE embedding IS NOT NULL
ORDER BY embedding <=> (SELECT embedding FROM market.posting_chunk
                        WHERE embedding IS NOT NULL LIMIT 1)
LIMIT 3;
\`\`\`

## 알아둘 것

- **벡터는 gemini-embedding-2 · 1024차원**입니다 (outputDimensionality=1024, L2 정규화됨).
  다른 임베딩 모델로 만든 벡터와 섞으면 안 됩니다.
  질의 임베딩도 같은 모델로 만들어야 검색이 성립합니다.
- **HNSW 인덱스는 덤프에 포함**되어 있어 복원 시 같이 만들어집니다. 데이터가 많아
  복원이 느리면 인덱스를 빼고(\`--section=pre-data --section=data\`) 나중에 만드세요.
- \`crawl_run\` 은 실행 이력이라 FK 가 없습니다. 필요 없으면 통째로 비워도 됩니다.
- \`raw_fields\` (jsonb) 에 컬럼으로 안 옮긴 원본이 들어 있습니다. 나중에 컬럼을 늘릴 때
  재수집 없이 채울 수 있는 근거입니다.
- 스키마 상세(컬럼별 의미·제약·인덱스)는 별도 레퍼런스 문서를 참고하세요.
MD

# ── 6. 압축 — 이 파일 하나만 넘기면 된다 ────────────────────────────────
ZIP="market-handoff-${STAMP}.zip"
rm -f "$ZIP"
if command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -Command \
    "Compress-Archive -Path '${OUT}\\*' -DestinationPath '${ZIP}' -Force" >/dev/null
elif command -v zip >/dev/null 2>&1; then
  (cd "$OUT" && zip -qr "../$ZIP" .)
else
  tar czf "${ZIP%.zip}.tar.gz" -C "$OUT" .
  ZIP="${ZIP%.zip}.tar.gz"
fi

echo
echo "════════════════════════════════════════════════════════"
echo "  넘길 파일:  $ZIP  ($(du -h "$ZIP" | cut -f1))"
echo "════════════════════════════════════════════════════════"
echo
echo "  안에 들어있는 것"
ls -lh "$OUT" | tail -n +2 | awk '{printf "    %-30s %s\n", $9, $5}'
echo
echo "  alembic 리비전 : $ALEMBIC"
echo "  벡터           : posting_chunk $VEC_CHUNK · company $VEC_COMPANY"
echo
echo "  → 이 zip 하나만 백엔드팀에 보내면 됩니다."
echo "    복원 방법은 안에 든 MANIFEST.md 에 다 적혀 있습니다."
