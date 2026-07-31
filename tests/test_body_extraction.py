"""본문 추출 판정 회귀 테스트.

이 파일이 존재하는 이유
    잡코리아에서 페이지 안내문 543자가 "본문 있음" 으로 통과해 스킬 0개인
    공고 99건이 조용히 쌓였다. 길이로 본문을 판정했기 때문이다.
    같은 실패가 다시 조용히 지나가지 않도록 실제 스냅샷을 고정해 둔다.

fixture 는 data/raw 에서 고른 진짜 응답이다.
    jobkorea_body_ok1/ok2.html   섹션 헤더가 있는 정상 요강
    jobkorea_body_failed.html    안내문·네비게이션만 있는 건
    saramin_body_image.html      본문이 이미지 한 장인 공고
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.domains.crawler import extractor
from app.domains.crawler.sites import htmlutil as hu
from app.domains.crawler.sites.jobkorea import JobkoreaCrawler

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> BeautifulSoup:
    return BeautifulSoup((FIXTURES / name).read_text(encoding="utf-8", errors="replace"), "lxml")


# ═══════════════════════════════════════════════════════════════════════════
#  is_valid_body — 길이가 아니라 내용으로 판정한다
# ═══════════════════════════════════════════════════════════════════════════
def test_long_navigation_text_is_not_a_valid_body():
    """이게 이번 사고의 핵심이다. 길이만 보면 통과해 버린다."""
    noise = "회원가입 로그인 기업 서비스 JOB 찾기 합격축하금 공채정보 신입·인턴 " * 16
    assert len(noise) > 500  # 과거 기준(길이)이었다면 통과했을 분량
    assert not extractor.is_valid_body(noise)


def test_text_with_section_header_is_valid():
    assert extractor.is_valid_body("[자격요건]\n- 성실하신 분")


def test_short_text_with_skill_is_valid():
    """섹션 헤더가 없어도 스킬이 잡히면 본문으로 인정한다."""
    assert extractor.is_valid_body("Spring Boot 기반 API 개발")


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_text_is_not_valid(text):
    assert not extractor.is_valid_body(text)


# ═══════════════════════════════════════════════════════════════════════════
#  실제 스냅샷 회귀
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("name", ["jobkorea_body_ok1.html", "jobkorea_body_ok2.html"])
def test_jobkorea_valid_body_yields_skills(name):
    result = JobkoreaCrawler.extract_body(load(name))
    assert not result["body_extract_failed"]
    assert extractor.is_valid_body(result["responsibility"])


def test_jobkorea_navigation_only_page_is_marked_failed():
    """본문이 응답에 없는 건. 이미지 공고가 아니라 '추출 실패' 로 구분된다."""
    result = JobkoreaCrawler.extract_body(load("jobkorea_body_failed.html"))
    assert result["body_extract_failed"] is True
    assert result["body_is_image"] is False


def test_saramin_image_posting_is_flagged_as_image():
    """본문이 이미지 한 장. 추출 실패가 아니라 이미지 공고로 분류돼야 한다."""
    soup = load("saramin_body_image.html")
    node = soup.select_one(".user_content") or soup.body
    text = hu.block_text(node)
    is_image, failed, images = hu.classify_body(node, text, is_valid=extractor.is_valid_body(text))
    assert is_image is True
    assert failed is False
    assert images, "이미지 URL 을 저장해야 나중에 OCR 을 붙일 때 재수집이 필요 없다"


# ═══════════════════════════════════════════════════════════════════════════
#  보일러플레이트 제거 — 크롬이 본문에 섞이면 안 된다
# ═══════════════════════════════════════════════════════════════════════════
# 유효 판정된 본문만 검사한다. 실패 판정된 건은 description 이 집계에서
# 제외되므로(body_extract_failed) 잡음이 남아 있어도 오염되지 않는다.
@pytest.mark.parametrize("name", ["jobkorea_body_ok1.html", "jobkorea_body_ok2.html"])
def test_boilerplate_never_reaches_description(name):
    """합격자소서·AI추천공고가 본문에 들어가면 스킬 집계가 오염된다.

    개별 별칭을 빼는 대응은 두더지잡기라 소스에서 제거한다.
    """
    body = JobkoreaCrawler.extract_body(load(name))["responsibility"] or ""
    for noise in ("합격자소서", "AI추천공고", "AI면접", "인적성"):
        assert noise not in body, f"보일러플레이트 '{noise}' 가 본문에 섞였다"


def test_strip_boilerplate_removes_chrome():
    html = """
    <html><body>
      <nav>합격자소서 인적성 면접 후기</nav>
      <div class="recommend">AI추천공고</div>
      <aside class="banner">광고</aside>
      <main>자격요건 Java 개발 경험</main>
      <footer>회사소개 이용약관</footer>
    </body></html>
    """
    text = hu.block_text(hu.strip_boilerplate(BeautifulSoup(html, "lxml")))
    assert "자격요건" in text
    for noise in ("합격자소서", "AI추천공고", "광고", "이용약관"):
        assert noise not in text


# ═══════════════════════════════════════════════════════════════════════════
#  이미지 판정 — 로고를 공고 이미지로 오인하면 안 된다
# ═══════════════════════════════════════════════════════════════════════════
def test_company_logo_is_not_a_posting_image():
    """진단에서 102/103 이 …/LogoImage 였다. 이걸 이미지 공고로 세면 안 된다."""
    soup = BeautifulSoup(
        '<div><img src="https://file2.jobkorea.co.kr/Net/Mng/Image/LogoImage?FN=logo.png"></div>',
        "lxml",
    )
    node = soup.div
    assert hu.collect_image_urls(node) == []
    assert not hu.looks_like_image_posting(node, "짧은 안내문")


@pytest.mark.parametrize(
    "tag",
    [
        '<img src="/img/icon_scrap.png">',
        '<img src="/a.png" class="logo">',
        '<img src="/a.png" width="24" height="24">',
        '<img src="/a.png" alt="회사 로고">',
    ],
)
def test_decorative_images_are_excluded(tag):
    node = BeautifulSoup(f"<div>{tag}</div>", "lxml").div
    assert hu.collect_image_urls(node) == []


def test_real_posting_image_is_kept():
    node = BeautifulSoup(
        '<div><img src="https://cdn.example.com/recruit/2026/detail.jpg" width="800"></div>',
        "lxml",
    ).div
    assert hu.collect_image_urls(node) == ["https://cdn.example.com/recruit/2026/detail.jpg"]
    assert hu.looks_like_image_posting(node, "채용합니다")


# ═══════════════════════════════════════════════════════════════════════════
#  classify_body — 세 상태가 배타적인지
# ═══════════════════════════════════════════════════════════════════════════
def test_valid_body_sets_neither_flag():
    soup = BeautifulSoup("<div><img src='a.jpg'>자격요건 Java 개발</div>", "lxml")
    node = soup.div
    is_image, failed, images = hu.classify_body(node, hu.block_text(node), is_valid=True)
    assert (is_image, failed, images) == (False, False, [])


def test_invalid_body_without_image_is_failure_not_image():
    soup = BeautifulSoup("<div>회원가입 로그인 안내</div>", "lxml")
    node = soup.div
    is_image, failed, _ = hu.classify_body(node, hu.block_text(node), is_valid=False)
    assert (is_image, failed) == (False, True)
