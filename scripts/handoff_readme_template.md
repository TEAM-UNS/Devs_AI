# market 스키마 인계 문서

채용 공고 수집 데이터입니다. **PostgreSQL 스키마 `market` 의 테이블 10개**를
그쪽 DB 에 이식합니다. 기존 테이블(`public` 등)은 건드리지 않습니다.

이 문서는 위에서 아래로 순서대로 실행하면 되도록 썼습니다.

---

## 0. 데이터 개요

| 테이블 | 행 | 역할 |
|---|---:|---|
| `job_posting` | __JOB_POSTING__ | 공고 본문·조건·연봉. **중심 테이블** |
| `posting_skill` | __POSTING_SKILL__ | 공고 ↔ 요구 기술 (등급 포함) |
| `posting_chunk` | __POSTING_CHUNK__ | 임베딩 벡터 (1024차원) |
| `company` | __COMPANY__ | 기업 마스터 |
| `company_source` | __COMPANY_SOURCE__ | 사이트별 기업 식별자 |
| `skill` | __SKILL__ | 스킬 사전 |
| `skill_alias` | __SKILL_ALIAS__ | 스킬 표기 변형 |
| `skill_field` | __SKILL_FIELD__ | 스킬 ↔ 분야 (다대다) |
| `tech_field` | __TECH_FIELD__ | 기술 분야 8종 |
| `crawl_run` | __CRAWL_RUN__ | 수집 실행 이력 |

수집처: 사람인 · 원티드 · 점핏 (잡코리아는 보류).
임베딩 모델: **voyage-3 · 1024차원 · 코사인 거리**.

---

## 1. 사전 확인

```sql
SELECT version();
SELECT name, default_version, installed_version
FROM pg_available_extensions WHERE name IN ('vector', 'pg_trgm');
```

| `vector` 조회 결과 | 다음 단계 |
|---|---|
| `installed_version` 에 값 있음 | **3번으로** |
| 행은 나오는데 `installed_version` 비어 있음 | **2-B 로** |
| 행이 안 나옴 | **2-A 로** |

필요한 확장은 **`vector` 와 `pg_trgm` 둘뿐**입니다.
(`pg_trgm` 은 회사명 부분검색 인덱스용. PostgreSQL 기본 포함이라 항상 설치 가능합니다.)

---

## 2-A. pgvector 설치

### docker-compose 를 쓰는 경우

`docker-compose.yml` 에서 **세 줄**을 바꿉니다.

```yaml
services:
  db:                                                    # 서비스 이름은 기존 것 유지
    image: pgvector/pgvector:pg16                        # ① postgres:16 → 교체
    shm_size: 1gb                                        # ② 추가
    command: ["postgres", "-c", "random_page_cost=1.1"]  # ③ 추가
    volumes:
      - pgdata:/var/lib/postgresql/data                  # 기존 볼륨 그대로

volumes:
  pgdata:
```

```bash
docker compose up -d db
```

- 태그(`pg16`)는 쓰는 PostgreSQL 메이저 버전에 맞추세요. `pg13`~`pg17` 이 있습니다.
- **기존 볼륨을 그대로 쓰므로 현재 데이터는 삭제되지 않습니다.** 이미지만 교체됩니다.
- `pgvector/pgvector` 는 공식 `postgres` 이미지에 확장만 얹은 것이라 나머지 동작은 동일합니다.

②③ 이 왜 필요한지는 **8번 "반드시 지킬 설정 2개"** 에 있습니다. 생략하면
조용히 문제가 생깁니다.

### 관리형 DB 인 경우

| 서비스 | 절차 |
|---|---|
| AWS RDS / Aurora | 파라미터 그룹 수정 불필요. `rds_superuser` 계정으로 2-B 실행. 안 되면 엔진 마이너 버전 업그레이드 |
| GCP Cloud SQL | 플래그 불필요. 2-B 바로 실행 |
| Azure Flexible Server | 서버 매개 변수 `azure.extensions` 에 `VECTOR` 추가 후 저장 → 2-B |

Azure CLI:
```bash
az postgres flexible-server parameter set \
  --resource-group <rg> --server-name <server> \
  --name azure.extensions --value vector
```

### 직접 설치한 PostgreSQL

```bash
sudo apt install postgresql-16-pgvector     # Ubuntu/Debian (버전 숫자 맞추기)
sudo dnf install pgvector_16                # RHEL/Rocky/Alma
brew install pgvector                       # macOS
```

패키지가 없으면 소스 빌드:
```bash
sudo apt install build-essential postgresql-server-dev-16
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
cd pgvector && make && sudo make install
```

