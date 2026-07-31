"""스택 추출기 테스트.

DB 를 쓰지 않는다. 시드 카탈로그(SKILL_CATALOG)로 매처를 만들어 검증하므로,
별칭을 추가하면 이 테스트가 실제 사전을 그대로 검증한다.
"""

from __future__ import annotations

import pytest

from app.core.enums import Requirement, SalaryPeriod, SalaryType, TechField
from app.domains.crawler.extractor import (
    Grade,
    Section,
    SkillMatcher,
    map_tech_field,
    normalize_company_name,
    parse_salary,
    split_sections,
)
from app.domains.market.seed_data import SKILL_CATALOG

matcher = SkillMatcher.from_catalog()


def extract(description: str | None = None, tags: tuple[str, ...] = ()) -> dict[str, Requirement]:
    """스킬명 → requirement 로 납작하게 만든다."""
    return {
        hit.name: hit.requirement for hit in matcher.extract(description=description, tags=tags)
    }


def names(description: str | None = None, tags: tuple[str, ...] = ()) -> set[str]:
    return set(extract(description, tags))


# ═══════════════════════════════════════════════════════════════════════════
#  섹션 분할
# ═══════════════════════════════════════════════════════════════════════════
POSTING = """\
[주요업무]
- 백엔드 API 설계 및 운영

[자격요건]
- Java, Spring Boot 경험이 있으신 분

[우대사항]
- Kotlin, Kubernetes 경험이 있으신 분

[복리후생]
- Slack, Notion 으로 소통해요
- 자바 도서구입비를 지원합니다
"""


def test_split_sections_labels_each_block():
    sections = dict(split_sections(POSTING))
    assert set(sections) == {
        Section.RESPONSIBILITY,
        Section.REQUIRED,
        Section.PREFERRED,
        Section.IGNORE,
    }
    assert "Spring Boot" in sections[Section.REQUIRED]
    assert "Slack" in sections[Section.IGNORE]


def test_no_header_becomes_single_body_block():
    sections = split_sections("Python 으로 데이터 파이프라인을 만듭니다.")
    assert [s for s, _ in sections] == [Section.BODY]


# ═══════════════════════════════════════════════════════════════════════════
#  등급 부여
# ═══════════════════════════════════════════════════════════════════════════
def test_qualification_section_is_required():
    result = extract(POSTING)
    assert result["Java"] is Requirement.REQUIRED
    assert result["Spring Boot"] is Requirement.REQUIRED


def test_preferred_section_is_preferred():
    result = extract(POSTING)
    assert result["Kotlin"] is Requirement.PREFERRED
    assert result["Kubernetes"] is Requirement.PREFERRED


def test_ignore_section_is_dropped():
    """복지 섹션의 협업툴·도서지원 문구가 스택으로 잡히면 안 된다."""
    found = names(POSTING)
    assert "Notion" not in found
    assert "Slack" not in found
    # "자바 도서구입비" 의 자바도 복지 섹션이므로 제외된다.
    # Java 는 자격요건에서 잡힌 것이지 복지에서 잡힌 게 아니다.
    assert extract(POSTING)["Java"] is Requirement.REQUIRED


def test_welfare_only_skill_is_not_extracted():
    body = "[복리후생]\n- 자바 도서구입비 지원\n- Slack 으로 소통합니다"
    assert names(body) == set()


def test_site_tag_beats_body_grade():
    """사이트 태그는 최상위 등급이다."""
    result = extract(POSTING, tags=("Kotlin",))
    assert result["Kotlin"] is Requirement.TAG


def test_grade_priority_is_tag_then_required_then_preferred():
    assert Grade.TAG > Grade.REQUIRED > Grade.PREFERRED > Grade.BODY


# ═══════════════════════════════════════════════════════════════════════════
#  경계 · 긴 별칭 우선
# ═══════════════════════════════════════════════════════════════════════════
def test_javascript_is_not_java():
    found = names("[자격요건]\nJavaScript 개발 경험이 필요합니다")
    assert "JavaScript" in found
    assert "Java" not in found


def test_korean_alias_longest_match_wins():
    found = names("[자격요건]\n자바스크립트 개발 경험")
    assert "JavaScript" in found
    assert "Java" not in found


