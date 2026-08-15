"""FastAPI 앱 진입점.

책임
    - lifespan: DB 엔진 · arq redis pool · LangGraph checkpointer(.setup())
      · 스킬 임베딩 메모리 로드 초기화 / 종료 정리
    - 라우터 등록: chat.router (/chat/*)
    - CORS · 예외 핸들러 등록
    - /health

여기에 도메인 로직을 두지 않는다. 조립만 한다.

★ Windows: 이 모듈 최상단(uvicorn 이 루프를 만들기 전)에서
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
  를 호출해야 한다. 기본 ProactorEventLoop 에서는 psycopg async 가 동작하지
  않는다. 자세한 내용은 README 의 "Windows 개발 환경 주의".
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import dispose_engines, ensure_selector_event_loop_policy
from app.core.redis import close_redis_pool, init_redis_pool

ensure_selector_event_loop_policy()

# from app.domains.chat.router import router as chat_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    pass


app = FastAPI(title="jobstack-ai", lifespan=lifespan)

# ★ cors_origins(콤마 문자열)를 그대로 넘기면 안 된다. Starlette 의 검사가
#   `origin in self.allow_origins` 인데, 문자열에 in 을 쓰면 부분문자열 매칭이
#   되어 https://jobstack.co 가 https://jobstack.com 설정을 통과한다.
#   allow_credentials=True 와 겹치면 유사 도메인이 인증된 응답을 읽는다.
#   개발 중에는 localhost origin 이 서로 부분문자열이라 멀쩡히 동작해 안 드러난다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    pass
