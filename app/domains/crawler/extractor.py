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

from app.domains.market import enums
from app.domains.market.schemas import SkillDictionaryRow
from app.domains.market.seed_data import SKILL_CATALOG, SkillSeed

# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════


class Section(StrEnum):
    RESPONSIBILITY = "responsibility"
    REQUIRED = "required"
    PREFERRED = "preferred"
    IGNORE = "ignore"
    BODY = "body"


HEADER_MAX_LEN = 60

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


SECTION_RE = re.compile(
    r"자격\s*요건|지원\s*자격|필수\s*요건|우대\s*사항|우대\s*조건"
    r"|주요\s*업무|담당\s*업무|업무\s*내용|모집\s*요강"
    r"|requirements?|qualifications?|responsibilit",
    re.IGNORECASE,
)

_DEFAULT_MATCHER: SkillMatcher | None = None


def _default_matcher() -> SkillMatcher:
    global _DEFAULT_MATCHER
    if _DEFAULT_MATCHER is None:
        _DEFAULT_MATCHER = SkillMatcher.from_catalog()
    return _DEFAULT_MATCHER


def is_valid_body(text: str | None, matcher: SkillMatcher | None = None) -> bool:
    if not text or not text.strip():
        return False
    if SECTION_RE.search(text):
        return True
    return bool((matcher or _default_matcher()).extract(description=text))


def detect_section(line: str) -> Section | None:
    stripped = line.strip()
    if not stripped or len(stripped) > HEADER_MAX_LEN:
        return None
    for section, pattern in _SECTION_HEADERS:
        if pattern.search(stripped):
            return section
    return None


def split_sections(text: str | None) -> list[tuple[Section, str]]:
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
# ═══════════════════════════════════════════════════════════════════════════


class Grade(IntEnum):
    BODY = 0
    PREFERRED = 1
    REQUIRED = 2
    TAG = 3

    def to_requirement(self) -> enums.Requirement:
        return _GRADE_TO_REQUIREMENT[self]


_GRADE_TO_REQUIREMENT: dict[Grade, enums.Requirement] = {
    Grade.TAG: enums.Requirement.TAG,
    Grade.REQUIRED: enums.Requirement.REQUIRED,
    Grade.PREFERRED: enums.Requirement.PREFERRED,
    Grade.BODY: enums.Requirement.BODY,
}

_SECTION_GRADE: dict[Section, Grade] = {
    Section.REQUIRED: Grade.REQUIRED,
    Section.PREFERRED: Grade.PREFERRED,
    Section.RESPONSIBILITY: Grade.BODY,
    Section.BODY: Grade.BODY,
}


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════

AMBIGUOUS_WINDOW = 40

_CONTEXT_CLUES = re.compile(
    r"언어|개발|사용|경험|스택|활용|프로그래밍|구현|숙련|능숙|기반|프레임워크"
    r"|라이브러리|코드|코딩|서버|백엔드|프론트|이해도?"
    r"|language|develop|experience|programming",
    re.IGNORECASE,
)


def has_context_clue(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - AMBIGUOUS_WINDOW) : end + AMBIGUOUS_WINDOW]
    return bool(_CONTEXT_CLUES.search(window))


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════

_KOREAN = re.compile(r"[가-힣]")


@dataclass(frozen=True)
class SkillEntry:
    skill_id: int
    name: str
    aliases: tuple[str, ...]
    is_ambiguous: bool = False
    is_common: bool = False
    cs_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillHit:
    skill_id: int
    name: str
    requirement: enums.Requirement
    mentions: int
    grade: Grade


def _alias_pattern(alias: str) -> str:
    left = r"(?<![0-9A-Za-z가-힣])" if _KOREAN.match(alias[0]) else r"(?<![0-9A-Za-z])"
    return f"{left}{re.escape(alias)}(?![0-9A-Za-z])"