def test_cpp_is_not_c():
    found = names("[자격요건]\nC++ 개발 경험이 필요합니다")
    assert "C++" in found
    assert "C" not in found


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("C# 개발 경험", "C#"),
        (".NET 기반 서비스 개발 경험", "ASP.NET"),
        ("Node.js 사용 경험", "Node.js"),
        ("Next.js 개발 경험", "Next.js"),
    ],
)
def test_special_character_aliases(text, expected):
    assert expected in names(f"[자격요건]\n{text}")


def test_alias_inside_word_is_not_matched():
    """앞뒤가 영숫자면 매칭되지 않는다."""
    assert "C" not in names("[자격요건]\nABC 시스템 개발 경험")
    assert "R" not in names("[자격요건]\nHR 시스템 개발 경험")


def test_korean_particle_after_alias_still_matches():
    """ "Java를", "코틀린으로" 처럼 조사가 붙어도 잡혀야 한다."""
    found = names("[자격요건]\nJava를 사용하고 코틀린으로 전환한 경험")
    assert {"Java", "Kotlin"} <= found


# ═══════════════════════════════════════════════════════════════════════════
#  모호 스킬 문맥 검사
# ═══════════════════════════════════════════════════════════════════════════
def test_ambiguous_go_without_context_is_rejected():
    assert "Go" not in names("[자격요건]\nGo to the office")


def test_ambiguous_go_with_context_is_accepted():
    assert "Go" in names("[자격요건]\nGo 언어 개발 경험")


def test_ambiguous_c_with_context_is_accepted():
    assert "C" in names("[자격요건]\nC 언어로 펌웨어를 작성한 경험")


def test_ambiguous_skill_from_site_tag_skips_context_check():
    """사이트가 붙인 태그는 문맥 없이도 신뢰한다."""
    assert "Go" in names(None, tags=("Go",))


# ═══════════════════════════════════════════════════════════════════════════
#  mentions
# ═══════════════════════════════════════════════════════════════════════════
def test_mentions_counts_occurrences():
    body = "[자격요건]\nPython 개발 경험\n[우대사항]\nPython 으로 데이터 처리 경험"
    hit = next(h for h in matcher.extract(description=body) if h.name == "Python")
    assert hit.mentions == 2
    assert hit.requirement is Requirement.REQUIRED  # 더 높은 등급이 이긴다


# ═══════════════════════════════════════════════════════════════════════════
#  부가 정규화
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(주)드림어스컴퍼니", "드림어스컴퍼니"),
        ("주식회사 카카오", "카카오"),
        ("네이버 (NAVER)", "네이버"),
    ],
)
def test_normalize_company_name(raw, expected):
    assert normalize_company_name(raw) == expected


@pytest.mark.parametrize(
    ("categories", "expected"),
    [
        (["서버/백엔드 개발자"], TechField.BACKEND),
        (["DBA", "devops/시스템 엔지니어", "서버/백엔드 개발자"], TechField.BACKEND),
        (["게임 서버 개발자"], TechField.GAME),
        (["인공지능/머신러닝"], TechField.DATA_AI),
        (["안드로이드 개발자"], TechField.MOBILE),
        (["정보보안 담당자"], TechField.SECURITY),
        (["HW/임베디드"], TechField.EMBEDDED),
        (["개발 PM"], None),
        ([], None),
    ],
)
def test_map_tech_field(categories, expected):
    assert map_tech_field(categories) is expected


# 명세 2-4 "연봉 정규화 규칙" 표 전체.
# min/max 는 항상 연봉 만원 단위다.
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3,000~4,000만원", (3000, 4000, SalaryType.RANGE, SalaryPeriod.ANNUAL)),
        ("3000~4000", (3000, 4000, SalaryType.RANGE, SalaryPeriod.ANNUAL)),
        ("2,600만원 이상", (2600, None, SalaryType.MIN_ONLY, SalaryPeriod.ANNUAL)),
        ("월 300만원", (3600, 3600, SalaryType.RANGE, SalaryPeriod.MONTHLY)),
        ("시급 12,000원", (None, None, SalaryType.UNKNOWN, SalaryPeriod.HOURLY)),
        ("회사내규에 따름", (None, None, SalaryType.NEGOTIABLE, None)),
        ("면접 후 결정", (None, None, SalaryType.NEGOTIABLE, None)),
        ("협의 후 결정", (None, None, SalaryType.NEGOTIABLE, None)),
        ("$80,000", (None, None, SalaryType.UNKNOWN, None)),
        (None, (None, None, SalaryType.UNKNOWN, None)),
    ],
)
def test_parse_salary_normalization_table(raw, expected):
    assert parse_salary(raw) == expected


