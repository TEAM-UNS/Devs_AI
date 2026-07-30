# ai-service

와!!!!!!!!!!!!!!!

채용 공고 수집 · 임베딩 · 커리어 어시스턴트.
담당 범위: **크롤링·적재**, **임베딩**, **AI 챗봇**.
(유저/인증 발급 · 대시보드 등등 화면 API 는 메인 백엔드 담당)

## 실행

```bash
cp .env.example .env      # 값 채우기
docker compose up -d      # postgres(pgvector) + redis, init.sql 자동 실행
uv sync
uv run uvicorn app.main:app --reload
uv run arq app.worker.WorkerSettings
```

스키마를 처음부터 다시 만들려면:

```bash
docker compose down -v && docker compose up -d
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
- 벡터 인덱스는 HNSW(코사인). 데이터가 적을 때 만들어 두고 이후 점진 반영.
- LangGraph checkpointer 테이블은 앱 기동 시 `.setup()` 이 `chat` 스키마에
  생성한다. alembic 관리 대상이 아니다.
- 스키마 변경은 `models.py` → autogenerate → 리뷰 순서로 하고,
  `init.sql` 도 같이 갱신한다. (`alembic/versions/README.md` 참고)

## 메인 백엔드와 합의 필요

JWT 알고리즘(RS256 가정) · `sub` 클레임 타입(문자열 가정) · 공개키 전달 방법.
