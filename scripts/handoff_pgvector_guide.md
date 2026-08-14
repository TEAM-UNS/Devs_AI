# pgvector 설치 가이드

이 문서는 `market-handoff-*.zip` 에 함께 들어갑니다. 받는 쪽(백엔드팀)이 읽는 문서입니다.

---

## 이게 왜 필요한가

넘겨받는 데이터에는 채용공고 본문을 벡터로 바꾼 **임베딩 14,322개**가 들어 있습니다.
"기기랑 앱 통신 만드는 일" 같은 문장으로 검색하면 그 단어가 없는 공고까지 의미로 찾아내는
기능이 여기서 나옵니다. 이걸 하려면 PostgreSQL 에 `pgvector` 확장이 필요합니다.

**pgvector 없이도 지금 당장 복원은 됩니다** (`_no-pgvector.dump` 사용). 공고·기업·스킬
데이터는 전부 정상 동작하고, 벡터 검색만 못 씁니다. 벡터는 `real[]` 배열로 보존되어
있어서 나중에 설치하면 `01-restore-vectors.sql` 한 번으로 되돌아갑니다. 재임베딩은
필요 없습니다.

---

## 0단계 — 먼저 이것부터 실행하세요

**환경을 추측하지 말고 DB 에 직접 물어봅니다.**

```sql
SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name = 'vector';
```

결과로 갈립니다.

| 결과 | 상태 | 할 일 |
|---|---|---|
| `installed_version` 에 값이 있음 | **이미 설치됨** | 할 것 없음. `market_*.dump` 로 복원 |
| 행은 나오는데 `installed_version` 이 비어 있음 | **설치 가능** | `CREATE EXTENSION vector;` 한 줄이면 끝 (A) |
| **행이 안 나옴** | 서버에 바이너리가 없음 | 아래 환경별 절차 (B~F) |

### 겸사겸사 확인할 것

```sql
SELECT version();                          -- PostgreSQL 13 이상이어야 합니다
SELECT current_user, session_user;         -- 확장 설치는 보통 슈퍼유저/rds_superuser 권한 필요
```

---

## A. 행이 나오고 `installed_version` 이 비어 있을 때

```sql
CREATE EXTENSION vector;
```

