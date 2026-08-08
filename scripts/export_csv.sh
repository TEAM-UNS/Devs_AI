#!/usr/bin/env bash
# market 스키마 10개 테이블을 CSV 로 뽑는다.
#
#   bash scripts/export_csv.sh
#
# 결과: csv-export/ + market-csv-YYYYMMDD.zip
#
# CSV 는 UTF-8, 헤더 포함, RFC4180 표준 인용. 엑셀·판다스·COPY 다 읽는다.
# 벡터는 "[0.1,0.2,...]" 문자열로 나간다 (pgvector 가 그대로 파싱하는 형식).
set -euo pipefail

CONTAINER="${CONTAINER:-jobstack-postgres}"
DB="${DB:-jobstack}"
DBUSER="${DBUSER:-jobstack}"
OUT="csv-export"
STAMP="$(date +%Y%m%d)"

TABLES=(
  tech_field company company_source job_posting posting_skill
  posting_chunk skill skill_alias skill_field crawl_run
)

rm -rf "$OUT"; mkdir -p "$OUT"
echo "CSV 추출 중…"

for t in "${TABLES[@]}"; do
  docker exec "$CONTAINER" psql -U "$DBUSER" -d "$DB" \
    -c "\copy (SELECT * FROM market.${t}) TO STDOUT WITH (FORMAT csv, HEADER true, FORCE_QUOTE *)" \
    > "$OUT/${t}.csv"
  # ★ wc -l 로 세면 안 된다. 본문(description 등)에 줄바꿈이 들어 있어서
  #   레코드 하나가 여러 줄을 차지한다. DB 에 직접 묻는다.
  rows=$(docker exec "$CONTAINER" psql -U "$DBUSER" -d "$DB" -tAc \
         "SELECT COUNT(*) FROM market.${t};")
  printf "  %-16s %8s행  %6s\n" "$t" "$rows" "$(du -h "$OUT/${t}.csv" | cut -f1)"
done

# 받는 쪽이 그대로 쓸 수 있는 적재 스크립트
cat > "$OUT/load.sql" <<'SQL'
-- CSV → PostgreSQL 적재.
--
--   psql -U <user> -d <db> -f load.sql
--
-- ★ psql 의 \copy 는 클라이언트 파일을 읽습니다. 이 파일이 있는 폴더에서
--   실행하세요. 서버측 COPY(\copy 아님)를 쓰면 서버 로컬 경로여야 합니다.
--
-- 테이블이 이미 있어야 합니다. 없으면 schema.sql 을 먼저 실행하세요.
-- 순서가 중요합니다 — 외래키 때문에 부모 테이블이 먼저입니다.

\copy market.tech_field     FROM 'tech_field.csv'     WITH (FORMAT csv, HEADER true)
\copy market.company        FROM 'company.csv'        WITH (FORMAT csv, HEADER true)
\copy market.company_source FROM 'company_source.csv' WITH (FORMAT csv, HEADER true)
\copy market.skill          FROM 'skill.csv'          WITH (FORMAT csv, HEADER true)
\copy market.skill_alias    FROM 'skill_alias.csv'    WITH (FORMAT csv, HEADER true)
\copy market.skill_field    FROM 'skill_field.csv'    WITH (FORMAT csv, HEADER true)
\copy market.job_posting    FROM 'job_posting.csv'    WITH (FORMAT csv, HEADER true)
\copy market.posting_skill  FROM 'posting_skill.csv'  WITH (FORMAT csv, HEADER true)
\copy market.posting_chunk  FROM 'posting_chunk.csv'  WITH (FORMAT csv, HEADER true)
\copy market.crawl_run      FROM 'crawl_run.csv'      WITH (FORMAT csv, HEADER true)

-- 시퀀스를 데이터 최대값 뒤로 옮깁니다. 안 하면 다음 INSERT 가 PK 충돌합니다.
SELECT setval('market.tech_field_id_seq',     COALESCE((SELECT MAX(id) FROM market.tech_field), 1));
SELECT setval('market.company_id_seq',        COALESCE((SELECT MAX(id) FROM market.company), 1));
SELECT setval('market.company_source_id_seq', COALESCE((SELECT MAX(id) FROM market.company_source), 1));
SELECT setval('market.skill_id_seq',          COALESCE((SELECT MAX(id) FROM market.skill), 1));
SELECT setval('market.skill_alias_id_seq',    COALESCE((SELECT MAX(id) FROM market.skill_alias), 1));
SELECT setval('market.job_posting_id_seq',    COALESCE((SELECT MAX(id) FROM market.job_posting), 1));
SELECT setval('market.posting_chunk_id_seq',  COALESCE((SELECT MAX(id) FROM market.posting_chunk), 1));
SELECT setval('market.crawl_run_id_seq',      COALESCE((SELECT MAX(id) FROM market.crawl_run), 1));

ANALYZE market.job_posting;
ANALYZE market.posting_skill;
ANALYZE market.posting_chunk;
ANALYZE market.company;
SQL

# 테이블을 만들 스키마 DDL 도 같이 넣는다 (CSV 만으로는 테이블이 안 생긴다)
docker exec "$CONTAINER" pg_dump -U "$DBUSER" -d "$DB" \
  --schema-only --schema=market --no-owner --no-privileges \
  > "$OUT/schema.sql"


# ── 인계 문서 — 템플릿의 __PLACEHOLDER__ 를 실제 행 수로 치환한다 ──────
TEMPLATE="$(dirname "$0")/handoff_readme_template.md"
if [ -f "$TEMPLATE" ]; then
  cnt() { docker exec "$CONTAINER" psql -U "$DBUSER" -d "$DB" -tAc "SELECT COUNT(*) FROM market.$1;"; }
  vec_chunk=$(docker exec "$CONTAINER" psql -U "$DBUSER" -d "$DB" -tAc \
              "SELECT COUNT(embedding) FROM market.posting_chunk;")
  vec_company=$(docker exec "$CONTAINER" psql -U "$DBUSER" -d "$DB" -tAc \
                "SELECT COUNT(profile_embedding) FROM market.company;")
  sed -e "s/__JOB_POSTING__/$(cnt job_posting)/" \
      -e "s/__POSTING_SKILL__/$(cnt posting_skill)/" \
      -e "s/__POSTING_CHUNK__/$(cnt posting_chunk)/" \
      -e "s/__COMPANY_SOURCE__/$(cnt company_source)/" \
      -e "s/__COMPANY__/$(cnt company)/" \
      -e "s/__SKILL_ALIAS__/$(cnt skill_alias)/" \
      -e "s/__SKILL_FIELD__/$(cnt skill_field)/" \
      -e "s/__SKILL__/$(cnt skill)/" \
      -e "s/__TECH_FIELD__/$(cnt tech_field)/" \
      -e "s/__CRAWL_RUN__/$(cnt crawl_run)/" \
      -e "s/__VEC_CHUNK__/${vec_chunk}/" \
      -e "s/__VEC_COMPANY__/${vec_company}/" \
      "$TEMPLATE" > "$OUT/README.md"
else
  echo "경고: $TEMPLATE 가 없어 인계 문서를 넣지 못했습니다." >&2
fi

ZIP="market-csv-${STAMP}.zip"
rm -f "$ZIP"
if command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -Command \
    "Compress-Archive -Path '${OUT}\\*' -DestinationPath '${ZIP}' -Force" >/dev/null
else
  (cd "$OUT" && zip -qr "../$ZIP" .)
fi

echo
echo "  → $ZIP  ($(du -h "$ZIP" | cut -f1))"
