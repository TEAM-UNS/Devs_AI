# 사람인 파서 테스트

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.domains.crawler.sites import htmlutil as hu
from app.domains.crawler.sites.saramin import SaraminCrawler

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> BeautifulSoup:
    return BeautifulSoup((FIXTURES / name).read_text(encoding="utf-8"), "lxml")


@pytest.fixture(scope="module")
def crawler() -> SaraminCrawler:
    return SaraminCrawler()


@pytest.fixture(scope="module")
def listing() -> BeautifulSoup:
    return load("saramin_list.html")


@pytest.fixture(scope="module")
def detail() -> BeautifulSoup:
    return load("saramin_detail.html")


@pytest.fixture(scope="module")
def body() -> BeautifulSoup:
    return load("saramin_body.html")


def test_list_yields_jobs(crawler, listing):
    jobs, _ = crawler.parse_list(listing, page=1)
    assert len(jobs) == 5
    for job in jobs:
        assert job.source == "saramin"
        assert job.source_job_id.isdigit()
        assert job.title
        assert job.company_name
        assert job.url.startswith("https://www.saramin.co.kr")


def test_list_extracts_job_keywords_without_date_noise(crawler, listing):
    jobs, _ = crawler.parse_list(listing, page=1)
    keywords = [kw for job in jobs for kw in job.tech_stacks]
    assert keywords, "직무 키워드가 하나도 안 나왔다"
    assert not any("수정일" in kw for kw in keywords)
    assert not any(kw == "외" for kw in keywords)


def test_empty_list_raises_parse_error(crawler):
    with pytest.raises(Exception, match="찾지 못했"):
        crawler.parse_list(BeautifulSoup("<html><body></body></html>", "lxml"), page=1)


def test_detail_has_no_jsonld_jobposting(detail):
    assert hu.jobposting_from_jsonld(detail) is None


@pytest.mark.parametrize(
    "key",
    [
        "career",
        "education",
        "employment_type",
        "salary",
        "location",
        "expires_at",
        "employee",
        "company_type",
        "industry",
        "founded",
    ],
)
def test_label_value_extracts_each_field(detail, key):
    pairs = hu.label_value_pairs(detail)
    assert hu.pick(pairs, key), f"{key} 를 못 뽑았다"


def test_career_and_employee_are_parsed(detail):
    pairs = hu.label_value_pairs(detail)
    # 이 공고는 신입과 경력을 함께 뽑아서 경력 무관이 정답이다
    assert hu.pick(pairs, "career") is not None
    assert hu.parse_career(hu.pick(pairs, "career")) == (None, None)
    assert hu.parse_employee_count(hu.pick(pairs, "employee")) == 156


def test_body_text_preserves_section_headers(body):
    text = hu.block_text(body.select_one(".user_content"))
    assert len(text) > 500
    assert "\n" in text
    assert any(header in text for header in ("자격요건", "우대사항", "담당업무", "주요업무"))


def test_merge_detail_fills_posting(crawler, listing, detail, body):
    jobs, _ = crawler.parse_list(listing, page=1)
    merged = crawler.merge_detail(jobs[0], detail, body)

    assert merged.detail_fetched
    assert merged.company_name
    assert merged.company_employee_count and merged.company_employee_count > 0
    assert merged.company_industry
    assert merged.salary_raw
    assert merged.locations
    assert merged.build_description(), "본문이 비었다"


def test_merge_detail_keeps_list_values_when_detail_is_missing(crawler, listing, detail):
    jobs, _ = crawler.parse_list(listing, page=1)
    merged = crawler.merge_detail(jobs[0], detail, None)
    assert merged.title == jobs[0].title
    assert merged.tech_stacks == jobs[0].tech_stacks


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("경력 5년 ↑", (5, None)),
        ("경력 3~20년", (3, 20)),
        ("신입", (0, 0)),
        ("경력무관", (None, None)),
        ("신입·경력", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_career(text, expected):
    assert hu.parse_career(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("175 명 (2026년 기준)", 175),
        ("1,250명", 1250),
        ("", None),
        (None, None),
    ],
)
def test_parse_employee_count(text, expected):
    assert hu.parse_employee_count(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("597억 2,533만원 (2025년 기준)", 59_725_330_000),
        ("1조 2,000억원", 1_200_000_000_000),
        ("5,000만원", 50_000_000),
        (None, None),
    ],
)
def test_parse_revenue(text, expected):
    assert hu.parse_revenue(text) == expected


def test_image_posting_detection():
    soup = BeautifulSoup('<div><img src="a.jpg"><img src="b.jpg"></div>', "lxml")
    node = soup.div
    assert hu.looks_like_image_posting(node, "짧은 안내문")
    assert hu.collect_image_urls(node) == ["a.jpg", "b.jpg"]
    assert not hu.looks_like_image_posting(node, "가" * 300)
