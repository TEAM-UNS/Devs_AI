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

### 임베딩

`gemini-embedding-2` · 1024차원 · 코사인. 키는 `GOOGLE_API_KEY` 로 챗봇과 공용이다.

`EMBED_PROVIDER` 로 더미와 갈아끼운다. 분기는 `build_embedder()` 한 곳에만 있고,
태스크·툴은 `EmbedderPort` 만 보므로 호출부는 손댈 필요가 없다.

| 값 | 구현 | 비고 |
|---|---|---|
| `auto` | 키 있으면 Gemini, 없으면 Fake | 기본값 |
| `gemini` | `GeminiEmbedder` | 키 필수. 없으면 기동 시 터진다 |
| `fake` | `FakeEmbedder` | 해시 기반 더미 벡터. 키 불필요 |

`gemini` 를 **명시**했는데 키가 없으면 Fake 로 떨어지지 않고 `UpstreamError` 를
던진다. 더미 벡터가 DB 에 들어가면 INSERT 는 통과하고 검색만 조용히 무의미해져서,
한참 뒤에야 드러나기 때문이다.

#### gemini 어댑터가 REST 를 직접 치는 이유

공식 SDK(`google-genai`)를 쓰지 않는다. 실제로 호출해 확인한 두 가지 때문이다.

1. **배치가 조용히 뭉개진다.** `embed_content(contents=["a","b","c"])` 는 세
   문자열을 하나의 content 의 parts 로 합쳐 **벡터 1개**를 돌려준다. 예외도
   경고도 없다. `embed_service` 는 반환 리스트를 인덱스로 원본 청크에
   되붙이므로, 이게 통과하면 엉뚱한 공고에 벡터가 박힌다.
2. **`usageMetadata` 를 버린다.** REST 응답에는 `promptTokenCount` 가 있는데
   SDK 응답 객체에는 없다. 토큰 추정 보정(`_TokenBudget`)의 입력이 바로 그
   값이라, SDK 를 쓰면 추정이 영원히 자기 오차를 모른다.

`httpx` 는 크롤러가 이미 쓰는 의존성이라 워커 이미지도 무거워지지 않는다.

**실측으로 고정한 API 제약** (`gemini-embedding-2`)

| 항목 | 값 |
|---|---|
| `batchEmbedContents` 한 요청 | 최대 **100개** (101개는 400) |
| 입력 토큰 | 8,192 / 텍스트 1개 |
| `outputDimensionality=1024` | 동작. **정규화된 채로** 온다 (norm=1.0) |
| 한글 토큰 비율 | 0.591 tok/char |

청크는 1,200자 상한이라 토큰 한도에 걸릴 일이 없다. 청크를 나누지 않는
**기업 프로필만** 길어질 수 있어 어댑터가 `MAX_INPUT_CHARS`(10,000자)로
꼬리를 자른다 — 초과하면 400 이고, 400 이면 그 배치에 묶인 공고 전부가
`embed_hash` 를 못 닫아 다음 백필에서 같은 자리에서 또 죽는 영구 루프가 된다.

**실측** — 공고 32건(청크 96개)을 API 1회로 2.6초. 기업 프로필 26건은 1.9초.

#### ★ 모델을 바꿨을 때 — 전량 재생성

차원만 1024 로 맞으면 INSERT 는 멀쩡히 통과하는데 벡터 공간이 달라 코사인
유사도만 조용히 깨진다. 에러가 안 나서 알아채기 어렵다.

**`embed_hash` 만 비우는 것으로는 부족하다.** `chunk_hash` 는 본문 내용으로
계산하므로 모델을 바꿔도 그대로다 — 그러면 `pending` 이 비어 "재사용"으로
넘어가고 옛 벡터가 그대로 남는다. 청크 행을 지워야 한다.

```sql
UPDATE market.job_posting SET embed_hash = NULL;
UPDATE market.company SET embed_hash = NULL, profile_embedding = NULL;
DELETE FROM market.posting_chunk;   -- ★ 이게 빠지면 재임베딩이 일어나지 않는다
```

```bash
uv run python -m app.cli embed
uv run python -m app.cli embed --companies
```

확인:

```sql
SELECT count(*) AS chunks, count(embedding) AS with_vector,
       min(vector_dims(embedding)) AS dim,
       round(min(sqrt(-(embedding <#> embedding)))::numeric, 6) AS min_norm
FROM market.posting_chunk;
```

### 임베딩 레이트리밋

**현재(gemini 선결제 등급)는 꺼 두었다.** `EMBED_RPM=0` · `EMBED_TPM=0` 이면
`_RateLimiter` 는 통째로 no-op 이고 배치는 개수 상한(96)만 본다.

```
EMBED_RPM=0               # 0 이면 클라이언트 제한 없음
EMBED_TPM=0
EMBED_BACKFILL_LIMIT=500
EMBED_ENQUEUE_CHUNK=0     # crawl_site → embed_postings 한 job 당 공고 수. 0=쪼개지 않음
```

장치 자체는 남겨 뒀다. 429 가 보이기 시작하면 실측값을 넣으면 된다 — 어댑터가
`EMBED_RPM` · `EMBED_TPM` 을 보고 **보내기 전에** 창을 기다린다. 429 를 맞고
백오프하지 않는 이유는 (1) 실패 로그가 정상 동작처럼 쌓이고 (2) 백오프가
짧으면 1분 창이 안 지나 재시도 횟수를 그대로 태워 먹기 때문이다.

**백로그가 쌓이면 백필로는 못 따라잡는다.** `EMBED_BACKFILL_LIMIT` 은 1회
상한이다. 대량 수집 직후처럼 미임베딩이 수천 건이면 `python -m app.cli embed`
를 직접 돌리는 편이 빠르다.

```sql
-- 남은 임베딩 대상
SELECT COUNT(*) FROM market.job_posting
WHERE description IS NOT NULL AND body_is_image = false
  AND body_extract_failed = false
  AND (embed_hash IS NULL OR embed_hash IS DISTINCT FROM content_hash);
```


## Windows 개발 환경 주의

### 1. psycopg async 는 SelectorEventLoop 가 필요할 수 있다

Windows 기본 이벤트 루프(`ProactorEventLoop`)에서 psycopg 가
`InterfaceError: Psycopg cannot use the 'ProactorEventLoop'` 로 죽으면,
uvicorn/arq 를 띄우기 **전에** 정책을 바꿔야 한다.

```python
import asyncio, sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

코드에는 넣지 않는다 (개발은 macOS/Linux 기준). Windows 에서 이 오류를
만나면 진입점 최상단에 위 3줄을 임시로 넣는다.

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
| R5 | Enum·비즈니스 상수는 소유 도메인에 (`market/enums.py`, `chat/enums.py`) |

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
