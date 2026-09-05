"""사이트 어댑터 공통 골격 — 딜레이 · 재시도 · 감속 · 스냅샷.

정책
    딜레이   요청 간 base_delay × uniform(1.0, 1.6) 지터
    동시성   세마포어 2
    재시도   429 · 403 · 5xx · 타임아웃 → 지수 백오프 3회
    감속     403/429 를 만나면 base_delay 를 1.6배로 올린다 (최대 20초).
             한 번 올라간 딜레이는 이 실행 동안 내려가지 않는다.
    스냅샷   모든 응답 원본을 data/raw/{source}/ 에 저장한다.
             파서가 깨졌을 때 재수집 없이 재파싱할 수 있어야 한다.

셀렉터/스키마 미스는 ParseError 로 올린다. 호출부(service)가 해당 사이트만
중단하고 나머지는 계속 진행한다.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import Any, Self

import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.domains.crawler.schemas import RawJob

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

RETRY_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}
BLOCK_STATUS = {403, 429}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class CrawlError(Exception):
    pass


class FetchError(CrawlError):
    pass


class ParseError(CrawlError):
    pass


class SelectorBrokenError(ParseError):
    def __init__(self, source: str, keyword: str | None = None) -> None:
        target = f"{source}({keyword})" if keyword else source
        super().__init__(f"{target}: 1페이지 0건 — 목록 셀렉터/엔드포인트가 깨졌습니다.")
        self.source = source
        self.keyword = keyword


class BaseSiteCrawler(abc.ABC):
    source: str
    referer: str
    concurrency: int = 2

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        delay: float | None = None,
        snapshot_dir: Path | str | None = None,
    ) -> None:
        settings = get_settings()
        self._delay = delay if delay is not None else settings.crawl_delay_seconds
        self._max_delay = settings.crawl_max_delay_seconds
        self._factor = settings.crawl_delay_factor
        self._max_retry = settings.crawl_max_retry

        self._client = client
        self._owns_client = client is None
        self._sem = asyncio.Semaphore(self.concurrency)

        base = Path(snapshot_dir) if snapshot_dir else Path("data/raw")
        self.snapshot_dir = base / self.source
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

        self.request_count = 0

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def __aenter__(self) -> Self:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0),
                follow_redirects=True,
                headers=self.default_headers(),
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": get_settings().crawl_user_agent or BROWSER_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": self.referer,
            "Origin": self.referer.rstrip("/").rsplit("/", 1)[0] if self.referer else "",
        }

    # ── 요청 ──────────────────────────────────────────────────────────────
    async def _sleep_with_jitter(self) -> None:
        await asyncio.sleep(self._delay * random.uniform(1.0, 1.6))

    def _slow_down(self) -> None:
        before = self._delay
        self._delay = min(self._delay * self._factor, self._max_delay)
        log.warning("%s: 차단 감지 → delay %.1fs → %.1fs", self.source, before, self._delay)

    def snapshot_path(self, name: str, suffix: str = ".json") -> Path:
        return self.snapshot_dir / f"{_SAFE_NAME.sub('_', name)}{suffix}"

    def save_snapshot(self, name: str, text: str, suffix: str = ".json") -> Path:
        path = self.snapshot_path(name, suffix)
        path.write_text(text, encoding="utf-8")
        return path

    def load_snapshot(self, name: str) -> Any | None:
        path = self.snapshot_path(name)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def load_html_snapshot(self, name: str) -> str | None:
        path = self.snapshot_path(name, ".html")
        return path.read_text(encoding="utf-8") if path.exists() else None

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        snapshot: str | None = None,
    ) -> Any:
        text = await self.get_text(url, params=params, snapshot=snapshot, suffix=".json")
        return json.loads(text)

    async def get_soup(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        snapshot: str | None = None,
    ) -> BeautifulSoup:
        html = await self.get_text(url, params=params, snapshot=snapshot, suffix=".html")
        return BeautifulSoup(html, "lxml")

    async def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        snapshot: str | None = None,
        suffix: str = ".html",
    ) -> str:
        if self._client is None:
            raise RuntimeError("async with 로 진입한 뒤 사용하세요.")

        last_error: Exception | None = None

        async with self._sem:
            for attempt in range(1, self._max_retry + 1):
                await self._sleep_with_jitter()
                try:
                    self.request_count += 1
                    res = await self._client.get(url, params=params)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = exc
                    log.warning(
                        "%s: %s (%d/%d) %s",
                        self.source,
                        type(exc).__name__,
                        attempt,
                        self._max_retry,
                        url,
                    )
                else:
                    if res.status_code in BLOCK_STATUS:
                        self._slow_down()
                    if res.status_code not in RETRY_STATUS:
                        res.raise_for_status()
                        if snapshot:
                            self.save_snapshot(snapshot, res.text, suffix)
                        return res.text

                    last_error = httpx.HTTPStatusError(
                        f"{res.status_code} {url}", request=res.request, response=res
                    )
                    log.warning(
                        "%s: HTTP %d (%d/%d) %s",
                        self.source,
                        res.status_code,
                        attempt,
                        self._max_retry,
                        url,
                    )

                if attempt < self._max_retry:
                    await asyncio.sleep(self._delay * (2 ** (attempt - 1)))

        raise FetchError(f"{url} 재시도 {self._max_retry}회 소진") from last_error

    # ── 어댑터가 구현할 것 ────────────────────────────────────────────────
    @abc.abstractmethod
    async def fetch_list_page(self, page: int) -> tuple[list[RawJob], int]:
        pass

    @abc.abstractmethod
    async def fetch_detail(self, job: RawJob) -> RawJob:
        pass
