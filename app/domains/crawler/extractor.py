# 본문 섹션 분할, 스킬 추출, 분야와 연봉 정규화

import functools
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from app.domains.crawler.seed_data import SkillSeed, skill_catalog
from app.domains.crawler import enums
from app.domains.crawler.schemas import SkillDictionaryRow
from typing import Optional


class Section(StrEnum):
    RESPONSIBILITY = "responsibility"
    REQUIRED = "required"
    PREFERRED = "preferred"
    IGNORE = "ignore"
    BODY = "body"


def section_header_pattern() -> re.Pattern[str]:
    return re.compile(
        r"자격\s*요건|지원\s*자격|필수\s*요건|우대\s*사항|우대\s*조건"
        r"|주요\s*업무|담당\s*업무|업무\s*내용|모집\s*요강"
        r"|requirements?|qualifications?|responsibilit",
        re.IGNORECASE,
    )


@functools.cache
def _default_matcher() -> "SkillMatcher":
    return SkillMatcher.from_catalog()


def is_valid_body(text: Optional[str], matcher: "Optional[SkillMatcher]" = None) -> bool:
    if not text or not text.strip():
        return False
    if section_header_pattern().search(text):
        return True
    return bool((matcher or _default_matcher()).extract(description=text))


def detect_section(line: str) -> Optional[Section]:
    stripped = line.strip()
    if not stripped or len(stripped) > 60:
        return None
    section_headers: tuple[tuple[Section, re.Pattern[str]], ...] = (
        (
            Section.IGNORE,
            re.compile(
                r"복리\s*후생|복지|근무\s*조건|근무\s*환경|근무\s*시간|채용\s*절차|전형\s*절차"
                r"|지원\s*방법|제출\s*서류|기타\s*사항|유의\s*사항|회사\s*소개|서비스\s*소개"
                # 영문 키워드는 줄 전체가 그것일 때만 헤더로 본다 (process 가 Processor 에 걸리지 않게)
                r"|^[\W\d_]*(?:hiring|recruit(?:ment)?|selection)?\s*"
                r"(?:process(?:es)?|benefits?|welfare|perks)"
                r"(?:\s*[&/]\s*(?:benefits?|perks))?[\W\d_]*$",
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
    for section, pattern in section_headers:
        if pattern.search(stripped):
            return section
    return None


def split_sections(text: Optional[str]) -> list[tuple[Section, str]]:
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


class Grade(IntEnum):
    BODY = 0
    PREFERRED = 1
    REQUIRED = 2
    TAG = 3

    def to_requirement(self) -> enums.Requirement:
        grade_to_requirement: dict[Grade, enums.Requirement] = {
            Grade.TAG: enums.Requirement.TAG,
            Grade.REQUIRED: enums.Requirement.REQUIRED,
            Grade.PREFERRED: enums.Requirement.PREFERRED,
            Grade.BODY: enums.Requirement.BODY,
        }
        return grade_to_requirement[self]


def has_context_clue(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 40) : end + 40]
    return bool(
        re.search(
            r"언어|개발|사용|경험|스택|활용|프로그래밍|구현|숙련|능숙|기반|프레임워크"
            r"|라이브러리|코드|코딩|서버|백엔드|프론트|이해도?"
            r"|language|develop|experience|programming",
            window,
            re.IGNORECASE,
        )
    )


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
    left = r"(?<![0-9A-Za-z가-힣])" if re.match(r"[가-힣]", alias[0]) else r"(?<![0-9A-Za-z])"
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

        self._pattern = self._compile(alias_to_id, re.IGNORECASE)
        self._cs_pattern = self._compile(cs_alias_to_id, 0)

    @staticmethod
    def _compile(aliases: dict[str, int], flags: int) -> Optional[re.Pattern[str]]:
        if not aliases:
            return None
        ordered = sorted(aliases, key=len, reverse=True)
        return re.compile("|".join(_alias_pattern(a) for a in ordered), flags)

    @classmethod
    def from_catalog(cls, catalog: Optional[Sequence[SkillSeed]] = None) -> "SkillMatcher":
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
                for index, seed in enumerate(skill_catalog() if catalog is None else catalog, start=1)
            ]
        )

    @classmethod
    def from_rows(cls, rows: Iterable[SkillDictionaryRow]) -> "SkillMatcher":
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
        description: Optional[str] = None,
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

        section_grade: dict[Section, Grade] = {
            Section.REQUIRED: Grade.REQUIRED,
            Section.PREFERRED: Grade.PREFERRED,
            Section.RESPONSIBILITY: Grade.BODY,
            Section.BODY: Grade.BODY,
        }
        for section, chunk in split_sections(description):
            if section is Section.IGNORE:
                continue
            grade = section_grade[section]
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


def normalize_company_name(name: str) -> str:
    text = re.sub(r"\(주\)|\(株\)|㈜|주식회사|유한회사|\(유\)", "", name or "", flags=re.IGNORECASE)
    text = re.sub(r"[\(\[（【][^\)\]）】]*[\)\]）】]", "", text)
    text = re.sub(r"[\s\-_.,'\"·•]+", "", text)
    return text.strip().lower()


def _keyword_hits(keyword: str, text: str) -> bool:
    if keyword in frozenset({"ai", "hw", "sre", "c", "ml 엔지니어"}):
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword in text


