# ai-service

와!!!!!!!!!!!!!!!

채용 공고 수집 · 임베딩 · 커리어 어시스턴트.
담당 범위: **크롤링·적재**, **임베딩**, **AI 챗봇**.
(유저/인증 발급 · 대시보드 등등 화면 API 는 메인 백엔드 담당)

## 실행

```bash
cp .env.example .env      # 값 채우기
docker compose up -d      # postgres(pgvector) + redis + arq worker
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

`docker compose up -d` 는 워커까지 띄운다(`restart: unless-stopped`). 코드를
고치면서 워커만 따로 돌리고 싶으면 컨테이너를 멈추고 로컬에서 띄운다:

```bash
docker compose stop worker
uv run arq app.worker.WorkerSettings
```

워커 이미지는 소스를 복사해 굽는다. 코드를 고쳤으면 다시 빌드해야 반영된다:

```bash
docker compose up -d --build worker
```

스키마를 처음부터 다시 만들려면:

```bash
docker compose down -v && docker compose up -d
```

### 초기 적재 순서 (★ 지킬 것)

HNSW 인덱스는 **데이터를 넣은 뒤**에 만든다. 빈 테이블에 먼저 걸면 INSERT
마다 그래프를 갱신하느라 초기 적재가 몇 배 느려진다.

```bash
uv run alembic upgrade head                       # ① 스키마 (벡터 인덱스 없음)
uv run python -m scripts.seed_skills              # ② 스킬 사전
uv run python -m app.cli crawl --site jumpit --pages 40   # ③ 수집
uv run python -m app.cli embed                    # ④ 임베딩
uv run python -m app.cli vector-index --build     # ⑤ HNSW 인덱스
```

인덱스가 없어도 벡터 검색은 순차 스캔으로 동작한다. ⑤ 를 잊어도 조용히
틀리지는 않고 느려질 뿐이다.

### CLI

```bash
python -m app.cli crawl --site saramin --keyword 백엔드 --pages 8 --skip-seen-days 7
python -m app.cli embed --limit 100           # 실제 임베딩 API 사용
python -m app.cli embed --fake                # API 키 없이 파이프라인만 확인
python -m app.cli embed --companies           # 기업 프로필 임베딩
python -m app.cli vector-index --build        # 적재 후 HNSW 생성
```

**파서를 고친 뒤 백필** — `--force-reextract` 가 필요하다:

```bash
python -m app.cli crawl --site saramin --keyword 백엔드 --pages 8 \
    --skip-seen-days 0 --force-reextract
