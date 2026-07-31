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
from app.domains.market.schemas import SkillDictionaryRow
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


# 본문 유효성 판정용. 섹션 헤더가 하나라도 있으면 "요강"으로 본다.
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
    """이 텍스트가 '공고 본문'인지 판정한다.

    ★ 길이로 판정하지 않는다. 길이는 본문의 정의가 아니다.
      실제로 잡코리아에서 페이지 안내문 543자가 "정상"으로 통과해
      스킬 0개인 공고 99건이 조용히 쌓였다.

    섹션 헤더가 있거나 스킬이 하나라도 잡히면 본문으로 인정한다.
    둘 다 아니면 네비게이션·안내문을 잡은 것이다.
    """
    if not text or not text.strip():
        return False
    if SECTION_RE.search(text):
        return True
    return bool((matcher or _default_matcher()).extract(description=text))


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
        return _GRADE_TO_REQUIREMENT[self]


# 등급과 DB 값은 1:1 이다. body 가 preferred 로 뭉개지던 문제를 고치면서
# posting_skill.requirement 를 4값으로 늘렸다.
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
    aliases: tuple[str, ...]  # 대소문자 무시 (소문자로 보관)
    is_ambiguous: bool = False
    is_common: bool = False
    cs_aliases: tuple[str, ...] = ()  # 대소문자 구분 (표기 그대로)


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
        cs_alias_to_id: dict[str, int] = {}
        for entry in entries:
            for alias in entry.aliases:
                # 같은 별칭이 두 스킬에 걸리면 먼저 등록된 쪽을 유지한다.
                if key := alias.strip().lower():
                    alias_to_id.setdefault(key, entry.skill_id)
            for alias in entry.cs_aliases:
                if key := alias.strip():
                    cs_alias_to_id.setdefault(key, entry.skill_id)

        self._alias_to_id = alias_to_id
        self._cs_alias_to_id = cs_alias_to_id

        # ★ 길이 내림차순. 같은 위치에서는 긴 별칭이 먼저 시도된다.
        #   ("javascript" 가 "java" 보다, "c++" 가 "c" 보다 먼저)
        self._pattern = self._compile(alias_to_id, re.IGNORECASE)
        # 대소문자 구분 패턴은 따로 돌린다. 한 정규식에 두 모드를 섞을 수 없다.
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
        """DB 없이 시드 카탈로그로 만든다 (테스트·오프라인용)."""
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
        """DB 조회 결과로 만든다."""
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
        """(스킬, 시작, 끝) 목록. 모호 스킬 필터는 걸지 않는다.

        대소문자 무시/구분 두 패턴을 각각 돌린 뒤 합친다. 겹치는 구간은
        긴 쪽이 이긴다 ("C++" 이 있는데 "C" 를 따로 세면 안 된다).
        """
        if not text:
            return []

        raw: list[tuple[int, int, int]] = []  # (start, end, skill_id)
        if self._pattern is not None:
            for match in self._pattern.finditer(text):
                if (sid := self._alias_to_id.get(match.group(0).lower())) is not None:
                    raw.append((match.start(), match.end(), sid))
        if self._cs_pattern is not None:
            for match in self._cs_pattern.finditer(text):
                if (sid := self._cs_alias_to_id.get(match.group(0))) is not None:
                    raw.append((match.start(), match.end(), sid))

        # 시작 위치 오름차순, 같은 위치면 긴 것 먼저 → 겹치면 뒤엣것을 버린다
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


def company_size_from_employee_count(count: int | None) -> enums.CompanySize:
    """사원수 → 규모 구간. 구간 값은 core/enums.py 가 소유한다 (R5)."""
    if not count or count <= 0:
        return enums.CompanySize.UNKNOWN
    for lower_bound, size in enums.COMPANY_SIZE_BOUNDS:
        if count >= lower_bound:
            return size
    return enums.CompanySize.UNKNOWN


def company_size(
    *, employee_count: int | None = None, tags: list[str] | None = None
) -> enums.CompanySize:
    """규모 추정. 사원수가 있으면 그쪽이 정확하다.

    사이트 태그("대기업")는 자기 신고라 과장되는 경우가 있어 보조로만 쓴다.
    """
    if (size := company_size_from_employee_count(employee_count)) is not enums.CompanySize.UNKNOWN:
        return size
    return company_size_from_tags(tags or [])


def normalize_skill_name(name: str) -> str:
    return " ".join((name or "").split()).strip()