def _vote(text_value: str) -> Optional[enums.TechField]:
    # 웹개발, SW/솔루션 같은 포괄 카테고리는 넣지 않는다 (동점이 늘어 미분류가 는다)
    field_rules: tuple[tuple[enums.TechField, tuple[str, ...]], ...] = (
        (enums.TechField.SECURITY, ("보안", "security", "해킹", "침해")),
        (
            enums.TechField.EMBEDDED,
            (
                "임베디드",
                "embedded",
                "펌웨어",
                "firmware",
                "hw",
                "제어",
                "h/w",
                "hw/",
                "하드웨어",
                "반도체",
                "asic",
                "fpga",
                "회로",
            ),
        ),
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
        (enums.TechField.BACKEND, ("서버", "백엔드", "backend", "풀스택", "si개발", "si·", "si/")),
        (
            enums.TechField.DEVOPS,
            (
                "devops",
                "시스템 엔지니어",
                "인프라",
                "클라우드",
                "sre",
                "네트워크",
                "시스템엔지니어",
                "system engineer",
                "시스템관리",
                "서버관리",
            ),
        ),
        (enums.TechField.DATA_AI, ("dba", "데이터", "data")),
    )
    lowered = (text_value or "").lower()
    for tech_field, keywords in field_rules:
        if any(_keyword_hits(kw, lowered) for kw in keywords):
            return tech_field
    return None


def map_tech_field(categories: list[str], title: str = "") -> Optional[enums.TechField]:
    # 카테고리를 이어붙이지 말고 하나씩 투표한다 (이어붙이면 규칙 순서가 분야를 정해 버린다)
    votes: Counter[enums.TechField] = Counter()
    for category in categories:
        if (hit := _vote(category)) is not None:
            votes[hit] += 1

    if not votes:
        return _vote(title)

    top, count = votes.most_common(1)[0]
    tied = [field for field, vote_count in votes.items() if vote_count == count]
    if len(tied) == 1:
        return top

    from_title = _vote(title)
    return from_title if from_title in tied else None


def company_size_from_tags(tags: list[str]) -> enums.CompanySize:
    size_by_tag = {
        "대기업": enums.CompanySize.ENTERPRISE,
        "중견기업": enums.CompanySize.LARGE,
        "중소기업": enums.CompanySize.MEDIUM,
        "스타트업": enums.CompanySize.STARTUP,
    }
    for tag in tags:
        if size := size_by_tag.get(tag.strip()):
            return size
    return enums.CompanySize.UNKNOWN


def company_size_from_employee_count(count: Optional[int]) -> enums.CompanySize:
    if not count or count <= 0:
        return enums.CompanySize.UNKNOWN
    bounds: tuple[tuple[int, enums.CompanySize], ...] = (
        (1000, enums.CompanySize.ENTERPRISE),
        (300, enums.CompanySize.LARGE),
        (100, enums.CompanySize.MEDIUM),
        (30, enums.CompanySize.SMALL),
        (0, enums.CompanySize.STARTUP),
    )
    for lower_bound, size in bounds:
        if count >= lower_bound:
            return size
    return enums.CompanySize.UNKNOWN


def company_size(
    *, employee_count: Optional[int] = None, tags: Optional[list[str]] = None
) -> enums.CompanySize:
    if (size := company_size_from_employee_count(employee_count)) is not enums.CompanySize.UNKNOWN:
        return size
    return company_size_from_tags(tags or [])


def normalize_skill_name(name: str) -> str:
    return " ".join((name or "").split()).strip()


def _to_int(text: str) -> int:
    return int(text.replace(",", ""))


def parse_salary(
    raw: Optional[str],
) -> tuple[Optional[int], Optional[int], enums.SalaryType, Optional[enums.SalaryPeriod]]:
    unknown = (None, None, enums.SalaryType.UNKNOWN, None)
    if not raw or not raw.strip():
        return unknown

    text = raw.strip()

    negotiable = ("회사내규", "사내규정", "면접 후", "면접후", "협의", "추후 결정", "미정")
    if any(token in text for token in negotiable):
        return None, None, enums.SalaryType.NEGOTIABLE, None

    if re.search(r"[$€£¥₹]|\b(usd|eur|jpy|gbp|krw\s*equivalent)\b", text, re.IGNORECASE):
        return unknown

    if re.search(r"시급|시간\s*당|hourly|per\s*hour", text, re.IGNORECASE):
        return None, None, enums.SalaryType.UNKNOWN, enums.SalaryPeriod.HOURLY

    monthly = bool(re.search(r"월\s*급|월급여|월\s*\d|monthly|per\s*month", text, re.IGNORECASE))
    period = enums.SalaryPeriod.MONTHLY if monthly else enums.SalaryPeriod.ANNUAL
    plausible = range(50, 10_001) if monthly else range(1000, 100_001)

    def convert(value: int) -> Optional[int]:
        if value not in plausible:
            return None
        return value * 12 if monthly else value

    num = r"(\d[\d,]*)"
    unit = r"\s*만\s*원?"

    if m := re.search(rf"{num}(?:{unit})?\s*[~\-–]\s*{num}(?:{unit})?", text):
        low, high = sorted((_to_int(m.group(1)), _to_int(m.group(2))))
        low, high = convert(low), convert(high)
        if low is not None and high is not None:
            return low, high, enums.SalaryType.RANGE, period
        return unknown

    if m := re.search(rf"{num}{unit}?\s*이상", text):
        if (low := convert(_to_int(m.group(1)))) is not None:
            return low, None, enums.SalaryType.MIN_ONLY, period
        return unknown

    if m := re.search(rf"{num}{unit}?\s*이하", text):
        if (high := convert(_to_int(m.group(1)))) is not None:
            return None, high, enums.SalaryType.MAX_ONLY, period
        return unknown

    if m := re.search(rf"{num}{unit}?", text):
        if (value := convert(_to_int(m.group(1)))) is not None:
            return value, value, enums.SalaryType.RANGE, period
        return unknown

    return unknown


def is_image_posting(description: Optional[str], has_image: bool) -> bool:
    return has_image and len((description or "").strip()) < 200