```

`content_hash` 는 **사이트가 준 원문으로만** 계산한다. 우리 파서가 바뀐 것은
해시에 반영되지 않으므로(`employment_type` 은 해시에 아예 없다), 그냥
재수집하면 해시가 같아 `touch` 만 하고 지나간다 — "재수집했는데 아무것도 안
바뀜" 이 된다. `--force-reextract` 는 해시가 같아도 다시 적재한다. 본문은
그대로이므로 재임베딩 큐에는 넣지 않는다.

### 배치 (arq)

| 시각 | 태스크 | 내용 | 재시도 |
|---|---|---|---|
| 04:00 | `crawl_dispatch` | CRAWL_CONFIG 대로 `crawl_site` **10개** 팬아웃 | 1 |
| — | `crawl_site` | 수집 → upsert → 변경분 `embed_postings` enqueue | 3 |
| — | `embed_postings` | 청크 분할 → 배치 임베딩 → upsert | 3 |
| 05:30 | `embed_backfill` | 누락·실패분 최대 `EMBED_BACKFILL_LIMIT` 건 | 2 |
| 06:00 | `embed_companies` | 기업 설명 변경분 | 2 |

10개 = 점핏 1 + 원티드 1 + 사람인 8(키워드). 잡코리아는 스킬 수율 15% 라
배치에서 빠져 있다(DECISIONS.md). 어댑터는 남아 있어 수동 실행·재파싱에는 쓴다.

같은 날 같은 (사이트, 키워드) 는 `_job_id = crawl:{site}:{keyword}:{date}` 로
중복 큐잉이 차단된다. "오늘 수집 전체 완료" 시점은 **의도적으로 추적하지
않는다** — 05:30 백필이 누락분을 청소해 결과적 정합성을 보장한다.

### 노트북에서 상시 운영하기

새벽 4시에 노트북이 꺼져 있으면 그날 수집은 없던 일이 된다. 그래서:

- `cron(crawl_dispatch, hour=4, minute=0, run_at_startup=True)` — 기동 시 1회
- `keep_result = 86400` — **중복 차단이 여기 달려 있다.** arq 는 결과가
  만료되면 그 `_job_id` 를 처음 보는 것으로 취급한다. 기본값 3600 이면
  1시간 뒤 차단이 풀려서, 워커를 재시작할 때마다 같은 날 수집을 처음부터
  다시 돌린다.
- compose `worker` 서비스가 `restart: unless-stopped` 로 떠 있다.

같은 날 두 번째 기동은 로그에 이렇게 남는다:

```
crawl_dispatch: 0건 enqueue (중복 차단 10)
```

### 임베딩 제공자 — 로컬 / API 갈아끼우기

**기본은 임베딩 API(voyage)다.** 로컬(BGE-m3)은 호스트에서 쓰는 선택지이고
컨테이너에는 넣지 않는다 — 아래 "로컬 임베딩은 호스트에서만" 참고.

`EMBED_PROVIDER` 하나로 바뀐다. 분기는 `build_embedder()` 한 곳에만 있고,
태스크·툴은 `EmbedderPort` 만 보므로 호출부는 손댈 필요가 없다.

| 값 | 어댑터 | 비고 |
|---|---|---|
| `auto` | 키 있으면 Voyage, 없으면 Fake | 기본값. 도입 전 동작 그대로 |
| `local` | `LocalEmbedder` (BGE-m3) | `uv sync --group local` 필요 |
| `voyage` | `VoyageEmbedder` | 키 필수 |
| `fake` | `FakeEmbedder` | 해시 기반 더미 벡터 |

```bash
uv sync --group local                  # torch + sentence-transformers (수 GB)
EMBED_PROVIDER=local uv run python -m app.cli embed
```

첫 실행은 모델 가중치 약 2.3GB 를 받는다(`~/.cache/huggingface`). 이후
기동마다 로드에 약 6초가 들고, 프로세스당 1회만 올린다.

**실측 (M5 Pro · 48GB / 청크 192개 / 길이 150~1200자)**

| 구성 | 배치 | 청크/초 | 공고/분 |
|---|---:|---:|---:|
| 호스트 MPS fp16 | 32 | 92.0 | 1,903 |
| 호스트 MPS fp32 | 16 | 28.3 | 585 |
| 호스트 CPU fp32 | 16 | 6.6 | 137 |
| **컨테이너 CPU fp32** | 16 | 3.4 | **71** |
| Voyage 무료 등급 | — | — | 12 |

컨테이너가 호스트 CPU 의 절반인 것은 Docker Desktop VM 오버헤드다.
**CPU 에서는 배치 16 이 최적이다** — 같은 코퍼스로 16: 3.4 · 32: 3.1 · 64: 2.5.
기본값 `EMBED_LOCAL_BATCH_SIZE=32` 는 GPU 기준이므로, CPU 로 돌릴 일이 있으면
16 으로 낮춘다.

fp16 은 fp32 대비 3.1배 빠르고 벡터는 사실상 같다 — 같은 청크 코사인 최소
0.99976, top-5 이웃 일치율 96.2%. GPU(cuda/mps)에서만 켜진다. CPU 에서는
half 연산이 가속되지 않아 무시된다.

`EMBED_BATCH_SIZE`(96)는 **로컬에 적용되지 않는다.** 저 값은 네트워크 왕복을
줄이려는 것이고 로컬 GPU 에서는 오히려 31% 느리다(96: 63.4 청크/초). 로컬은
`EMBED_LOCAL_BATCH_SIZE`(기본 32)를 따로 본다. `EMBED_RPM`/`EMBED_TPM` 도
로컬에는 해당이 없어 무시된다.

**★ 제공자를 바꾸면 기존 벡터는 못 쓴다.** BGE-m3 와 voyage-3 는 차원이 둘 다
1024 라 INSERT 는 멀쩡히 통과하는데 벡터 공간이 서로 달라 코사인 유사도만
조용히 깨진다. 에러가 안 나서 알아채기 어렵다. 바꿨으면 전량 재생성할 것:

```sql
UPDATE market.job_posting SET embed_hash = NULL;
DELETE FROM market.posting_chunk;
```

```bash
EMBED_PROVIDER=local uv run python -m app.cli embed
```

#### ★ 로컬 임베딩은 호스트에서만 쓴다 — 컨테이너에 넣지 말 것

**맥의 Docker 는 GPU(MPS)를 통과시키지 않는다.** Apple 의 Metal 을 리눅스 VM
으로 넘기는 경로가 없어서, `--gpus` 같은 옵션 자체가 존재하지 않는다.
컨테이너 안에서 `torch` 는 항상 `device=cpu` 다.

실제로 워커 이미지에 넣어 봤고, 결론은 "넣지 않는다" 였다:

| | 이미지 | 청크/초 | CPU 사용 |
|---|---:|---:|---|
| 호스트 (MPS fp16) | — | **90.7** | 코어 0.1개 (1%) |
| 컨테이너 (CPU fp32) | 3.6GB | 3.4 | 15코어 전부 |
| 컨테이너 (API, 현재) | 1.1GB | — | 없음 |

CPU 를 다 태우면서 27배 느리다. 스레드를 묶어도 나아지지 않는다 —
4스레드 1.9 청크/초 · 8스레드 2.9 청크/초로 속도만 같이 떨어진다.

그래서 `Dockerfile` 은 `--group local` 없이 굽고(1.1GB), 워커는 임베딩 API 를
쓴다. 로컬 임베딩이 필요하면 워커를 호스트에서 돌린다:

```bash
docker compose stop worker
EMBED_PROVIDER=local uv run arq app.worker.WorkerSettings
```

> 리눅스 + NVIDIA 서버라면 이야기가 다르다. 거기서는 nvidia-container-toolkit
> 으로 컨테이너가 GPU 를 쓸 수 있다. 못 쓰는 건 **맥의 Docker** 다.
>
> 굳이 컨테이너에 넣는다면 `pyproject.toml` 의 `[tool.uv.sources]` 가 linux 용
> torch 를 CPU 전용 빌드로 받게 해 둔 것이 필요하다. 기본 PyPI 판은 CUDA
> 런타임을 끌고 와서 이미지가 17.1GB 가 된다(`nvidia/` 2.9GB + `triton/` 652MB
> 가 전부 죽은 무게). 그래서 `local` 그룹에 `torch` 가 **직접** 적혀 있다 —
> uv 의 source 재정의는 직접 의존성에만 적용되고 전이 의존성은 건너뛴다.

### 임베딩 레이트리밋

Voyage 계정에 결제수단이 없으면 **3 RPM · 10K TPM** 으로 묶인다. 명세의
96개 배치는 이 한도를 그냥 넘어 매 요청이 429 로 튕기므로, 어댑터가
`EMBED_RPM` · `EMBED_TPM` 을 보고 **보내기 전에** 창을 기다린다.

```
EMBED_RPM=3               # 0 이면 클라이언트 제한 없음(유료 등급)
EMBED_TPM=10000
EMBED_BACKFILL_LIMIT=100  # 무료 등급 기준. 표준 등급이면 500
EMBED_ENQUEUE_CHUNK=40    # crawl_site → embed_postings 한 job 당 공고 수. 0=쪼개지 않음
```

무료 등급 실측: 공고 60건(청크 173개) 임베딩에 약 4분 — **분당 12건**.
그 속도로는 500건 백필이 `job_timeout=600` 안에 못 끝나 태스크가 통째로
잘리므로, 백필 100건 · enqueue 40건으로 낮춰 뒀다.

**백로그가 쌓이면 백필로는 못 따라잡는다.** `EMBED_BACKFILL_LIMIT=100` 은
하루 100건이다. 대량 수집 직후처럼 미임베딩이 수천 건이면 몇 주가 걸리므로,
그때는 표준 등급으로 올리거나 `python -m app.cli embed` 를 직접 오래 돌린다.

```sql
-- 남은 임베딩 대상
SELECT COUNT(*) FROM market.job_posting
WHERE description IS NOT NULL AND body_is_image = false
  AND body_extract_failed = false
  AND (embed_hash IS NULL OR embed_hash IS DISTINCT FROM content_hash);
