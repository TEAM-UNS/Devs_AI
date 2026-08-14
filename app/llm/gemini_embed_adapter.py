"""EmbedderPort 구현 — 임베딩 API 어댑터 (Gemini).

책임
    - 배치 호출: 최대 EMBED_BATCH_SIZE 개씩 묶어 1회 호출 (API 상한 100)
    - 입력 타입 구분: RETRIEVAL_DOCUMENT(적재용) / RETRIEVAL_QUERY(검색용)
    - 재시도 EMBED_MAX_RETRY(3) 회. 최종 실패는 UpstreamError
    - 반환 벡터 차원이 EMBED_DIM(1024) 과 다르면 즉시 실패시킨다
    - 계정 레이트리밋(RPM · TPM) 을 클라이언트에서 먼저 지킨다 (rate_budget)

호출 지점: crawler/embed_service.py, chat/tools/search.py(질의 임베딩)

★ 왜 공식 SDK(google-genai)를 안 쓰고 httpx 로 직접 치는가
    두 가지가 이 파이프라인에서 치명적이다. 둘 다 실제로 호출해 확인했다.

    (1) 배치가 조용히 뭉개진다.
        client.aio.models.embed_content(contents=["a","b","c"]) 는 세 문자열을
        하나의 Content 의 parts 로 합쳐서 **벡터 1개**를 돌려준다. 예외도
        경고도 없다. embed_service 는 반환 리스트를 인덱스로 원본 청크에
        되붙이므로 이게 통과하면 엉뚱한 공고에 벡터가 박힌다.
        (types.Content 로 하나씩 감싸면 3개가 나오긴 한다 — 즉 리스트의
        의미가 호출 방식에 따라 달라진다. 그 위에 파이프라인을 올릴 이유가 없다)

    (2) usageMetadata 를 버린다.
        REST 응답에는 promptTokenCount 가 있는데 SDK 응답 객체에는 없다
        (metadata=None, sdk_http_response.body=None). _TokenBudget 의 보정
        신호가 바로 이 값이라, SDK 를 쓰면 추정이 영원히 자기 오차를 모른다.

    REST 계약은 실측으로 고정했다 (아래 _ENDPOINT · 응답 파싱).
    httpx 는 크롤러가 이미 쓰는 의존성이라 워커 이미지도 더 무거워지지 않는다.

★ 실측으로 확인한 API 제약 (2026-08-14, gemini-embedding-2)
    - batchEmbedContents 는 한 번에 최대 100개. 101개는 400 이다.
    - inputTokenLimit 8192 / 텍스트 1개. 청크는 1200자 상한이라 여유가 크지만
      기업 프로필은 청크를 안 나눠서 길어질 수 있다 → MAX_INPUT_CHARS 참고.
    - outputDimensionality=1024 로 자르면 **정규화된 채로** 온다 (norm=1.0).
      gemini-embedding-001 처럼 직접 재정규화할 필요가 없다.
    - 한글 실측 0.591 tok/char.

★ 벡터 호환성
    다른 모델로 만든 벡터와 섞이지 않는다. 차원만 1024 로 맞으면 INSERT 는
    멀쩡히 통과하고 코사인 유사도만 조용히 깨진다. 모델을 바꿨으면
    posting_chunk.embedding · company.embedding 을 전량 재생성할 것.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import UpstreamError

log = logging.getLogger(__name__)

Vector = list[float]

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# 한 요청에 담을 수 있는 텍스트 수의 하드 상한. 서버가 강제한다:
#     "at most 100 requests can be in one batch" (101개로 400)
# EMBED_BATCH_SIZE 가 이보다 크면 여기서 잘라 준다 — 설정 실수로 전량 400 이
# 나는 것보다 조용히 100 으로 맞추고 로그를 남기는 편이 낫다.
MAX_BATCH = 100

# 텍스트 1개의 길이 상한(문자). 모델 한도는 8192 토큰이고 한글 실측이
# 0.591 tok/char 이므로 10,000자 ≈ 5,900토큰으로 여유가 충분하다.
#
# 왜 자르는가: 초과하면 400 이고, 400 은 재시도해도 안 풀리는 실패다. 그런데
# 배치 하나가 400 이면 그 배치에 묶인 공고 96건이 통째로 embed_hash 를 못 닫고
# 다음 백필에서 또 같은 자리에서 죽는다 — 영구 루프다. 긴 텍스트 하나의 꼬리를
# 버리는 쪽이 낫다. 청크(1200자 상한)는 애초에 걸릴 일이 없고, 청크를 나누지
# 않는 기업 프로필만 해당된다.
MAX_INPUT_CHARS = 10_000

# 재시도해도 의미가 없는 상태코드. 키가 틀렸는데 3번 더 물어볼 이유가 없다.
# 429(레이트리밋)와 5xx 는 여기 없다 — 저건 재시도 대상이다.
_FATAL_STATUS = frozenset({400, 401, 403, 404})


# ═══════════════════════════════════════════════════════════════════════════
#  토큰 추정 · 레이트리밋
#
#  둘 다 "남의 서버에 요청을 보낸다" 에서 나오는 복잡도다. 계정 한도가 낮을 때
#  필요하고, EMBED_RPM · EMBED_TPM 이 0 이면 통째로 꺼진다(현재 기본값).
# ═══════════════════════════════════════════════════════════════════════════

# 문자당 토큰 근사의 출발값. gemini-embedding-2 한글 실측(3,320자): 0.591.
#
# ★ 이 값은 출발점일 뿐이고 응답의 promptTokenCount 로 계속 보정한다
#   (_TokenBudget). 고정값으로 두면 과대 추정이 그대로 처리량 손실이 된다 —
#   0.85 로 고정했을 때 실측 0.625 대비 36% 를 허공에 예약해서, 10K TPM 중
#   6K 밖에 못 썼다.
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


class _TokenBudget:
    """응답의 promptTokenCount 로 문자당 토큰 비율을 계속 보정한다.

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

    왜 429 를 맞고 백오프하지 않는가
        (1) 실패 로그가 정상 동작처럼 쌓이고
        (2) 백오프가 짧으면 1분 창이 안 지나 재시도 횟수를 그대로 태워 먹는다.
        그래서 **보내기 전에** 창을 확인해 기다린다.
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