---

## 2-B. 확장 활성화

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

검증:
```sql
SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pg_trgm');
SELECT '[1,2,3]'::vector <=> '[3,2,1]'::vector;   -- 0.2 근처 값이면 정상
```

> **이 설정은 `docker compose down` 으로 사라지지 않습니다.**
> 확장은 DB 안에 저장되고, DB 는 볼륨에 있습니다. 컨테이너를 지워도 볼륨은 남습니다.
> **단 `docker compose down -v` 는 볼륨까지 삭제합니다. `-v` 를 붙이지 마세요.**

---

## 3. 트리거 함수 (schema.sql 보다 **먼저**)

```sql
CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;
```

`schema.sql` 의 트리거 3개(`company_touch`, `job_posting_touch`, `skill_touch`)가
이 함수를 참조합니다. 함수 자체는 `public` 스키마 소속이라 덤프에 포함되지 않습니다.
**먼저 만들지 않으면 `schema.sql` 마지막에 트리거 3개가 실패합니다.**
(데이터는 들어가지만 이후 UPDATE 에서 `updated_at` 이 갱신되지 않습니다.)

---

## 4. 테이블 생성

```bash
psql -U <user> -d <db> -f schema.sql
```

테이블 10개 + 인덱스 37개 + CHECK 제약 + 외래키 9개 + 트리거 3개가 생성됩니다.
`CREATE SCHEMA market;` 도 이 파일에 포함되어 있습니다.

---

## 5. CSV 적재

```bash
psql -U <user> -d <db> -f load.sql     # CSV 파일이 있는 폴더에서 실행
```

`load.sql` 은 외래키 순서대로 넣고 시퀀스까지 보정합니다.
직접 적재한다면 **아래 순서를 지켜야 합니다** (부모 → 자식):

```
tech_field → company → company_source → skill → skill_alias
→ skill_field → job_posting → posting_skill → posting_chunk → crawl_run
```

적재 후 시퀀스 보정을 빠뜨리면 다음 INSERT 에서 PK 충돌이 납니다:

```sql
SELECT setval('market.job_posting_id_seq',   (SELECT MAX(id) FROM market.job_posting));
SELECT setval('market.company_id_seq',       (SELECT MAX(id) FROM market.company));
SELECT setval('market.posting_chunk_id_seq', (SELECT MAX(id) FROM market.posting_chunk));
-- company_source, skill, skill_alias, tech_field, crawl_run 도 동일
```

**CSV 형식**: UTF-8, 헤더 포함, RFC4180 표준 인용.
`description` 등 텍스트 컬럼에 **줄바꿈이 포함**되어 있습니다(따옴표 안). 정상입니다 —
`wc -l` 로 행을 세면 실제 레코드 수보다 크게 나옵니다.
벡터는 `[0.1,0.2,...]` 문자열로 저장되어 있고 pgvector 가 그대로 파싱합니다.

---

## 6. 통계 수집 (필수)

```sql
ANALYZE market.job_posting;
ANALYZE market.posting_skill;
ANALYZE market.posting_chunk;
ANALYZE market.company;
```

**생략하면 벡터 인덱스가 있어도 사용되지 않습니다.**

---

## 7. 검증

### 행 수

```sql
SELECT 'job_posting' t, COUNT(*) FROM market.job_posting
UNION ALL SELECT 'posting_skill',  COUNT(*) FROM market.posting_skill
UNION ALL SELECT 'posting_chunk',  COUNT(*) FROM market.posting_chunk
UNION ALL SELECT 'company',        COUNT(*) FROM market.company
UNION ALL SELECT 'company_source', COUNT(*) FROM market.company_source
UNION ALL SELECT 'skill',          COUNT(*) FROM market.skill
UNION ALL SELECT 'skill_alias',    COUNT(*) FROM market.skill_alias
UNION ALL SELECT 'skill_field',    COUNT(*) FROM market.skill_field
UNION ALL SELECT 'tech_field',     COUNT(*) FROM market.tech_field
UNION ALL SELECT 'crawl_run',      COUNT(*) FROM market.crawl_run
ORDER BY 1;
```

0번 표의 값과 일치해야 합니다.

### 벡터

```sql
SELECT COUNT(embedding) FROM market.posting_chunk;       -- __VEC_CHUNK__
SELECT COUNT(profile_embedding) FROM market.company;     -- __VEC_COMPANY__
```

### 인덱스 사용 여부 — **가장 중요**

```sql
EXPLAIN SELECT id FROM market.posting_chunk
ORDER BY embedding <=> (SELECT embedding FROM market.posting_chunk LIMIT 1) LIMIT 5;
```

