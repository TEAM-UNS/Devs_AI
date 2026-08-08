"""EmbedderPort 구현 — 임베딩 API 어댑터 (Voyage).

책임
    - 배치 호출: 최대 EMBED_BATCH_SIZE(96) 개씩 묶어 1회 호출
    - 입력 타입 구분: document(적재용) / query(검색용)
    - 재시도 EMBED_MAX_RETRY(3) 회. 최종 실패는 UpstreamError
    - 반환 벡터 차원이 EMBED_DIM(1024) 과 다르면 즉시 실패시킨다
      (DB 컬럼 vector(1024) 와 어긋나면 INSERT 단계에서야 터진다)
    - ★ 계정 레이트리밋(RPM · TPM) 을 클라이언트에서 먼저 지킨다

호출 지점: crawler/embed_service.py, chat/tools/search.py(질의 임베딩)

레이트리밋
    Voyage 무료 등급은 3 RPM · 10K TPM 이다. 96개 배치는 토큰 상한을 그냥
    넘어서 매 요청이 429 로 튕긴다. 429 를 맞고 백오프하는 방식은
      (1) 실패 로그가 정상 동작처럼 쌓이고
      (2) 백오프가 짧으면 1분 창이 안 지나 3회 재시도를 그대로 태워 먹는다.
    그래서 **보내기 전에** 창을 확인해 기다린다. EMBED_RPM · EMBED_TPM 이
    0 이면 이 계산은 통째로 꺼진다(유료 등급).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Sequence

import voyageai
from voyageai import error as voyage_error

from app.core.config import get_settings
from app.core.exceptions import UpstreamError
from app.llm.port import EmbedderPort

log = logging.getLogger(__name__)

Vector = list[float]

# 재시도해도 의미가 없는 것들. 키가 틀렸는데 3번 더 물어볼 이유가 없다.
_FATAL = (
    voyage_error.AuthenticationError,
    voyage_error.InvalidRequestError,
    voyage_error.MalformedRequestError,
)

# 문자당 토큰 근사의 출발값. voyage-3 실측(사이트별 청크 20개씩):
#     jumpit 0.601 · saramin 0.606 · wanted 0.623 · jobkorea 0.797
#
# ★ 이 값은 출발점일 뿐이고 응답의 total_tokens 로 계속 보정한다(_TokenBudget).
#   고정값으로 두면 과대 추정이 그대로 처리량 손실이 된다 — 0.85 로 고정했을 때
#   실측 0.625 대비 36% 를 허공에 예약해서, 10K TPM 중 6K 밖에 못 썼다.
TOKENS_PER_CHAR = 0.85

# 관측값보다 이만큼 위로 잡는다. 청크마다 한글/영문 비율이 달라 평균만 믿으면
# 긴 영문 청크가 몰린 배치에서 초과한다.
TOKEN_SAFETY = 1.10
# 추정이 아래로 흐를 때의 하한. 어떤 텍스트도 이보다 토큰이 적지는 않았다.
TOKENS_PER_CHAR_FLOOR = 0.45

# TPM 을 100% 채우면 추정 오차만큼 그대로 429 가 된다.
#
# 0.8 이던 것을 0.9 로 올렸다. 추정 오차 방어는 _TokenBudget 의 TOKEN_SAFETY
# (관측값 대비 +10%)가 이미 하고 있어서, 여기서 20% 를 더 빼는 것은 이중
# 방어였다. 실측 로그가 "요청 1/3 · 토큰 7860/8000" — RPM 은 3개 중 1개만
# 쓰는데 TPM 이 먼저 말라서 62초를 기다렸다. 즉 이 값이 곧 처리량이다.
#     0.8 → 실효 10K 중 7.3K 사용 (73%)
#     0.9 → 실효 10K 중 8.2K 사용 (82%)
RATE_LIMIT_HEADROOM = 0.9

# 서버의 1분 창과 우리 창은 시작점이 다르다. 창이 풀리자마자 쏘면 서버
# 기준으로는 아직 안 지났을 수 있어 여유를 둔다.
RATE_LIMIT_MARGIN_SEC = 3.0

# 429 를 실제로 맞았을 때의 대기. 창이 1분이라 지수 백오프 1·2·4초로는
# 절대 안 풀린다. 창 하나를 통째로 비운다.
RATE_LIMIT_BACKOFF_SEC = 65.0


def estimate_tokens(text: str) -> int:
    """고정 비율 추정. 보정이 필요 없는 호출부(테스트 등)를 위한 편의 함수."""
    return max(1, math.ceil(len(text) * TOKENS_PER_CHAR))


class _TokenBudget:
    """응답의 total_tokens 로 문자당 토큰 비율을 계속 보정한다.

    TPM 이 병목일 때 추정 오차는 그대로 처리량 손실이다. 예약은 추정치로
    하는데 한도는 실제 토큰으로 걸리므로, 36% 과대 추정하면 한도의 64% 만
    쓰게 된다.

    위로는 즉시, 아래로는 천천히 움직인다. 과소 추정은 429(1분 손실)지만
    과대 추정은 그냥 조금 느린 것뿐이라 비대칭이 맞다.
    """

    def __init__(self, initial: float = TOKENS_PER_CHAR) -> None:
        self.ratio = initial
        self.samples = 0

    def estimate(self, texts: Sequence[str]) -> int:
        return max(1, math.ceil(sum(len(t) for t in texts) * self.ratio))

    def observe(self, texts: Sequence[str], actual_tokens: int) -> None:
        chars = sum(len(t) for t in texts)
        if chars <= 0 or actual_tokens <= 0:
            return
        observed = (actual_tokens / chars) * TOKEN_SAFETY
        self.samples += 1
        if observed > self.ratio:
            self.ratio = observed  # 초과 위험은 즉시 반영
        else:
            # 한 번의 짧은 배치로 확 낮추지 않는다. 5% 씩만 내린다.
            self.ratio = max(self.ratio * 0.95, observed, TOKENS_PER_CHAR_FLOOR)


class _RateLimiter:
    """분당 요청수 · 분당 토큰을 클라이언트에서 지키는 슬라이딩 윈도우.

    rpm 또는 tpm 이 0 이면 해당 제한을 보지 않는다.
    """

    WINDOW = 60.0

    def __init__(self, rpm: int, tpm: int) -> None:
        self.rpm = rpm
        self.tpm = tpm
        self._events: deque[tuple[float, int]] = deque()  # (시각, 토큰)
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.rpm > 0 or self.tpm > 0

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= self.WINDOW:
            self._events.popleft()

    async def acquire(self, tokens: int) -> None:
        if not self.enabled:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                self._prune(now)
                used_requests = len(self._events)
                used_tokens = sum(t for _, t in self._events)

                over_rpm = self.rpm > 0 and used_requests + 1 > self.rpm
                over_tpm = self.tpm > 0 and used_tokens + tokens > self.tpm
                if not (over_rpm or over_tpm):
                    self._events.append((now, tokens))
                    return

                # ★ 창이 비었는데도 초과라면, 요청 하나가 창 전체보다 크다는 뜻이다.
                #   기다려도 영원히 안 풀린다 — 비울 게 없다.
                #
                #   실제로 이걸로 318건이 죽었다. _split 은 비율 R1 로 배치를 잘라
                #   tpm 에 딱 맞췄는데, 그 사이 응답을 보고 비율이 R2(>R1) 로 오르면
                #   같은 배치의 추정치가 tpm 을 넘어선다. 그 상태로 여기 들어오면
                #   _events 를 전부 prune 한 뒤 self._events[0] 를 읽어 IndexError.
                #
                #   기다리는 대신 그냥 보낸다. 서버가 429 를 주면 재시도가 받아낸다.
                if not self._events:
                    log.warning(
                        "요청 하나가 창 한도보다 큽니다 (추정 %d토큰 > %d). 그대로 보냅니다.",
                        tokens,
                        self.tpm,
                    )
                    self._events.append((now, tokens))
                    return

                # 가장 오래된 기록이 창을 벗어날 때까지 기다린다.
                wait = self.WINDOW - (now - self._events[0][0]) + RATE_LIMIT_MARGIN_SEC
                log.info(
                    "레이트리밋 대기 %.1fs (요청 %d/%s · 토큰 %d+%d/%s)",
                    wait,
                    used_requests,
                    self.rpm or "∞",
                    used_tokens,
                    tokens,
                    self.tpm or "∞",
                )
                await asyncio.sleep(wait)


class VoyageEmbedder:
    """EmbedderPort 구현.

    배치 상한은 여기서 한 번 더 건다. 호출부가 96개로 묶어 주지만 챗봇 질의
    경로처럼 다른 곳에서 들어올 수도 있어서 어댑터가 스스로 지킨다.
    개수 상한(batch_size)과 토큰 상한(tpm) 둘 다로 쪼갠다.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        batch_size: int | None = None,
        max_retry: int | None = None,
        rpm: int | None = None,
        tpm: int | None = None,
    ) -> None:
        settings = get_settings()
        key = api_key if api_key is not None else settings.voyage_api_key
        if not key:
            raise UpstreamError(
                "VOYAGE_API_KEY 가 비어 있습니다. "
                "키 없이 파이프라인을 돌리려면 FakeEmbedder 를 쓰세요."
            )

        self.model = model or settings.embed_model
        self.dim = dim or settings.embed_dim
        self.batch_size = batch_size or settings.embed_batch_size
        self.max_retry = max_retry or settings.embed_max_retry
        # 한도를 그대로 쓰지 않는다. 토큰 수는 추정치라 오차가 있고 그 오차가
        # 곧 429 다. 요청 분할과 대기 계산 모두 여유분을 뺀 값으로 한다.
        raw_tpm = settings.embed_tpm if tpm is None else tpm
        self.tpm = int(raw_tpm * RATE_LIMIT_HEADROOM) if raw_tpm > 0 else 0
        self._limiter = _RateLimiter(rpm=settings.embed_rpm if rpm is None else rpm, tpm=self.tpm)
        # SDK 자체 재시도는 끄고(기본 max_retries=0) 백오프를 여기서 관리한다.
        # 그래야 실패 로그와 UpstreamError 메시지가 한 곳에서 나온다.
        self._client = voyageai.AsyncClient(api_key=key, timeout=60.0)
        # 응답의 total_tokens 로 계속 보정되는 추정기. 고정 비율이면 과대 추정
        # 만큼 TPM 을 못 쓰고 놀린다.
        self._budget = _TokenBudget()

        self.call_count = 0
        self.total_tokens = 0

    # ── EmbedderPort ──────────────────────────────────────────────────────
    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        out: list[Vector] = []
        for batch in self._split(list(texts)):
            out.extend(await self._embed(batch, input_type="document"))
        return out

    async def embed_query(self, text: str) -> Vector:
        vectors = await self._embed([text], input_type="query")
        return vectors[0]

    # ── 내부 ──────────────────────────────────────────────────────────────
    def _split(self, texts: list[str]) -> list[list[str]]:
        """개수 상한과 토큰 상한 둘 다 지키도록 쪼갠다.

        TPM 이 0(무제한)이면 개수 상한만 본다 — 그때가 명세의 96개 배치다.
        """
        batches: list[list[str]] = []
        current: list[str] = []
        current_tokens = 0

        for text in texts:
            tokens = self._budget.estimate([text])
            too_many = len(current) >= self.batch_size
            too_big = self.tpm > 0 and current and current_tokens + tokens > self.tpm
            if too_many or too_big:
                batches.append(current)
                current, current_tokens = [], 0
            current.append(text)
            current_tokens += tokens

        if current:
            batches.append(current)
        return batches

    async def _embed(self, texts: list[str], *, input_type: str) -> list[Vector]:
        last: Exception | None = None

        for attempt in range(1, self.max_retry + 1):
            # 매 시도마다 다시 추정한다. 직전 응답으로 비율이 보정됐을 수 있다.
            await self._limiter.acquire(self._budget.estimate(texts))
            try:
                result = await self._client.embed(texts, model=self.model, input_type=input_type)
            except _FATAL as exc:
                raise UpstreamError(f"임베딩 요청이 거부되었습니다: {exc}") from exc
            except Exception as exc:  # noqa: BLE001 — 네트워크 · 5xx · 레이트리밋 전부 재시도
                last = exc
                log.warning(
                    "임베딩 실패 (%d/%d) %s: %s",
                    attempt,
                    self.max_retry,
                    type(exc).__name__,
                    str(exc)[:200],
                )
                if attempt < self.max_retry:
                    # 429 는 1분 창이 지나야 풀린다. 지수 백오프로는 못 넘긴다.
                    if isinstance(exc, voyage_error.RateLimitError):
                        await asyncio.sleep(RATE_LIMIT_BACKOFF_SEC)
                    else:
                        await asyncio.sleep(2 ** (attempt - 1))
                continue

            self.call_count += 1
            used = getattr(result, "total_tokens", 0) or 0
            self.total_tokens += used
            self._budget.observe(texts, used)
            return self._validate(result.embeddings, expected=len(texts))

        raise UpstreamError(f"임베딩 재시도 {self.max_retry}회 소진") from last

    def _validate(self, vectors: list[Vector], *, expected: int) -> list[Vector]:
        """개수와 차원을 여기서 막는다. DB 까지 흘려보내지 않는다."""
        if len(vectors) != expected:
            raise UpstreamError(f"임베딩 개수 불일치: 요청 {expected} vs 응답 {len(vectors)}")
        for vector in vectors:
            if len(vector) != self.dim:
                raise UpstreamError(
                    f"임베딩 차원 불일치: {len(vector)} (기대 {self.dim}). "
                    f"EMBED_MODEL={self.model} 이 vector({self.dim}) 컬럼과 맞지 않습니다."
                )
        return vectors


def build_embedder(*, force_fake: bool = False) -> EmbedderPort:
    """설정에 따라 실제 어댑터 / Fake 를 고른다.

    ★ 여기가 유일한 분기점이다. 태스크·툴은 EmbedderPort 만 보고 쓴다.

    USE_FAKE_LLM 은 보지 않는다. 저건 챗봇 LLM 스위치다. 임베딩까지 같이
    묶으면 "챗봇은 Fake 로 두고 임베딩만 실제로 한 번 돌린다" 를 못 한다.
    임베딩은 키 유무로 판단하고, 강제하고 싶으면 force_fake 를 쓴다.
    """
    from app.llm.fake import FakeEmbedder  # port 만 알면 되게 지연 import

    settings = get_settings()
    if force_fake or not settings.voyage_api_key:
        log.warning("FakeEmbedder 를 사용합니다 (force_fake=%s).", force_fake)
        return FakeEmbedder(dim=settings.embed_dim)
    return VoyageEmbedder()