```

결제수단을 등록해 표준 등급으로 올린 뒤에는 네 값을 되돌린다:

```
EMBED_RPM=0
EMBED_TPM=0
EMBED_BACKFILL_LIMIT=500
EMBED_ENQUEUE_CHUNK=0
```

## Windows 개발 환경 주의

### 1. psycopg async 는 SelectorEventLoop 가 필요하다

Windows 기본 이벤트 루프(`ProactorEventLoop`)에서는 psycopg 가 async 모드로
동작하지 않는다 (`InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`).
uvicorn/arq 를 띄우기 **전에** 정책을 바꿔야 한다.

```python
import asyncio, sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

- 앱: `app/main.py` 최상단(uvicorn 이 루프를 만들기 전에 import 되는 위치)
- 워커: `app/worker.py` 최상단
- alembic: 해당 없음. `env.py` 가 동기 엔진을 쓴다

검증됨: 기본 루프 → 연결 실패 / SelectorEventLoop → 정상.

### 2. 포트 충돌

이 PC 는 5432·6379 를 네이티브 서비스(`postgresql-x64-18`, `Redis`)가,
5433·6380 을 다른 프로젝트 컨테이너(`aegis-*`)가 쓰고 있다.
그래서 `.env` 에서 **5440 / 6390** 으로 비켜 띄운다.