- `Index Scan using posting_chunk_embedding_idx` → 정상
- `Seq Scan` → 6번 ANALYZE 를 안 했거나 `random_page_cost` 가 기본값(4.0)

### 트리거

```sql
SELECT COUNT(*) FROM pg_trigger WHERE NOT tgisinternal;   -- 3
```

---

## 8. 반드시 지킬 설정 2개

인계하는 쪽에서 실제로 겪은 문제입니다. **둘 다 조용히 잘못되는 종류**라 미리 알려드립니다.

### ① `random_page_cost = 1.1`

pgvector 의 HNSW 인덱스는 시작비용 추정이 큽니다. PostgreSQL 기본값 `4.0` 에서는
플래너가 **순차 스캔이 더 싸다고 오판**합니다.

| | 실행 시간 (청크 14,322개 실측) |
|---|---|
| 순차 스캔 (기본값에서 플래너가 선택) | **61.1 ms** |
| HNSW 인덱스 | **0.8 ms** |

61배 차이인데 **결과는 똑같이 맞습니다.** 그래서 아무도 눈치채지 못합니다.
SSD 환경이면 1.1 이 어차피 권장값입니다.

```sql
ALTER SYSTEM SET random_page_cost = 1.1;
SELECT pg_reload_conf();
```

docker-compose 면 `command: ["postgres", "-c", "random_page_cost=1.1"]`.

### ② `shm_size: 1gb` (도커인 경우)

인덱스를 재생성할 때 이 오류가 납니다:

```
ERROR: could not resize shared memory segment to 533794304 bytes:
       No space left on device
```

디스크가 아니라 **공유 메모리(`/dev/shm`)** 입니다. 도커 기본값이 64MB 인데
병렬 인덱스 빌드가 `maintenance_work_mem` 만큼을 공유 메모리로 요구합니다.

- compose 에 `shm_size: 1gb` 추가, 또는
- 세션에서 직렬 빌드: `SET max_parallel_maintenance_workers = 0;`
  (직렬이어도 청크 14,322개에 27초)

---

## 9. 테이블 관계

```
tech_field ──< job_posting >── company ──< company_source
    │              │  │
    │              │  └──< posting_chunk        (벡터)
    │              └─────< posting_skill >── skill ──< skill_alias
    └──────────────────< skill_field >────────┘

crawl_run                                       (독립. FK 없음)
```

### 외래키 9개

| 자식 | 컬럼 | → 부모 | ON DELETE |
|---|---|---|---|
| `company_source` | `company_id` | `company.id` | **CASCADE** |
| `job_posting` | `company_id` | `company.id` | **SET NULL** |
| `job_posting` | `field_id` | `tech_field.id` | **SET NULL** |
| `posting_skill` | `posting_id` | `job_posting.id` | **CASCADE** |
| `posting_skill` | `skill_id` | `skill.id` | **CASCADE** |
| `posting_chunk` | `posting_id` | `job_posting.id` | **CASCADE** |
| `skill_alias` | `skill_id` | `skill.id` | **CASCADE** |
| `skill_field` | `skill_id` | `skill.id` | **CASCADE** |
| `skill_field` | `field_id` | `tech_field.id` | **CASCADE** |

**삭제 규칙이 두 갈래인 이유**
- `CASCADE` — 부모가 없으면 존재 의미가 없는 것 (공고의 스킬·청크, 스킬의 별칭)
- `SET NULL` — 참조가 사라져도 본체는 살아야 하는 것. 회사를 지운다고 공고까지
  날아가면 안 됩니다. `company_id` 만 NULL 이 되고 `company_name_raw` 에 원본
  회사명이 남아 복구할 수 있습니다.

`crawl_run` 은 의도적으로 FK 가 없습니다. 실행 이력이라 데이터가 지워져도 남아야 하고,
반대로 이력이 지워져도 데이터는 멀쩡해야 합니다.

### UNIQUE 제약 = upsert 충돌 키

| 테이블 | 키 | 의미 |
|---|---|---|
| `job_posting` | `(source, source_job_id)` | 사이트 + 그 사이트의 공고번호 |
| `company` | `name_key` | **사이트 간 기업 병합 지점** |
| `company_source` | `(source, source_company_id)` | 같은 사이트의 같은 기업은 1행 |
| `posting_chunk` | `(posting_id, section, seq)` | 청크 좌표 |
| `skill` | `name` | 정규 표기 |
| `skill_alias` | `alias` | **전역 유니크.** 두 스킬이 같은 별칭 불가 |