def test_monthly_salary_is_annualized():
    """월급이 그대로 저장되면 연봉 3,600 짜리가 300 으로 들어가 중앙값이 망가진다."""
    low, high, _, period = parse_salary("월 300만원")
    assert (low, high) == (3600, 3600)
    assert period is SalaryPeriod.MONTHLY


def test_hourly_amount_is_discarded():
    """시급은 근무시간을 몰라 연환산이 불가능하다. 기간만 남기고 금액은 버린다."""
    low, high, salary_type, period = parse_salary("시급 12,000원")
    assert (low, high) == (None, None)
    assert salary_type is SalaryType.UNKNOWN
    assert period is SalaryPeriod.HOURLY


def test_implausible_amount_is_rejected():
    """원 단위 표기를 만원으로 잘못 읽지 않는다."""
    assert parse_salary("30,000,000원")[:2] == (None, None)


# ═══════════════════════════════════════════════════════════════════════════
#  4값 등급
# ═══════════════════════════════════════════════════════════════════════════
def test_body_grade_is_no_longer_flattened_into_preferred():
    """주요업무에서 잡힌 스킬은 body 다. preferred 로 뭉개지면 안 된다."""
    result = extract(POSTING)
    # POSTING 의 주요업무는 "백엔드 API 설계 및 운영" 이라 스킬이 없다.
    body = "[주요업무]\n- Kubernetes 클러스터 위에서 서비스를 운영합니다"
    assert extract(body)["Kubernetes"] is Requirement.BODY
    # 자격요건은 그대로 required
    assert result["Java"] is Requirement.REQUIRED


def test_requirement_has_four_values():
    assert {r.value for r in Requirement} == {"tag", "required", "preferred", "body"}


# ═══════════════════════════════════════════════════════════════════════════
#  대소문자 구분 별칭
# ═══════════════════════════════════════════════════════════════════════════
def test_lowercase_can_in_english_sentence_is_not_matched():
    """ "you can use..." 의 can 이 차량용 CAN 으로 잡히면 안 된다."""
    body = "[자격요건]\nYou can use any language you prefer for development"
    assert "CAN" not in names(body)


def test_uppercase_can_is_matched():
    body = "[자격요건]\nCAN, UART 등 통신 인터페이스 개발 경험"
    assert "CAN" in names(body)


def test_lowercase_es_is_not_matched():
    assert "Elasticsearch" not in names("[자격요건]\nSe requieren es habilidades de desarrollo")


def test_uppercase_es_is_matched():
    assert "Elasticsearch" in names("[자격요건]\nES 클러스터 운영 경험")


def test_lowercase_single_letters_are_not_matched():
    """소문자 c · r 은 영어 문장에서 너무 흔하다."""
    body = "[자격요건]\nsection c and r of the development handbook"
    assert "C" not in names(body)
    assert "R" not in names(body)


# ═══════════════════════════════════════════════════════════════════════════
#  is_common
# ═══════════════════════════════════════════════════════════════════════════
def test_common_tools_are_flagged():
    flagged = {s.name for s in SKILL_CATALOG if s.is_common}
    assert flagged == {"Git", "Jira", "Slack", "Notion", "Confluence"}


def test_figma_and_linux_are_not_common():
    """직군 신호가 있는 도구는 트렌드에서 빼면 정보가 사라진다."""
    not_common = {s.name for s in SKILL_CATALOG if not s.is_common}
    assert {"Figma", "Linux"} <= not_common


def test_common_tools_are_still_extracted():
    """추출은 한다. 제외는 집계 쿼리(get_popular_skills)의 몫이다."""
    assert "Slack" in names("[자격요건]\nSlack 으로 협업한 경험")
