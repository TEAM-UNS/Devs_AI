# 사이트별 수집 대상과 크롤러 생성

from dataclasses import dataclass

from app.domains.crawler.sites.base import BaseSiteCrawler
from app.domains.crawler.sites.jobkorea import JobkoreaCrawler
from app.domains.crawler.sites.jumpit import JumpitCrawler
from app.domains.crawler.sites.saramin import SaraminCrawler
from app.domains.crawler.sites.wanted import WantedCrawler
from typing import Optional


@dataclass(frozen=True)
class CrawlJob:
    site: str
    keyword: Optional[str]
    pages: int

    @property
    def job_key(self) -> str:
        return self.keyword or "all"


def iter_crawl_jobs(config: Optional[dict[str, dict[str, object]]] = None) -> list[CrawlJob]:
    if not config:
        # 페이지 수는 목록이 실제로 끝나는 지점 + 여유로 잡는다. CrawlService 는
        # 빈 페이지를 만나면 break 하므로 넉넉하게 줘도 요청 1번만 더 나간다.
        #   실측 2026-10-02 (목록 1페이지씩 찍어 확인)
        #     점핏   p50 에서 7건(부분) · p60 0건  → 총 791건
        #     원티드 p100 까지 20건씩 · p150 0건   → API 가 총계를 주지 않아 범위로만 안다
        #     사람인 p40 까지 페이지당 신규 17~29건. 검색이 느슨해 깊이 들어가면
        #            관련도가 떨어진다(개발 분야 분류율 p8 40/40 · p32 30/40 · p56 29/40).
        #            30페이지까지만 — 그 이상은 트렌드 집계의 분모만 늘린다
        config = {
            "jumpit": {"pages": 60, "keywords": None},
            "wanted": {"pages": 150, "keywords": None},
            "saramin": {
                "pages": 30,
                "keywords": [
                    "백엔드",
                    "프론트엔드",
                    "안드로이드",
                    "iOS",
                    "데이터 엔지니어",
                    "DevOps",
                    "정보보안",
                    "임베디드",
                ],
            },
            # 잡코리아는 보류라 제외 (DECISIONS.md 참고)
        }
    jobs: list[CrawlJob] = []
    for site, cfg in config.items():
        pages = int(cfg.get("pages", 1))  # type: ignore[arg-type]
        keywords = cfg.get("keywords")
        if keywords is None:
            jobs.append(CrawlJob(site=site, keyword=None, pages=pages))
            continue
        for keyword in keywords:  # type: ignore[union-attr]
            jobs.append(CrawlJob(site=site, keyword=keyword, pages=pages))
    return jobs


def build_crawler(site: str, keyword: Optional[str] = None, **kwargs: object) -> BaseSiteCrawler:
    site_classes: dict[str, type[BaseSiteCrawler]] = {
        "jumpit": JumpitCrawler,
        "wanted": WantedCrawler,
        "saramin": SaraminCrawler,
        "jobkorea": JobkoreaCrawler,
    }
    cls = site_classes.get(site)
    if cls is None:
        raise ValueError(f"지원하지 않는 사이트: {site} (가능: {', '.join(site_classes)})")
    if site in {"saramin", "jobkorea"} and keyword:
        return cls(keyword=keyword, **kwargs)  # type: ignore[arg-type]
    return cls(**kwargs)  # type: ignore[arg-type]