### 복합 기본키 2개 (대리키 없음)

```
posting_skill  PK (posting_id, skill_id)
skill_field    PK (skill_id, field_id)
```

순수 연결 테이블이라 `id` 컬럼이 없습니다.

---

## 10. 컬럼 레퍼런스

### `job_posting` — 30컬럼 (중심 테이블)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | bigint | PK |
| `source` | varchar(20) | `saramin` `jobkorea` `wanted` `jumpit` (CHECK) |
| `source_job_id` | varchar(100) | 사이트의 공고번호 |
| `company_id` | bigint | → `company.id` |
| `field_id` | integer | → `tech_field.id`. **분류 실패 시 NULL 유지** |
| `title` | varchar(300) | 공고 제목 |
| `company_name_raw` | varchar(200) | 정규화 전 회사명 (병합 오류 추적용) |
| `tags_raw` | jsonb | 사이트가 준 태그 **원본** 배열 |
| `career_min` / `career_max` | integer | 신입=0, 무관=NULL. `min ≤ max` CHECK |
| `employment_type` | varchar(30) | 정규직 · 계약직 등 |
| `education` | varchar(30) | 학력무관 · 대졸 이상 등 |
| `location` | varchar(120) | 근무지 |
| `description` | text | **본문 전문.** 추출 실패 시 NULL |
| `welfare` | text | 복지 + 전형절차. **임베딩 대상 아님** |
| `salary_raw` | text | 원문 표기 그대로 |
| `salary_min` / `salary_max` | integer | **항상 연봉 만원 단위.** `min ≤ max` CHECK |
| `salary_type` | varchar(16) | `range` `min_only` `max_only` `negotiable` `unknown` |
| `salary_period` | varchar(8) | `annual` `monthly` `hourly` 또는 NULL. **원문 기준** |
| `body_is_image` | boolean | 본문이 이미지 한 장 (원래 텍스트가 없는 공고) |
| `body_extract_failed` | boolean | 파서가 본문을 못 건짐 (재시도 대상) |
| `posted_at` / `expires_at` | timestamptz | 게시일 / 마감일 |
| `content_hash` | char(64) | 변경 감지용 sha256 |
| `embed_hash` | char(64) | 재임베딩 판단용 |
| `collected_at` | timestamptz | 마지막 수집 시각 |
| `raw_fields` | jsonb | url · job_categories · locations 등 |
| `created_at` / `updated_at` | timestamptz | `updated_at` 은 트리거가 갱신 |

**해시가 두 개인 이유**: 하나로 합칠 수 없습니다. "내용은 그대로인데 임베딩만 아직"
이라는 상태를 표현해야 하기 때문입니다.
`embed_hash IS DISTINCT FROM content_hash` 가 곧 임베딩 대기열입니다.

### `posting_skill` — 4컬럼

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `posting_id` | bigint | PK, → `job_posting.id` |
| `skill_id` | integer | PK, → `skill.id` |
| `requirement` | varchar(16) | `tag` `required` `preferred` `body` (CHECK) |
| `mentions` | integer | 본문 내 등장 횟수 |

**`requirement` 4등급이 이 테이블의 핵심입니다.** 신뢰도 순서는
`tag` > `required` > `preferred` > `body`.

| 용도 | 사용할 등급 |
|---|---|
| "이 기술이 실제로 요구되는가" (갭 분석) | `required` + `tag` **만** |
| 인기·급상승 기술 순위 | `required` + `preferred` + `tag` (`body` 제외) |
| "이 회사가 쓰는 스택" | **전부** (`body` 포함) |

`body` 는 "우리는 AWS 위에서 운영합니다" 같은 단순 언급입니다. 요구사항은 아니지만
그 회사의 기술 정보로는 유효해서 버리지 않고 등급만 낮춰 담았습니다.

### `posting_chunk` — 9컬럼 (벡터)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | bigint | PK |
| `posting_id` | bigint | → `job_posting.id` |
| `section` | varchar(20) | `responsibility` `required` `preferred` **3종뿐** (CHECK) |
| `seq` | integer | 섹션이 1,200자 초과 시 분할 번호 |
| `content` | text | 임베딩에 실제로 넣은 텍스트 |
| `chunk_hash` | char(64) | `sha256(section + 정규화 content)` |
| `embedding` | vector(1024) | voyage-3 · 코사인 |
| `token_count` | integer | 근사치 |
| `created_at` | timestamptz | |

