"""사람인 파서 — 저장된 실제 HTML 로 검증한다.

fixture 는 2026-07-30 에 받은 진짜 응답이다. 네트워크를 타지 않는다.
사이트가 개편되면 이 테스트가 먼저 깨져서 알려준다.

    saramin_list.html   /zf_user/search 목록 (item_recruit 5개만 남김)
    saramin_detail.html /zf_user/jobs/relay/view-ajax (조건 + 기업정보)
    saramin_body.html   /zf_user/jobs/relay/view-detail (iframe 본문)
"""

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
    # 네트워크를 열지 않는다. 파싱 메서드만 쓴다.
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


# ═══════════════════════════════════════════════════════════════════════════
#  목록
# ═══════════════════════════════════════════════════════════════════════════
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
    """.job_sector 안에 "수정일 26/07/21" 이 섞여 있다. 키워드로 새면 안 된다."""
    jobs, _ = crawler.parse_list(listing, page=1)
    keywords = [kw for job in jobs for kw in job.tech_stacks]
    assert keywords, "직무 키워드가 하나도 안 나왔다"
    assert not any("수정일" in kw for kw in keywords)
    assert not any(kw == "외" for kw in keywords)


def test_empty_list_raises_parse_error(crawler):
    """0건은 '검색 결과 없음'이 아니라 셀렉터가 깨진 것으로 본다."""
    with pytest.raises(Exception, match="찾지 못했"):
        crawler.parse_list(BeautifulSoup("<html><body></body></html>", "lxml"), page=1)


# ═══════════════════════════════════════════════════════════════════════════
#  상세 — 라벨-값 (2순위 폴백)
# ═══════════════════════════════════════════════════════════════════════════
def test_detail_has_no_jsonld_jobposting(detail):
    """사람인은 JobPosting JSON-LD 를 심지 않는다. 2순위가 주력인 이유."""
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
    # 이 fixture 공고는 "신입·경력" 이라 경력 무관이다 → (None, None) 이 정답.
    # 경력 구간 파싱 자체는 test_parse_career 가 표로 검증한다.
    assert hu.pick(pairs, "career") is not None
    assert hu.parse_career(hu.pick(pairs, "career")) == (None, None)
    assert hu.parse_employee_count(hu.pick(pairs, "employee")) == 156


# ═══════════════════════════════════════════════════════════════════════════
#  본문 — iframe
# ═══════════════════════════════════════════════════════════════════════════
def test_body_text_preserves_section_headers(body):
    """br 을 개행으로 살려야 추출기가 섹션을 나눌 수 있다."""
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
    """본문 수집이 실패해도 목록에서 얻은 값은 살아 있어야 한다."""
    jobs, _ = crawler.parse_list(listing, page=1)
    merged = crawler.merge_detail(jobs[0], detail, None)
    assert merged.title == jobs[0].title
    assert merged.tech_stacks == jobs[0].tech_stacks


# ═══════════════════════════════════════════════════════════════════════════
#  값 정규화 유틸
# ═══════════════════════════════════════════════════════════════════════════
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
    # 본문이 충분히 길면 이미지가 있어도 이미지 공고가 아니다
    assert not hu.looks_like_image_posting(node, "가" * 300)
