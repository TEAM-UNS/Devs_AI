# 잡코리아 파서 테스트

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.domains.crawler.extractor import parse_salary
from app.domains.crawler.sites import htmlutil as hu
from app.domains.crawler.sites.jobkorea import JobkoreaCrawler, salary_text_from_jsonld
from app.domains.market.enums import SalaryPeriod, SalaryType

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> BeautifulSoup:
    return BeautifulSoup((FIXTURES / name).read_text(encoding="utf-8"), "lxml")


@pytest.fixture(scope="module")
def crawler() -> JobkoreaCrawler:
    return JobkoreaCrawler()


@pytest.fixture(scope="module")
def listing() -> BeautifulSoup:
    return load("jobkorea_list.html")


@pytest.fixture(scope="module")
def detail() -> BeautifulSoup:
    return load("jobkorea_detail.html")


def test_bot_detection_policy_is_conservative(crawler):
    assert crawler.concurrency == 1
    assert crawler._delay >= 2.5


def test_list_yields_jobs(crawler, listing):
    jobs, _ = crawler.parse_list(listing, page=1)
    assert jobs
    for job in jobs:
        assert job.source == "jobkorea"
        assert job.source_job_id.isdigit()
        assert job.title
        assert job.url.endswith(job.source_job_id)


def test_empty_list_raises_parse_error(crawler):
    with pytest.raises(Exception, match="찾지 못했"):
        crawler.parse_list(BeautifulSoup("<html><body></body></html>", "lxml"), page=1)


def test_detail_has_jsonld_jobposting(detail):
    posting = hu.jobposting_from_jsonld(detail)
    assert posting is not None
    assert posting["@type"] == "JobPosting"


def test_detail_has_no_label_value_pairs(detail):
    pairs = hu.label_value_pairs(detail)
    assert hu.pick(pairs, "career") is None
    assert hu.pick(pairs, "employee") is None


def test_merge_detail_fills_from_jsonld(crawler, listing, detail):
    jobs, _ = crawler.parse_list(listing, page=1)
    merged = crawler.merge_detail(jobs[0], detail)

    assert merged.detail_fetched
    assert merged.title
    assert merged.company_name
    assert merged.education
    assert merged.employment_type == "정규직"
    assert merged.published_at is not None
    assert merged.closed_at is not None
    assert merged.locations, "jobLocation.address.streetAddress 를 못 읽었다"


def test_body_comes_from_html_not_jsonld_description(crawler, listing, detail):
    posting = hu.jobposting_from_jsonld(detail)
    seo_text = hu.jsonld_text(posting.get("description"))
    jobs, _ = crawler.parse_list(listing, page=1)
    body = crawler.merge_detail(jobs[0], detail).build_description()

    assert body
    assert body != seo_text
    assert "모집요강" in body or len(body) > len(seo_text or "")


@pytest.mark.parametrize(
    ("base_salary", "expected"),
    [
        ({"currency": "KRW", "value": {"value": 36000000, "unitText": "YEAR"}}, "3,600만원"),
        ({"currency": "KRW", "value": {"value": 3000000, "unitText": "MONTH"}}, "월 300만원"),
        ({"currency": "USD", "value": {"value": 80000, "unitText": "YEAR"}}, None),
        ({"currency": "KRW", "value": {"value": 0, "unitText": "YEAR"}}, None),
        (None, None),
    ],
)
def test_salary_text_from_jsonld(base_salary, expected):
    assert salary_text_from_jsonld(base_salary) == expected


def test_jsonld_salary_round_trips_through_shared_parser():
    text = salary_text_from_jsonld(
        {"currency": "KRW", "value": {"value": 36000000, "unitText": "YEAR"}}
    )
    assert parse_salary(text) == (3600, 3600, SalaryType.RANGE, SalaryPeriod.ANNUAL)


def test_monthly_jsonld_salary_is_annualized():
    text = salary_text_from_jsonld(
        {"currency": "KRW", "value": {"value": 3000000, "unitText": "MONTH"}}
    )
    low, high, salary_type, period = parse_salary(text)
    assert (low, high) == (3600, 3600)
    assert salary_type is SalaryType.RANGE
    assert period is SalaryPeriod.MONTHLY


def test_largest_text_block_finds_body_without_selectors():
    html = """
    <html><body>
      <nav><a>홈</a><a>채용</a></nav>
      <div id="wrap">
        <div class="x1y2z3">{body}</div>
        <aside>추천 공고</aside>
      </div>
    </body></html>
    """.replace("{body}", "자격요건 Java 개발 경험 " * 40)
    node = hu.largest_text_block(BeautifulSoup(html, "lxml"))
    assert node is not None
    assert "자격요건" in hu.block_text(node)
