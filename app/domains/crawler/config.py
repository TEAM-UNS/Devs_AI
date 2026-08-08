"""수집 대상 설정 — crawl_dispatch 가 이 표를 그대로 팬아웃한다 (명세 3-1).

    KEYWORDS       사람인·잡코리아처럼 전 직종이 섞인 사이트에서 쓸 검색어
    CRAWL_CONFIG   사이트 → {pages, keywords}

점핏·원티드는 개발 직군 전용이라 키워드 없이 전체를 순회한다.
사람인·잡코리아는 전 직종이 섞여 있어 키워드로 걸러야 한다.

**페이지당 건수는 사이트별로 다르다.** 첫 수집 로그(`{site} {page}페이지: N건`)
로 확인해 pages 를 조정한다.
"""

from __future__ import annotations

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
    # ★ 잡코리아는 제외한다 (DECISIONS.md "잡코리아 보류").
    #   본문 수율은 79%까지 올렸지만 스킬 수율이 15%에 머문다 — 실제 기술
    #   나열이 별도 JS 엔드포인트에 있기 때문이다. 스킬이 안 잡히는 공고를
    #   매일 수집하면 트렌드 집계의 분모만 늘어난다.
    #   sites/jobkorea.py 와 스냅샷 103건은 보존한다. 엔드포인트를 찾으면
    #   {"pages": 2, "keywords": KEYWORDS} 로 되살린다.
}
# 총 job 수 = 1(jumpit) + 1(wanted) + 8(saramin × KEYWORDS) = 10

# 증분 수집 기본값. 목록 단계에서 최근 N일 내 수집한 공고를 걸러 상세 요청을
# 아낀다. 영구 스킵(=None)은 쓰지 않는다 — 마감일 변경 같은 갱신을 주 1회는
# 반영해야 오래된 정보가 고정되지 않는다.
DEFAULT_SKIP_SEEN_DAYS = 7

# 사이트 어댑터 레지스트리. 태스크는 문자열만 받으므로 여기서 클래스를 찾는다.
#
# CRAWL_CONFIG 에 없는 사이트도 여기 남겨 둔다. 잡코리아는 배치에서 빠졌을
# 뿐이고, 스냅샷 재파싱과 수동 실행(`app.cli crawl --site jobkorea`)에는
# 그대로 쓴다. 엔드포인트를 찾으면 CRAWL_CONFIG 에 한 줄 추가하면 끝이다.
SITE_CLASSES: dict[str, type[BaseSiteCrawler]] = {
    "jumpit": JumpitCrawler,
    "wanted": WantedCrawler,
    "saramin": SaraminCrawler,
    "jobkorea": JobkoreaCrawler,
}

# 생성자가 keyword 를 받는 사이트. 나머지는 키워드를 줘도 쓸 곳이 없다.
KEYWORD_SITES = {"saramin", "jobkorea"}


@dataclass(frozen=True)
class CrawlJob:
    """crawl_dispatch 가 만들어 내는 job 1개."""

    site: str
    keyword: str | None
    pages: int

    @property
    def job_key(self) -> str:
        """중복 큐잉 차단용 키의 일부. 키워드가 없으면 'all'."""
        return self.keyword or "all"


def iter_crawl_jobs(config: dict[str, dict[str, object]] | None = None) -> list[CrawlJob]:
    """CRAWL_CONFIG 를 (site, keyword, pages) 목록으로 편다.

    keywords 가 None 이면 키워드 없이 1개, 리스트면 키워드 수만큼 생긴다.
    """
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
    """사이트 이름 → 어댑터 인스턴스."""
    cls = SITE_CLASSES.get(site)
    if cls is None:
        raise ValueError(f"지원하지 않는 사이트: {site} (가능: {', '.join(SITE_CLASSES)})")
    if site in KEYWORD_SITES and keyword:
        return cls(keyword=keyword, **kwargs)  # type: ignore[arg-type]
    return cls(**kwargs)  # type: ignore[arg-type]