공고 하나가 최대 3행입니다. 본문을 통째로 벡터 하나로 만들면 주요업무·자격요건·복지가
평균 나서 "그냥 채용공고" 라는 밋밋한 벡터가 됩니다. **복지·전형절차는 CHECK 제약으로
아예 들어올 수 없습니다** — 어느 회사나 비슷해서 벡터 공간에서 전부 뭉칩니다.

### `company` — 17컬럼

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | bigint | PK |
| `name_key` | varchar(200) | **UNIQUE. 병합 키.** 괄호·"주식회사"·공백 제거 |
| `name` | varchar(200) | 표시용 원본 이름 |
| `description` / `business_content` / `talent_profile` | text | 기업소개 / 사업내용 / 인재상 |
| `size_type` | varchar(20) | `startup` `small` `medium` `large` `enterprise` `unknown` |
| `employee_count` | integer | `≥ 0` CHECK |
| `industry` | varchar(120) | 업종 |
| `founded` | varchar(20) | 설립일. 표기가 제각각이라 문자열 |
| `revenue` | bigint | 매출액 (원 단위) |
| `homepage` | varchar(500) | |
| `profile_embedding` | vector(1024) | `description + business_content + industry` 합친 벡터 |
| `embed_hash` | char(64) | 위 3필드 변경 감지 |
| `raw_fields` | jsonb | |
| `created_at` / `updated_at` | timestamptz | |

`(주)네이버제트` → `name_key = 네이버제트`. 이걸로 세 사이트에 흩어진 같은 회사를
하나로 묶습니다.

### 나머지 6개 테이블

| 테이블 | 컬럼 |
|---|---|
| `company_source` | `id` · `company_id` · `source` · `source_company_id` · `url` · `collected_at` |
| `skill` | `id` · `name`(UNIQUE) · `category` · `is_ambiguous` · `is_common` · `embedding` · `created_at` · `updated_at` |
| `skill_alias` | `id` · `skill_id` · `alias`(UNIQUE) · `case_sensitive` |
| `skill_field` | `skill_id` · `field_id` (복합 PK) |
| `tech_field` | `id` · `code`(UNIQUE) · `name` · `sort_order` · `created_at` |
| `crawl_run` | `id` · `kind` · `source` · `keyword` · `status` · `fetched` · `inserted` · `updated` · `skipped` · `embedded` · `errors` · `message` · `started_at` · `finished_at` |

**`skill.is_common`** — Git · Jira · Slack · Notion · Confluence **5개만** true 입니다.
전 직군 공통 도구라 트렌드 집계에서 제외하는 용도입니다. 안 빼면 Git 이 1위를
차지하는데 그건 아무 정보가 아닙니다.

**`skill.is_ambiguous`** — Go · C · R 처럼 일상 단어와 겹치는 것들.
매칭 위치 ±40자에 문맥 단서가 있을 때만 채택했습니다.

**`skill_alias.case_sensitive`** — true 면 대소문자 일치 필수.
`CAN`(차량 통신 규격)이 영어 문장의 `can` 에, `ES`(Elasticsearch)가 `es` 에
걸리는 오탐을 차단합니다.

**`tech_field`** — `backend` `frontend` `mobile` `data_ai` `devops` `security`
`game` `embedded` 8종. **`etc` 버킷이 없습니다.** 분류 실패는 `field_id = NULL` 로
두어 "매핑 실패" 와 "진짜 기타 직무" 가 섞이지 않게 했습니다.

---

## 11. 쿼리 작성 시 주의

```sql
-- 본문이 없는 공고는 집계에서 제외
WHERE body_is_image = false AND body_extract_failed = false AND description IS NOT NULL

-- 연봉 통계: 금액이 공개된 것만, 시급 제외
WHERE salary_type IN ('range','min_only','max_only')
  AND salary_period IS DISTINCT FROM 'hourly'
  -- salary_min/max 는 이미 "연봉 만원" 으로 정규화되어 있음

-- "실제로 요구되는 기술" 집계
WHERE ps.requirement IN ('required','tag') AND NOT s.is_common

-- 벡터 검색 (질의도 voyage-3 로 임베딩해야 함)
ORDER BY pc.embedding <=> $1::vector LIMIT 20
```

- `salary_period = 'hourly'` 는 근무시간을 몰라 연환산이 불가능합니다. 통계에서 빼세요.
- 벡터 검색의 질의 임베딩은 **반드시 같은 모델(voyage-3, 1024차원)** 이어야 합니다.
  다른 모델 벡터와는 비교 자체가 성립하지 않습니다.
- `skill.embedding` 은 비어 있습니다(챗봇 작업에서 채울 예정). 인덱스도 없습니다.