호스트 포트가 이미 점유돼 있으면 Docker 는 컨테이너를 띄우되 연결은
선점한 쪽으로 가버려서, "붙긴 붙는데 스키마가 없다" 는 형태로 헷갈린다.
포트를 바꿀 때는 `.env` 의 `POSTGRES_PORT`/`REDIS_PORT` 와
`DATABASE_URL`/`REDIS_URL` 을 **함께** 고칠 것.

## 구조

```
ai-service/
├── docker-compose.yml   postgres(pgvector) + redis
├── init.sql             extension · schema(market/chat) · 테이블 · 인덱스 · 롤
├── alembic/             마이그레이션 (baseline 은 init.sql)
├── pyproject.toml       의존성 + import-linter 계약(R1~R4)
├── app/
│   ├── main.py          FastAPI 조립
│   ├── worker.py        arq WorkerSettings · cron
│   ├── core/            설정 · DB · redis · 인증 · enums · 예외
│   ├── llm/             port(Protocol) ← chat_adapter · embed_adapter · fake
│   └── domains/
│       ├── market/      데이터의 주인. models · repository(쓰기) · queries(읽기)
│       ├── crawler/     수집·적재·임베딩만
│       └── chat/        어시스턴트. graph · tools(13개)
└── tests/
```

## 의존 규칙

| 규칙 | 내용 |
|---|---|
| R1 | `market` 은 아무 도메인도 import 하지 않는다 |
| R2 | `crawler` → `market.repository` (쓰기)만 |
| R3 | `chat` → `market.queries` (읽기)만. `market.models` / `repository` 직접 참조 금지 |
| R4 | `llm/port.py` 는 어댑터를 모른다. 주입만 받는다 |
| R5 | 비즈니스 상수는 `core/enums.py` 에만 |

CI에서 강제:

```bash
uv run lint-imports
```

R3 은 DB 레벨에서도 강제된다. 운영 접속을 `ai_crawler`(market RW) /
`ai_chat`(market RO + chat RW) 로 분리하면 챗봇은 쓰기 자체가 불가능하다.

## DB 메모
나도 개발해야되서 걍 임시 DB 만들어둠.<br>
백엔드쪽에서 DB 만들면 버릴 예정

- 벡터 차원은 **1024 고정**. `.env` 의 `EMBED_DIM` 과 `init.sql` 의
  `vector(1024)` 가 어긋나면 INSERT 단계에서 터진다.
- 벡터 인덱스는 HNSW(코사인). **마이그레이션이 만들지 않는다** —
  DDL 은 `app/domains/market/vector_index.py` 에 있고 적재가 끝난 뒤
  `app.cli vector-index --build` 로 세운다. 위 "초기 적재 순서" 참고.
- LangGraph checkpointer 테이블은 앱 기동 시 `.setup()` 이 `chat` 스키마에
  생성한다. alembic 관리 대상이 아니다.
- 스키마 변경은 `models.py` → autogenerate → 리뷰 순서로 하고,
  `init.sql` 도 같이 갱신한다. (`alembic/versions/README.md` 참고)

## 메인 백엔드와 합의 필요

JWT 알고리즘(RS256 가정) · `sub` 클레임 타입(문자열 가정) · 공개키 전달 방법.