# ═══════════════════════════════════════════════════════════════════════════
#  연봉
# ═══════════════════════════════════════════════════════════════════════════
_NEGOTIABLE = ("회사내규", "사내규정", "면접 후", "면접후", "협의", "추후 결정", "미정")
# 외화는 환율·시점 문제가 있어 환산하지 않는다.
_FOREIGN = re.compile(r"[$€£¥₹]|\b(usd|eur|jpy|gbp|krw\s*equivalent)\b", re.IGNORECASE)
_HOURLY = re.compile(r"시급|시간\s*당|hourly|per\s*hour", re.IGNORECASE)
_MONTHLY = re.compile(r"월\s*급|월급여|월\s*\d|monthly|per\s*month", re.IGNORECASE)

_NUM = r"(\d[\d,]*)"
_UNIT = r"\s*만\s*원?"
# "3,000~4,000만원" 처럼 단위가 뒤에만 붙는 표기가 흔하다.
# 앞쪽 단위를 필수로 두면 range 를 놓치고 뒤 숫자만 single 로 잡힌다.
_RANGE = re.compile(rf"{_NUM}(?:{_UNIT})?\s*[~\-–]\s*{_NUM}(?:{_UNIT})?")
_MIN_ONLY = re.compile(rf"{_NUM}{_UNIT}?\s*이상")
_MAX_ONLY = re.compile(rf"{_NUM}{_UNIT}?\s*이하")
_SINGLE = re.compile(rf"{_NUM}{_UNIT}?")

MONTHS_PER_YEAR = 12
# 만원 단위로 그럴듯한 범위. 벗어나면 단위를 잘못 읽은 것으로 본다
# (예: "30,000,000" 은 원 단위 표기이므로 만원으로 취급하면 안 된다).
_PLAUSIBLE_ANNUAL = range(1000, 100_001)
_PLAUSIBLE_MONTHLY = range(50, 10_001)


def _to_int(text: str) -> int:
    return int(text.replace(",", ""))


def parse_salary(
    raw: str | None,
) -> tuple[int | None, int | None, enums.SalaryType, enums.SalaryPeriod | None]:
    """급여 문자열 → (min, max, type, period).

    ★ min/max 는 **항상 연봉 만원**이다. 월급 표기는 ×12 해서 저장한다.
      "월 300만원" 을 300 으로 저장하면 연봉 3,600 짜리가 300 으로 들어가
      중앙값이 통째로 망가진다.

        "3,000~4,000만원"    → (3000, 4000, range,      annual)
        "3000~4000"          → (3000, 4000, range,      annual)
        "2,600만원 이상"      → (2600, None, min_only,   annual)
        "월 300만원"          → (3600, 3600, range,      monthly)
        "시급 12,000원"       → (None, None, unknown,    hourly)
        "회사내규에 따름"      → (None, None, negotiable, None)
        "$80,000"            → (None, None, unknown,    None)

    hourly 는 근무시간을 모르면 연환산이 불가능해 금액을 버린다.
    """
    unknown = (None, None, enums.SalaryType.UNKNOWN, None)
    if not raw or not raw.strip():
        return unknown

    text = raw.strip()

    # 협의 표기가 최우선. "면접 후 결정 (3,000만원 수준)" 같은 혼합 표기는
    # 확정 금액이 아니므로 금액을 취하지 않는다.
    if any(token in text for token in _NEGOTIABLE):
        return None, None, enums.SalaryType.NEGOTIABLE, None

    # 외화는 환산하지 않는다. 기간도 알 수 없으므로 period 는 비운다.
    if _FOREIGN.search(text):
        return unknown

    # 시급은 기간만 남기고 금액은 버린다 (통계 제외 대상).
    if _HOURLY.search(text):
        return None, None, enums.SalaryType.UNKNOWN, enums.SalaryPeriod.HOURLY

    monthly = bool(_MONTHLY.search(text))
    period = enums.SalaryPeriod.MONTHLY if monthly else enums.SalaryPeriod.ANNUAL
    plausible = _PLAUSIBLE_MONTHLY if monthly else _PLAUSIBLE_ANNUAL

    def convert(value: int) -> int | None:
        """만원 단위 검증 후 연봉으로 환산."""
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
        # 단일 금액은 상·하한이 같은 range 로 본다 (명세 2-4 "월 300만원" 행).
        if (value := convert(_to_int(m.group(1)))) is not None:
            return value, value, enums.SalaryType.RANGE, period
        return unknown

    return unknown


IMAGE_POSTING_MIN_CHARS = 200


def is_image_posting(description: str | None, has_image: bool) -> bool:
    """본문이 200자 미만인데 이미지가 있으면 이미지 공고로 본다."""
    return has_image and len((description or "").strip()) < IMAGE_POSTING_MIN_CHARS