class SkillMatcher:
    def __init__(self, entries: Sequence[SkillEntry]) -> None:
        self.entries = tuple(entries)
        self._by_id = {e.skill_id: e for e in entries}

        alias_to_id: dict[str, int] = {}
        cs_alias_to_id: dict[str, int] = {}
        for entry in entries:
            for alias in entry.aliases:
                if key := alias.strip().lower():
                    alias_to_id.setdefault(key, entry.skill_id)
            for alias in entry.cs_aliases:
                if key := alias.strip():
                    cs_alias_to_id.setdefault(key, entry.skill_id)

        self._alias_to_id = alias_to_id
        self._cs_alias_to_id = cs_alias_to_id

        # ★ 길이 내림차순. 같은 위치에서는 긴 별칭이 먼저 시도된다.
        self._pattern = self._compile(alias_to_id, re.IGNORECASE)
        self._cs_pattern = self._compile(cs_alias_to_id, 0)

    @staticmethod
    def _compile(aliases: dict[str, int], flags: int) -> re.Pattern[str] | None:
        if not aliases:
            return None
        ordered = sorted(aliases, key=len, reverse=True)
        return re.compile("|".join(_alias_pattern(a) for a in ordered), flags)

    # ── 생성자 ────────────────────────────────────────────────────────────
    @classmethod
    def from_catalog(cls, catalog: Sequence[SkillSeed] = SKILL_CATALOG) -> SkillMatcher:
        return cls(
            [
                SkillEntry(
                    skill_id=index,
                    name=seed.name,
                    aliases=tuple(seed.all_aliases()),
                    is_ambiguous=seed.is_ambiguous,
                    is_common=seed.is_common,
                    cs_aliases=tuple(seed.all_cs_aliases()),
                )
                for index, seed in enumerate(catalog, start=1)
            ]
        )

    @classmethod
    def from_rows(cls, rows: Iterable[SkillDictionaryRow]) -> SkillMatcher:
        return cls(
            [
                SkillEntry(
                    skill_id=row.skill_id,
                    name=row.name,
                    aliases=tuple(row.aliases),
                    is_ambiguous=row.is_ambiguous,
                    is_common=row.is_common,
                    cs_aliases=tuple(row.cs_aliases),
                )
                for row in rows
            ]
        )

    # ── 매칭 ──────────────────────────────────────────────────────────────
    def find(self, text: str) -> list[tuple[SkillEntry, int, int]]:
        if not text:
            return []

        raw: list[tuple[int, int, int]] = []
        if self._pattern is not None:
            for match in self._pattern.finditer(text):
                if (sid := self._alias_to_id.get(match.group(0).lower())) is not None:
                    raw.append((match.start(), match.end(), sid))
        if self._cs_pattern is not None:
            for match in self._cs_pattern.finditer(text):
                if (sid := self._cs_alias_to_id.get(match.group(0))) is not None:
                    raw.append((match.start(), match.end(), sid))

        raw.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        hits: list[tuple[SkillEntry, int, int]] = []
        consumed_until = -1
        for start, end, skill_id in raw:
            if start < consumed_until:
                continue
            hits.append((self._by_id[skill_id], start, end))
            consumed_until = end
        return hits

    def extract(
        self,
        *,
        description: str | None = None,
        tags: Sequence[str] = (),
    ) -> list[SkillHit]:
        grades: dict[int, Grade] = {}
        mentions: dict[int, int] = defaultdict(int)

        def record(skill_id: int, grade: Grade, count: int = 1) -> None:
            grades[skill_id] = max(grades.get(skill_id, Grade.BODY), grade)
            mentions[skill_id] += count

        for tag in tags:
            key = (tag or "").strip().lower()
            if not key:
                continue
            if (skill_id := self._alias_to_id.get(key)) is not None:
                record(skill_id, Grade.TAG)
                continue
            for entry, _, _ in self.find(tag):
                record(entry.skill_id, Grade.TAG)

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
# ═══════════════════════════════════════════════════════════════════════════
_CORP_TOKENS = re.compile(r"\(주\)|\(株\)|㈜|주식회사|유한회사|\(유\)", re.IGNORECASE)
_PARENS = re.compile(r"[\(\[（【][^\)\]）】]*[\)\]）】]")
_NON_WORD = re.compile(r"[\s\-_.,'\"·•]+")


def normalize_company_name(name: str) -> str:
    text = _CORP_TOKENS.sub("", name or "")
    text = _PARENS.sub("", text)
    text = _NON_WORD.sub("", text)
    return text.strip().lower()


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
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
    joined = " / ".join(categories).lower()
    if not joined.strip():
        return None
    for tech_field, keywords in _FIELD_RULES:
        if any(kw in joined for kw in keywords):
            return tech_field
    return None