class GeminiEmbedder:
    """EmbedderPort 구현.

    배치 상한은 여기서 한 번 더 건다. 호출부가 묶어 주지만 챗봇 질의 경로처럼
    다른 곳에서 들어올 수도 있어서 어댑터가 스스로 지킨다.
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
        key = api_key if api_key is not None else settings.google_api_key
        if not key:
            raise UpstreamError(
                "GOOGLE_API_KEY 가 비어 있습니다. "
                "키 없이 파이프라인을 돌리려면 FakeEmbedder 를 쓰세요."
            )

        self.model = model or settings.gemini_embed_model
        self.dim = dim or settings.embed_dim
        self.max_retry = max_retry or settings.embed_max_retry

        requested = batch_size or settings.embed_batch_size
        if requested > MAX_BATCH:
            log.warning(
                "EMBED_BATCH_SIZE=%d 는 API 상한(%d)을 넘어 %d 로 낮춥니다.",
                requested,
                MAX_BATCH,
                MAX_BATCH,
            )
        self.batch_size = min(requested, MAX_BATCH)

        # 한도를 그대로 쓰지 않는다. 토큰 수는 추정치라 오차가 있고 그 오차가
        # 곧 429 다. 요청 분할과 대기 계산 모두 여유분을 뺀 값으로 한다.
        raw_tpm = settings.embed_tpm if tpm is None else tpm
        self.tpm = int(raw_tpm * RATE_LIMIT_HEADROOM) if raw_tpm > 0 else 0
        self._limiter = _RateLimiter(rpm=settings.embed_rpm if rpm is None else rpm, tpm=self.tpm)

        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            timeout=60.0,
        )
        # 응답의 promptTokenCount 로 계속 보정되는 추정기. 고정 비율이면 과대
        # 추정만큼 TPM 을 못 쓰고 놀린다.
        self._budget = _TokenBudget()

        self.call_count = 0
        self.total_tokens = 0

    # ── EmbedderPort ──────────────────────────────────────────────────────
    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        out: list[Vector] = []
        for batch in self._split(list(texts)):
            out.extend(await self._embed(batch, task_type="RETRIEVAL_DOCUMENT"))
        return out

    async def embed_query(self, text: str) -> Vector:
        vectors = await self._embed([text], task_type="RETRIEVAL_QUERY")
        return vectors[0]

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── 내부 ──────────────────────────────────────────────────────────────
    def _split(self, texts: list[str]) -> list[list[str]]:
        """개수 상한과 토큰 상한 둘 다 지키도록 쪼갠다.

        TPM 이 0(무제한)이면 개수 상한만 본다 — 유료 등급의 정상 경로다.
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

    def _clamp(self, texts: list[str]) -> list[str]:
        """MAX_INPUT_CHARS 를 넘는 텍스트의 꼬리를 자른다. 이유는 상수 주석 참고."""
        out: list[str] = []
        for text in texts:
            if len(text) > MAX_INPUT_CHARS:
                log.warning(
                    "입력이 %d자로 상한(%d)을 넘어 잘랐습니다. 모델 한도는 8192토큰입니다.",
                    len(text),
                    MAX_INPUT_CHARS,
                )
                text = text[:MAX_INPUT_CHARS]
            out.append(text)
        return out

    def _payload(self, texts: list[str], *, task_type: str) -> dict[str, Any]:
        """batchEmbedContents 요청 본문.

        ★ 텍스트마다 별개의 request 로 넣는다. 하나의 content 에 parts 로 몰면
          서버가 이어 붙여 벡터 1개를 준다 — 모듈 docstring 의 (1) 이 그 얘기다.
        """
        return {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                    "outputDimensionality": self.dim,
                }
                for text in texts
            ]
        }

    async def _embed(self, texts: list[str], *, task_type: str) -> list[Vector]:
        texts = self._clamp(texts)
        url = f"/models/{self.model}:batchEmbedContents"
        last: Exception | None = None

        for attempt in range(1, self.max_retry + 1):
            # 매 시도마다 다시 추정한다. 직전 응답으로 비율이 보정됐을 수 있다.
            await self._limiter.acquire(self._budget.estimate(texts))
            try:
                response = await self._client.post(
                    url, json=self._payload(texts, task_type=task_type)
                )
                if response.status_code in _FATAL_STATUS:
                    raise UpstreamError(
                        f"임베딩 요청이 거부되었습니다 "
                        f"({response.status_code}): {self._error_message(response)}"
                    )
                response.raise_for_status()
                body = response.json()
            except UpstreamError:
                raise
            except Exception as exc:  # noqa: BLE001 — 네트워크 · 5xx · 429 전부 재시도
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
                    if _is_rate_limited(exc):
                        await asyncio.sleep(RATE_LIMIT_BACKOFF_SEC)
                    else:
                        await asyncio.sleep(2 ** (attempt - 1))
                continue

            self.call_count += 1
            used = int(body.get("usageMetadata", {}).get("promptTokenCount", 0) or 0)
            self.total_tokens += used
            self._budget.observe(texts, used)
            return self._validate(body, expected=len(texts))

        raise UpstreamError(f"임베딩 재시도 {self.max_retry}회 소진") from last

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        """구글 오류 본문에서 message 만 뽑는다. 실패하면 원문 앞부분."""
        try:
            return str(response.json()["error"]["message"])[:300]
        except Exception:  # noqa: BLE001 — 오류 메시지 파싱 실패가 원인을 가리면 안 된다
            return response.text[:300]

    def _validate(self, body: dict[str, Any], *, expected: int) -> list[Vector]:
        """개수와 차원을 여기서 막는다. DB 까지 흘려보내지 않는다.

        ★ 개수 검사가 특히 중요하다. embed_service 는 반환 리스트를 인덱스로
          원본 청크에 되붙이므로, 하나라도 어긋나면 전부 밀려서 엉뚱한 공고에
          벡터가 박힌다. 그런 오염은 나중에 "검색이 이상하다" 로만 드러난다.
        """
        raw = body.get("embeddings")
        if not isinstance(raw, list):
            raise UpstreamError(f"임베딩 응답에 embeddings 가 없습니다: {str(body)[:200]}")
        if len(raw) != expected:
            raise UpstreamError(f"임베딩 개수 불일치: 요청 {expected} vs 응답 {len(raw)}")

        vectors: list[Vector] = []
        for item in raw:
            values = item.get("values") if isinstance(item, dict) else None
            if not isinstance(values, list):
                raise UpstreamError(f"임베딩 항목에 values 가 없습니다: {str(item)[:200]}")
            if len(values) != self.dim:
                raise UpstreamError(
                    f"임베딩 차원 불일치: {len(values)} (기대 {self.dim}). "
                    f"GEMINI_EMBED_MODEL={self.model} 이 vector({self.dim}) 컬럼과 "
                    f"맞지 않습니다."
                )
            vectors.append([float(v) for v in values])
        return vectors


def _is_rate_limited(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
