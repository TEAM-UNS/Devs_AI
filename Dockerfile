# arq 워커 이미지.
#
# 이 이미지는 **워커 전용**이다. FastAPI 는 개발 중 로컬에서 --reload 로 띄우는
# 편이 편해서 컨테이너로 만들지 않았다. 필요해지면 CMD 만 바꿔 재사용하면 된다.
#
# 빌드가 느려지지 않게 의존성 설치와 소스 복사를 분리한다.
# pyproject/uv.lock 이 안 바뀌면 uv sync 레이어는 캐시된다.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# ① 의존성만 먼저 (소스가 바뀌어도 재설치하지 않는다)
#
# ★ --group local(로컬 임베딩)은 일부러 넣지 않는다. sentence-transformers +
#   torch 로 이미지가 1.1GB → 3.6GB 가 되는데, 맥의 Docker 는 GPU(MPS)를
#   통과시키지 않아 컨테이너 안에서는 CPU fp32 로 3.4 청크/초밖에 안 나온다.
#   임베딩 API(분당 12건, 무료 등급) 대비 이득이 적고 CPU 를 15코어 전부 태운다.
#   로컬 임베딩이 필요하면 호스트에서 돌릴 것 — MPS fp16 으로 92 청크/초다.
#       docker compose stop worker
#       EMBED_PROVIDER=local uv run arq app.worker.WorkerSettings
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ② 소스
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/

# 스냅샷 저장 위치. compose 에서 볼륨으로 덮어쓴다.
RUN mkdir -p data/raw

# ★ Windows 의 SelectorEventLoop 문제는 리눅스 컨테이너에는 없지만,
#   app.worker 가 import 시점에 정책을 잡는 것은 그대로 안전하다.
CMD ["arq", "app.worker.WorkerSettings"]