# ═══════════════════════════════════════════════════════════════════════════
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


def company_size_from_employee_count(count: int | None) -> enums.CompanySize:
    if not count or count <= 0:
        return enums.CompanySize.UNKNOWN
    for lower_bound, size in enums.COMPANY_SIZE_BOUNDS:
        if count >= lower_bound:
            return size
    return enums.CompanySize.UNKNOWN


def company_size(
    *, employee_count: int | None = None, tags: list[str] | None = None
) -> enums.CompanySize:
    if (size := company_size_from_employee_count(employee_count)) is not enums.CompanySize.UNKNOWN:
        return size
    return company_size_from_tags(tags or [])


def normalize_skill_name(name: str) -> str:
    return " ".join((name or "").split()).strip()


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
_NEGOTIABLE = ("회사내규", "사내규정", "면접 후", "면접후", "협의", "추후 결정", "미정")
_FOREIGN = re.compile(r"[$€£¥₹]|\b(usd|eur|jpy|gbp|krw\s*equivalent)\b", re.IGNORECASE)
_HOURLY = re.compile(r"시급|시간\s*당|hourly|per\s*hour", re.IGNORECASE)
_MONTHLY = re.compile(r"월\s*급|월급여|월\s*\d|monthly|per\s*month", re.IGNORECASE)

_NUM = r"(\d[\d,]*)"
_UNIT = r"\s*만\s*원?"
_RANGE = re.compile(rf"{_NUM}(?:{_UNIT})?\s*[~\-–]\s*{_NUM}(?:{_UNIT})?")
_MIN_ONLY = re.compile(rf"{_NUM}{_UNIT}?\s*이상")
_MAX_ONLY = re.compile(rf"{_NUM}{_UNIT}?\s*이하")
_SINGLE = re.compile(rf"{_NUM}{_UNIT}?")

MONTHS_PER_YEAR = 12
_PLAUSIBLE_ANNUAL = range(1000, 100_001)
_PLAUSIBLE_MONTHLY = range(50, 10_001)


def _to_int(text: str) -> int:
    return int(text.replace(",", ""))


def parse_salary(
    raw: str | None,
) -> tuple[int | None, int | None, enums.SalaryType, enums.SalaryPeriod | None]:
    unknown = (None, None, enums.SalaryType.UNKNOWN, None)
    if not raw or not raw.strip():
        return unknown

    text = raw.strip()

    if any(token in text for token in _NEGOTIABLE):
        return None, None, enums.SalaryType.NEGOTIABLE, None

    if _FOREIGN.search(text):
        return unknown

    if _HOURLY.search(text):
        return None, None, enums.SalaryType.UNKNOWN, enums.SalaryPeriod.HOURLY

    monthly = bool(_MONTHLY.search(text))
    period = enums.SalaryPeriod.MONTHLY if monthly else enums.SalaryPeriod.ANNUAL
    plausible = _PLAUSIBLE_MONTHLY if monthly else _PLAUSIBLE_ANNUAL

    def convert(value: int) -> int | None:
        if value not in plausible:
            return None
        return value * MONTHS_PER_YEAR if monthly else value

    if m := _RANGE.search(text):
        low, high = sorted((_to_int(m.group(1)), _to_int(m.group(2))))
        low, high = convert(low), convert(high)
        if low is not None and high is not None:
            return low, high, enums.SalaryType.RANGE, period
        return unknown

    if m := _MIN_ONLY.search(text):
        if (low := convert(_to_int(m.group(1)))) is not None:
            return low, None, enums.SalaryType.MIN_ONLY, period
        return unknown

    if m := _MAX_ONLY.search(text):
        if (high := convert(_to_int(m.group(1)))) is not None:
            return None, high, enums.SalaryType.MAX_ONLY, period
        return unknown

    if m := _SINGLE.search(text):
        if (value := convert(_to_int(m.group(1)))) is not None:
            return value, value, enums.SalaryType.RANGE, period
        return unknown

    return unknown


IMAGE_POSTING_MIN_CHARS = 200


def is_image_posting(description: str | None, has_image: bool) -> bool:
    return has_image and len((description or "").strip()) < IMAGE_POSTING_MIN_CHARS
