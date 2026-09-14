# 사이트별 수집 대상과 크롤러 생성

from dataclasses import dataclass

from app.domains.crawler.sites.base import BaseSiteCrawler
from app.domains.crawler.sites.jobkorea import JobkoreaCrawler
from app.domains.crawler.sites.jumpit import JumpitCrawler
from app.domains.crawler.sites.saramin import SaraminCrawler
from app.domains.crawler.sites.wanted import WantedCrawler

KEYWORDS = [
    "백엔드",
    "프론트엔드",
    "안드로이드",
    "iOS",
    "데이터 엔지니어",
    "DevOps",
    "정보보안",
    "임베디드",
]

CRAWL_CONFIG: dict[str, dict[str, object]] = {
    "jumpit": {"pages": 40, "keywords": None},
    "wanted": {"pages": 30, "keywords": None},
    "saramin": {"pages": 8, "keywords": KEYWORDS},
    # 잡코리아는 보류라 제외 (DECISIONS.md 참고)
}

DEFAULT_SKIP_SEEN_DAYS = 7

SITE_CLASSES: dict[str, type[BaseSiteCrawler]] = {
    "jumpit": JumpitCrawler,
    "wanted": WantedCrawler,
    "saramin": SaraminCrawler,
    "jobkorea": JobkoreaCrawler,
}

KEYWORD_SITES = {"saramin", "jobkorea"}


@dataclass(frozen=True)
class CrawlJob:
    site: str
    keyword: str | None
    pages: int

    @property
    def job_key(self) -> str:
        return self.keyword or "all"


def iter_crawl_jobs(config: dict[str, dict[str, object]] | None = None) -> list[CrawlJob]:
    jobs: list[CrawlJob] = []
    for site, cfg in (config or CRAWL_CONFIG).items():
        pages = int(cfg.get("pages", 1))  # type: ignore[arg-type]
        keywords = cfg.get("keywords")
        if keywords is None:
            jobs.append(CrawlJob(site=site, keyword=None, pages=pages))
            continue
        for keyword in keywords:  # type: ignore[union-attr]
            jobs.append(CrawlJob(site=site, keyword=keyword, pages=pages))
    return jobs


def build_crawler(site: str, keyword: str | None = None, **kwargs: object) -> BaseSiteCrawler:
    cls = SITE_CLASSES.get(site)
    if cls is None:
        raise ValueError(f"지원하지 않는 사이트: {site} (가능: {', '.join(SITE_CLASSES)})")
    if site in KEYWORD_SITES and keyword:
        return cls(keyword=keyword, **kwargs)  # type: ignore[arg-type]
    return cls(**kwargs)  # type: ignore[arg-type]