끝입니다. **[검증](#검증)** 으로 가세요.

권한 오류(`permission denied to create extension "vector"`)가 나면 DBA 나 슈퍼유저 계정으로
같은 명령을 실행하면 됩니다. 확장은 DB 당 한 번만 만들면 되고, 그 뒤로는 일반 유저도 씁니다.

---

## B. 도커 / docker-compose

이미지만 바꾸면 됩니다. `pgvector/pgvector` 는 공식 postgres 이미지에 pgvector 만 얹은
것이라 나머지는 동일합니다.

```yaml
services:
  postgres:
    # image: postgres:16          ← 이걸
    image: pgvector/pgvector:pg16 # ← 이걸로
    shm_size: 1gb                 # ★ 아래 "흔한 사고" 참고
    command:
      - "postgres"
      - "-c"
      - "random_page_cost=1.1"    # ★ 아래 "흔한 사고" 참고
```

```bash
docker compose up -d postgres
```

데이터 볼륨은 그대로 유지되므로 기존 데이터는 안 날아갑니다. 그다음:

```sql
CREATE EXTENSION vector;
```

> 태그는 PG 메이저 버전에 맞추세요: `pg13` `pg14` `pg15` `pg16` `pg17`

---

## C. AWS RDS / Aurora PostgreSQL

**파라미터 그룹을 건드릴 필요가 없습니다.** 바이너리가 이미 올라가 있어서
`CREATE EXTENSION` 만 하면 됩니다.

```sql
CREATE EXTENSION vector;
```

`rds_superuser` 권한이 있는 계정으로 실행하세요 (RDS 마스터 계정이 기본으로 갖고 있습니다).

행이 안 나온다면 엔진 버전이 낮은 것입니다. `SELECT version();` 으로 확인하고,
낮으면 마이너 버전 업그레이드가 필요합니다. RDS 콘솔의 **수정 → DB 엔진 버전** 에서
최신 마이너로 올린 뒤 다시 시도하세요. pgvector 는 PG 13 계열부터 지원되지만
**정확한 최소 마이너 버전은 리전·엔진마다 다르므로**, 아래 쿼리로 실제 확인하는 게 확실합니다.

```sql
-- 이 서버에서 쓸 수 있는 확장 목록에 vector 가 있는지
SELECT * FROM pg_available_extensions WHERE name = 'vector';
```

---

## D. GCP Cloud SQL for PostgreSQL

```sql
CREATE EXTENSION vector;
```

역시 플래그 설정 없이 바로 됩니다. 안 되면 인스턴스의 PostgreSQL 버전을 확인하세요.
Cloud SQL 콘솔 → 인스턴스 → **편집 → 데이터베이스 버전** 입니다.

---

## E. Azure Database for PostgreSQL (Flexible Server)

**여기만 서버 파라미터를 먼저 바꿔야 합니다.**

1. Azure Portal → 해당 서버 → **설정 → 서버 매개 변수**
2. `azure.extensions` 검색
3. 목록에서 **VECTOR** 체크 (기존 값은 지우지 말고 추가)
4. **저장**. 재시작은 보통 필요 없습니다

그다음:

```sql
CREATE EXTENSION vector;
```

CLI 로 하려면:

```bash
az postgres flexible-server parameter set \
  --resource-group <rg> --server-name <server> \
  --name azure.extensions --value vector
```

---

## F. 직접 설치한 PostgreSQL (Ubuntu / Debian / RHEL / macOS)

### 패키지로 (가장 쉬움)

```bash
# Ubuntu / Debian — PG 메이저 버전에 맞춰 숫자를 바꾸세요
sudo apt update
sudo apt install postgresql-16-pgvector

# RHEL / Rocky / Alma
sudo dnf install pgvector_16

# macOS (Homebrew)
brew install pgvector
```

### 소스에서 빌드

패키지가 없을 때만. `pg_config` 가 PATH 에 있어야 합니다.

```bash
sudo apt install build-essential postgresql-server-dev-16   # 빌드 도구
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
cd pgvector
make
sudo make install
```

설치 후 재시작은 필요 없고, DB 에 접속해서:

```sql
CREATE EXTENSION vector;
```

---

## 검증

```sql
-- 1) 확장이 올라왔는지
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';

-- 2) 벡터 타입과 거리 연산자가 실제로 동작하는지
SELECT '[1,2,3]'::vector <=> '[3,2,1]'::vector AS 코사인거리;
-- 0.2 근처 값이 나오면 정상입니다
```

둘 다 되면 `01-restore-vectors.sql` 을 실행해 벡터를 되돌리거나,
처음부터 `market_*.dump` 로 복원하면 됩니다.

---

## 흔한 사고 두 가지

넘기는 쪽에서 실제로 겪은 것들입니다. 둘 다 **조용히 잘못되는** 종류라 미리 알려드립니다.

### ① 인덱스를 만들어 놓고도 안 씁니다 — `random_page_cost`

pgvector 의 HNSW 인덱스는 시작비용 추정이 큽니다. PostgreSQL 기본값
`random_page_cost = 4.0` 에서는 플래너가 **순차 스캔이 더 싸다고 오판**합니다.

실측 (청크 14,322개 기준):

| | 실행 시간 |
|---|---|
| 순차 스캔 (기본값에서 플래너가 고르는 것) | **61.1 ms** |
| HNSW 인덱스 | **0.8 ms** |

**61배 차이인데 결과는 똑같이 맞습니다.** 그래서 아무도 모르고 지나갑니다.
SSD 환경이면 1.1 이 어차피 권장값입니다.

```sql
ALTER SYSTEM SET random_page_cost = 1.1;
SELECT pg_reload_conf();
```

확인:

```sql
EXPLAIN SELECT id FROM market.posting_chunk
ORDER BY embedding <=> (SELECT embedding FROM market.posting_chunk LIMIT 1) LIMIT 3;
-- "Index Scan using posting_chunk_embedding_idx" 가 나와야 정상
-- "Seq Scan" 이면 위 설정을 안 했거나 ANALYZE 를 안 돌린 것
```

### ② 도커에서 인덱스 생성이 실패합니다 — `shm_size`

```
ERROR: could not resize shared memory segment to 533794304 bytes:
       No space left on device
```

디스크가 아니라 **공유 메모리(`/dev/shm`)** 입니다. 도커 기본값이 64MB 인데 병렬
인덱스 빌드가 `maintenance_work_mem` 만큼(예: 512MB)을 공유 메모리로 요구합니다.

- compose 에 `shm_size: 1gb` 추가, 또는
- 세션에서 직렬 빌드: `SET max_parallel_maintenance_workers = 0;`

직렬이어도 청크 14,322개에 27초입니다.

---

## 참고

- 벡터는 **gemini-embedding-2 · 1024차원** 입니다. 검색 질의도 같은 모델로 임베딩해야
  결과가 성립합니다. 다른 모델 벡터와 섞으면 안 됩니다.
- 공식 문서: https://github.com/pgvector/pgvector
