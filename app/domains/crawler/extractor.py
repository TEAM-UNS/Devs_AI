"""정규화 · 기술스택 추출.

추출 파이프라인
    1. 본문을 섹션으로 분할 (자격요건 · 우대사항 · 주요업무 · 무시)
    2. 무시 섹션을 버린다.  ★ 이게 핵심이다.
       복지 섹션의 "Slack 으로 소통해요", "자바 개발서적 지원" 이 스택으로
       잡히면 집계가 통째로 오염된다.
    3. skill_alias 전체를 하나의 정규식으로 합쳐 매칭
       - 긴 별칭 우선 (JavaScript 가 Java 로 잡히면 안 된다)
       - 좌우 경계 검사 (ABC 안의 C 가 잡히면 안 된다)
       - C++ · C# · .NET · Node.js 처럼 특수문자가 든 별칭도 동작해야 한다
    4. 모호 스킬(Go · C · R)은 ±40자 안에 문맥 단서가 있을 때만 채택
    5. 등급 결정: tag > required > preferred > body

등급과 DB 값
    DB(posting_skill.requirement)는 required · preferred · tag 세 값만 갖는다.
    본문 등급(BODY)은 우선순위 계산에만 쓰고 저장할 때 preferred 로 내린다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from app.core import enums
from app.domains.market.seed_data import SKILL_CATALOG, SkillSeed

# ═══════════════════════════════════════════════════════════════════════════
#  섹션 분할
# ═══════════════════════════════════════════════════════════════════════════


class Section(StrEnum):
    RESPONSIBILITY = "responsibility"
    REQUIRED = "required"
    PREFERRED = "preferred"
    IGNORE = "ignore"
    BODY = "body"  # 헤더가 나오기 전 도입부


# 헤더로 인정할 최대 길이. 본문 문장 안의 "우대사항" 언급을 헤더로 오인하지 않는다.
HEADER_MAX_LEN = 60

# 위에서부터 먼저 맞는 것을 채택한다.
# IGNORE 가 가장 먼저다. "복리후생 및 근무조건" 같은 복합 헤더를 놓치지 않기 위함.
# PREFERRED 가 REQUIRED 보다 먼저다. "이런 분들을 환영해요(우대 조건)" 처럼
# 우대 헤더가 자격 표현을 함께 쓰는 경우가 많다.
_SECTION_HEADERS: tuple[tuple[Section, re.Pattern[str]], ...] = (
    (
        Section.IGNORE,
        re.compile(
            r"복리\s*후생|복지|근무\s*조건|근무\s*환경|근무\s*시간|채용\s*절차|전형\s*절차"
            r"|지원\s*방법|제출\s*서류|기타\s*사항|유의\s*사항|회사\s*소개|서비스\s*소개"
            r"|benefits?|welfare|perks|process",
            re.IGNORECASE,
        ),
    ),
    (
        Section.PREFERRED,
        re.compile(
            r"우대\s*사항|우대\s*조건|우대\s*요건|있으면\s*좋아요|environment"
            r"|이런\s*분(들)?(을|이라면|이면)?\s*(환영|더\s*좋)"
            r"|preferred|nice\s*to\s*have|plus",
            re.IGNORECASE,
        ),
    ),
    (
        Section.REQUIRED,
        re.compile(
            r"자격\s*요건|지원\s*자격|필수\s*요건|필수\s*조건|필수\s*역량|자격\s*조건"
            r"|이런\s*분(들)?(을|를)?\s*(찾|모)"
            r"|requirements?|qualifications?|must\s*have",
            re.IGNORECASE,
        ),
    ),
    (
        Section.RESPONSIBILITY,
        re.compile(
            r"주요\s*업무|담당\s*업무|업무\s*내용|수행\s*업무|이런\s*일을\s*해",
            re.IGNORECASE,
        ),
    ),
)


def detect_section(line: str) -> Section | None:
    """이 줄이 섹션 헤더면 해당 섹션을, 아니면 None 을 돌려준다."""
    stripped = line.strip()
    if not stripped or len(stripped) > HEADER_MAX_LEN:
        return None
    for section, pattern in _SECTION_HEADERS:
        if pattern.search(stripped):
            return section
    return None


def split_sections(text: str | None) -> list[tuple[Section, str]]:
    """본문을 (섹션, 내용) 목록으로 나눈다.

    헤더가 하나도 없으면 전체가 BODY 한 덩어리가 된다.
    헤더 줄 자체는 내용에서 제외한다. 헤더의 "경험" 같은 단어가 모호 스킬의
    문맥 단서로 오인되지 않게 하기 위함이다.
    """
    if not text or not text.strip():
        return []

    chunks: list[tuple[Section, list[str]]] = [(Section.BODY, [])]
    for line in text.splitlines():
        section = detect_section(line)
        if section is not None:
            chunks.append((section, []))
            continue
        chunks[-1][1].append(line)

    return [
        (section, "\n".join(lines).strip()) for section, lines in chunks if "\n".join(lines).strip()
    ]


# ═══════════════════════════════════════════════════════════════════════════
#  등급
# ═══════════════════════════════════════════════════════════════════════════


class Grade(IntEnum):
    """숫자가 클수록 신뢰도가 높다. 같은 스킬이 여러 곳에 나오면 최댓값을 쓴다."""

    BODY = 0
    PREFERRED = 1
    REQUIRED = 2
    TAG = 3  # 사이트가 직접 붙인 태그. 가장 믿을 만하다.

    def to_requirement(self) -> enums.Requirement:
        if self is Grade.TAG:
            return enums.Requirement.TAG
        if self is Grade.REQUIRED:
            return enums.Requirement.REQUIRED
        # BODY 는 DB 에 대응 값이 없다. 가장 약한 preferred 로 내린다.
        return enums.Requirement.PREFERRED


_SECTION_GRADE: dict[Section, Grade] = {
    Section.REQUIRED: Grade.REQUIRED,
    Section.PREFERRED: Grade.PREFERRED,
    Section.RESPONSIBILITY: Grade.BODY,
    Section.BODY: Grade.BODY,
}


# ═══════════════════════════════════════════════════════════════════════════
#  모호 스킬 문맥 검사
# ═══════════════════════════════════════════════════════════════════════════

AMBIGUOUS_WINDOW = 40

_CONTEXT_CLUES = re.compile(
    r"언어|개발|사용|경험|스택|활용|프로그래밍|구현|숙련|능숙|기반|프레임워크"
    r"|라이브러리|코드|코딩|서버|백엔드|프론트|이해도?"
    r"|language|develop|experience|programming",
    re.IGNORECASE,
)


def has_context_clue(text: str, start: int, end: int) -> bool:
    """매칭 위치 ±40자 안에 개발 문맥 단서가 있는지."""
    window = text[max(0, start - AMBIGUOUS_WINDOW) : end + AMBIGUOUS_WINDOW]
    return bool(_CONTEXT_CLUES.search(window))


# ═══════════════════════════════════════════════════════════════════════════
#  매처
# ═══════════════════════════════════════════════════════════════════════════

_KOREAN = re.compile(r"[가-힣]")


@dataclass(frozen=True)
class SkillEntry:
    skill_id: int
    name: str
    aliases: tuple[str, ...]
    is_ambiguous: bool = False


@dataclass(frozen=True)
class SkillHit:
    skill_id: int
    name: str
    requirement: enums.Requirement
    mentions: int
    grade: Grade


def _alias_pattern(alias: str) -> str:
    """별칭 하나를 경계 검사와 함께 감싼다.

    왼쪽  영숫자 뒤에 붙으면 안 된다 (ABC 의 C, ASP.NET 의 .NET).
          한글로 시작하는 별칭은 한글 뒤에 붙는 것도 막는다.
    오른쪽 영숫자가 이어지면 안 된다 (Java 뒤에 Script 가 오면 Java 가 아니다).
          한글은 막지 않는다. "Java를", "코틀린으로" 처럼 조사가 붙기 때문이다.
    """
    left = r"(?<![0-9A-Za-z가-힣])" if _KOREAN.match(alias[0]) else r"(?<![0-9A-Za-z])"
    return f"{left}{re.escape(alias)}(?![0-9A-Za-z])"


class SkillMatcher:
    """별칭 사전 하나를 정규식 하나로 합쳐 쓰는 매처.

    스킬이 수백 개가 되어도 본문을 한 번만 훑는다.
    """

    def __init__(self, entries: Sequence[SkillEntry]) -> None:
        self.entries = tuple(entries)
        self._by_id = {e.skill_id: e for e in entries}

        alias_to_id: dict[str, int] = {}
        for entry in entries:
            for alias in entry.aliases:
                key = alias.strip().lower()
                # 같은 별칭이 두 스킬에 걸리면 먼저 등록된 쪽을 유지한다.
                alias_to_id.setdefault(key, entry.skill_id)
        self._alias_to_id = alias_to_id

        # ★ 길이 내림차순. 같은 위치에서는 긴 별칭이 먼저 시도된다.
        #   ("javascript" 가 "java" 보다, "c++" 가 "c" 보다 먼저)
        ordered = sorted(alias_to_id, key=len, reverse=True)
        self._pattern = (
            re.compile("|".join(_alias_pattern(a) for a in ordered), re.IGNORECASE)
            if ordered
            else None
        )

    # ── 생성자 ────────────────────────────────────────────────────────────
    @classmethod
    def from_catalog(cls, catalog: Sequence[SkillSeed] = SKILL_CATALOG) -> SkillMatcher:
        """DB 없이 시드 카탈로그로 만든다 (테스트·오프라인용)."""
        return cls(
            [
                SkillEntry(
                    skill_id=index,
                    name=seed.name,
                    aliases=tuple(seed.all_aliases()),
                    is_ambiguous=seed.is_ambiguous,
                )
                for index, seed in enumerate(catalog, start=1)
            ]
        )

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[int, str, bool, Sequence[str]]]) -> SkillMatcher:
        """DB 조회 결과로 만든다. (skill_id, name, is_ambiguous, aliases)"""
        return cls(
            [
                SkillEntry(skill_id=sid, name=name, aliases=tuple(aliases), is_ambiguous=amb)
                for sid, name, amb, aliases in rows
            ]
        )

    # ── 매칭 ──────────────────────────────────────────────────────────────
    def find(self, text: str) -> list[tuple[SkillEntry, int, int]]:
        """(스킬, 시작, 끝) 목록. 모호 스킬 필터는 걸지 않는다."""
        if not text or self._pattern is None:
            return []
        hits: list[tuple[SkillEntry, int, int]] = []
        for match in self._pattern.finditer(text):
            skill_id = self._alias_to_id.get(match.group(0).lower())
            if skill_id is None:
                continue
            hits.append((self._by_id[skill_id], match.start(), match.end()))
        return hits

    def extract(
        self,
        *,
        description: str | None = None,
        tags: Sequence[str] = (),
    ) -> list[SkillHit]:
        """본문 + 사이트 태그에서 스킬을 뽑는다."""
        grades: dict[int, Grade] = {}
        mentions: dict[int, int] = defaultdict(int)

        def record(skill_id: int, grade: Grade, count: int = 1) -> None:
            grades[skill_id] = max(grades.get(skill_id, Grade.BODY), grade)
            mentions[skill_id] += count

        # 1) 사이트 태그 — 가장 신뢰도가 높다. 모호 스킬도 문맥 검사 없이 채택한다.
        for tag in tags:
            key = (tag or "").strip().lower()
            if not key:
                continue
            if (skill_id := self._alias_to_id.get(key)) is not None:
                record(skill_id, Grade.TAG)
                continue
            # "Apache Kafka" 처럼 태그 안에 별칭이 들어 있는 경우
            for entry, _, _ in self.find(tag):
                record(entry.skill_id, Grade.TAG)

        # 2) 본문 — 무시 섹션은 버린다
        for section, chunk in split_sections(description):
            if section is Section.IGNORE:
                continue
            grade = _SECTION_GRADE[section]
            for entry, start, end in self.find(chunk):
                if entry.is_ambiguous and not has_context_clue(chunk, start, end):
                    continue
                record(entry.skill_id, grade)

        return sorted(
            (
                SkillHit(
                    skill_id=skill_id,
                    name=self._by_id[skill_id].name,
                    requirement=grade.to_requirement(),
                    mentions=mentions[skill_id],
                    grade=grade,
                )
                for skill_id, grade in grades.items()
            ),
            key=lambda hit: (-hit.grade, hit.name),
        )


# ═══════════════════════════════════════════════════════════════════════════
#  기업명 정규화
# ═══════════════════════════════════════════════════════════════════════════
_CORP_TOKENS = re.compile(r"\(주\)|\(株\)|㈜|주식회사|유한회사|\(유\)", re.IGNORECASE)
_PARENS = re.compile(r"[\(\[（【][^\)\]）】]*[\)\]）】]")
_NON_WORD = re.compile(r"[\s\-_.,'\"·•]+")


def normalize_company_name(name: str) -> str:
    """사이트 간 기업 통합 키.

        "(주)드림어스컴퍼니 (Dreamus)" → "드림어스컴퍼니"

    주의: 동명 다른 기업이 합쳐질 수 있다. 그래서 company_source 에
    사이트별 식별자를 따로 남긴다.
    """
    text = _CORP_TOKENS.sub("", name or "")
    text = _PARENS.sub("", text)
    text = _NON_WORD.sub("", text)
    return text.strip().lower()


# ═══════════════════════════════════════════════════════════════════════════
#  직무 분류 → tech_field
# ═══════════════════════════════════════════════════════════════════════════
# 위에서부터 먼저 맞는 것을 채택한다. 순서가 곧 우선순위다.
#   - 보안 · 임베디드 · 게임을 먼저 본다. "게임 서버 개발자" 가 backend 로
#     빨려 들어가면 안 되기 때문이다.
#   - 데이터/AI 는 강한 단서(인공지능 · 빅데이터)만 먼저 보고, 약한 단서(DBA)는
#     맨 뒤로 미룬다. "DBA, devops, 서버/백엔드" 조합은 backend 가 맞다.
_FIELD_RULES: tuple[tuple[enums.TechField, tuple[str, ...]], ...] = (
    (enums.TechField.SECURITY, ("보안", "security", "해킹", "침해")),
    (enums.TechField.EMBEDDED, ("임베디드", "embedded", "펌웨어", "firmware", "hw", "제어")),
    (enums.TechField.GAME, ("게임", "game")),
    (
        enums.TechField.DATA_AI,
        (
            "인공지능",
            "머신러닝",
            "딥러닝",
            "빅데이터",
            "데이터 엔지니어",
            "데이터 사이언",
            "ai",
            "ml 엔지니어",
            "data engineer",
            "data scien",
        ),
    ),
    (enums.TechField.MOBILE, ("안드로이드", "android", "ios", "모바일", "앱개발", "크로스플랫폼")),
    (enums.TechField.FRONTEND, ("프론트", "frontend", "퍼블리셔", "웹 개발자")),
    (enums.TechField.BACKEND, ("서버", "백엔드", "backend", "풀스택")),
    (
        enums.TechField.DEVOPS,
        ("devops", "시스템 엔지니어", "인프라", "클라우드", "sre", "네트워크"),
    ),
    (enums.TechField.DATA_AI, ("dba", "데이터", "data")),
)


def map_tech_field(categories: list[str]) -> enums.TechField | None:
    """사이트 직무 분류 문자열 → 분야 코드.

    못 맞추면 None 이다. "기타" 버킷을 만들면 매핑 실패가 숨어버린다.
    """
    joined = " / ".join(categories).lower()
    if not joined.strip():
        return None
    for tech_field, keywords in _FIELD_RULES:
        if any(kw in joined for kw in keywords):
            return tech_field
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  기업 규모
# ═══════════════════════════════════════════════════════════════════════════
_SIZE_BY_TAG = {
    "대기업": enums.CompanySize.ENTERPRISE,
    "중견기업": enums.CompanySize.LARGE,
    "중소기업": enums.CompanySize.MEDIUM,
    "스타트업": enums.CompanySize.STARTUP,
}


def company_size_from_tags(tags: list[str]) -> enums.CompanySize:
    for tag in tags:
        if size := _SIZE_BY_TAG.get(tag.strip()):
            return size
    return enums.CompanySize.UNKNOWN


def normalize_skill_name(name: str) -> str:
    return " ".join((name or "").split()).strip()


# ═══════════════════════════════════════════════════════════════════════════
#  연봉
# ═══════════════════════════════════════════════════════════════════════════
_NEGOTIABLE = ("회사내규", "면접 후", "면접후", "협의", "추후 협의")
_NUM = r"(\d[\d,]*)"
_UNIT = r"\s*만\s*원?"
# "3,000~4,000만원" 처럼 단위가 뒤에만 붙는 표기가 흔하다.
# 앞쪽 단위를 필수로 두면 range 를 놓치고 뒤 숫자만 single 로 잡힌다.
_RANGE = re.compile(rf"{_NUM}(?:{_UNIT})?\s*[~\-–]\s*{_NUM}{_UNIT}")
_MIN_ONLY = re.compile(rf"{_NUM}{_UNIT}\s*이상")
_MAX_ONLY = re.compile(rf"{_NUM}{_UNIT}\s*이하")
_SINGLE = re.compile(rf"{_NUM}{_UNIT}")


def _to_int(text: str) -> int:
    return int(text.replace(",", ""))


def parse_salary(raw: str | None) -> tuple[int | None, int | None, enums.SalaryType]:
    """연봉 문자열 → (min, max, type). 단위는 만원.

        "3,000~4,000만원"   → (3000, 4000, range)
        "2,600만원 이상"     → (2600, None, min_only)
        "회사내규에 따름"     → (None, None, negotiable)
        파싱 불가            → (None, None, unknown)

    점핏은 급여 필드 자체가 없어 항상 unknown 이 된다.
    """
    if not raw or not raw.strip():
        return None, None, enums.SalaryType.UNKNOWN

    text = raw.strip()
    if any(token in text for token in _NEGOTIABLE):
        return None, None, enums.SalaryType.NEGOTIABLE

    if m := _RANGE.search(text):
        lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
        return (lo, hi, enums.SalaryType.RANGE) if lo <= hi else (hi, lo, enums.SalaryType.RANGE)
    if m := _MIN_ONLY.search(text):
        return _to_int(m.group(1)), None, enums.SalaryType.MIN_ONLY
    if m := _MAX_ONLY.search(text):
        return None, _to_int(m.group(1)), enums.SalaryType.MAX_ONLY
    if m := _SINGLE.search(text):
        value = _to_int(m.group(1))
        return value, value, enums.SalaryType.RANGE

    return None, None, enums.SalaryType.UNKNOWN


IMAGE_POSTING_MIN_CHARS = 200


def is_image_posting(description: str | None, has_image: bool) -> bool:
    """본문이 200자 미만인데 이미지가 있으면 이미지 공고로 본다."""
    return has_image and len((description or "").strip()) < IMAGE_POSTING_MIN_CHARS
