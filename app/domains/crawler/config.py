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
        config = {
            # ★ pages 는 더 이상 수집 범위가 아니다. 안전 상한일 뿐이다.
            #   범위는 CrawlService 가 정한다 — 목록이 최신순이므로 "이미 가진
            #   공고만 나오는 페이지" 가 2번 연속이면 거기서 멈춘다. 평소엔
            #   2~3페이지에서 끝나고, 수집이 며칠 밀렸으면 밀린 만큼 더 내려간다.
            #   상한을 두는 건 정렬이 깨지거나 사이트가 개편됐을 때 무한정 긁는
            #   사고를 막기 위해서다.
            "jumpit": {"pages": 60, "keywords": None},
            # ★ 원티드는 배치에서 제외. 등록일을 어디서도 주지 않는다.
            #   목록·상세 응답 전부에 날짜 필드가 없어 posted_at 이 전량 NULL 이다.
            #   수집일(created_at)로 대체하면 두 달 전 공고가 오늘 올라온 것으로
            #   집계된다 — 원티드는 활성 공고만 노출해서 과거 공고가 계속 목록에
            #   남아 있기 때문이다. 틀린 줄 모르는 숫자가 나오느니 빼는 게 낫다.
            #   어댑터는 build_crawler() 에 남겨 수동 실행·스냅샷 재파싱에 쓴다
            #   (잡코리아와 같은 처리).
            "saramin": {
                "pages": 80,
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
