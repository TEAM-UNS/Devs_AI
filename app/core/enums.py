"""도메인 공용 Enum · 비즈니스 상수 (R5 — 여기에만 둔다).

여기 값은 init.sql 의 CHECK 제약과 반드시 일치해야 한다.
모델은 `sql_in()` 으로 CHECK 문자열을 이 Enum 에서 생성하므로,
Enum 을 고치면 DDL 도 같이 따라온다.
"""

from __future__ import annotations

from enum import StrEnum

# ── 임베딩 ──────────────────────────────────────────────────────────────────
# init.sql 의 vector(N) · .env 의 EMBED_DIM 과 반드시 같아야 한다.
EMBEDDING_DIM = 1024


# ── Enum ────────────────────────────────────────────────────────────────────
class CompanySize(StrEnum):
    """기업 규모 구간."""

    STARTUP = "startup"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    ENTERPRISE = "enterprise"
    UNKNOWN = "unknown"


class TechField(StrEnum):
    """기술 분야. market.tech_field.code 와 1:1 대응."""

    BACKEND = "backend"
    FRONTEND = "frontend"
    ANDROID = "android"
    IOS = "ios"
    DATA = "data"
    DEVOPS = "devops"
    AI = "ai"
    ETC = "etc"


class Requirement(StrEnum):
    """공고가 스킬을 요구하는 강도."""

    REQUIRED = "required"  # 자격요건
    PREFERRED = "preferred"  # 우대사항
    TAG = "tag"  # 사이트가 제공한 스택 태그 (원티드 · 점핏)


class ChunkSection(StrEnum):
    """임베딩 청크 섹션. 복지 · 전형절차는 청크로 만들지 않는다."""

    RESPONSIBILITY = "responsibility"
    REQUIRED = "required"
    PREFERRED = "preferred"


class SalaryType(StrEnum):
    """salary_raw 파싱 결과."""

    RANGE = "range"  # 3,000~4,000만원
    MIN_ONLY = "min_only"  # 2,600만원 이상
    MAX_ONLY = "max_only"  # 4,000만원 이하
    NEGOTIABLE = "negotiable"  # 회사내규에 따름 · 면접 후 결정
    UNKNOWN = "unknown"  # 파싱 불가


class CareerLevel(StrEnum):
    """경력 구간. 공고의 career_min/max 를 이 구간으로 접어서 집계한다."""

    NEWCOMER = "newcomer"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"


class CrawlSource(StrEnum):
    """수집 대상 사이트."""

    SARAMIN = "saramin"
    JOBKOREA = "jobkorea"
    WANTED = "wanted"
    JUMPIT = "jumpit"


class RunKind(StrEnum):
    """crawl_run 의 실행 종류."""

    CRAWL = "crawl"
    EMBED = "embed"


class RunStatus(StrEnum):
    """crawl_run 의 상태."""

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"  # 일부 사이트/페이지 실패
    FAILED = "failed"


class MessageRole(StrEnum):
    """chat_message.role."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


# ── 구간 상수 ───────────────────────────────────────────────────────────────
# 사원수 → CompanySize. (하한 포함, 위에서부터 처음 맞는 구간)
COMPANY_SIZE_BOUNDS: tuple[tuple[int, CompanySize], ...] = (
    (1000, CompanySize.ENTERPRISE),
    (300, CompanySize.LARGE),
    (100, CompanySize.MEDIUM),
    (30, CompanySize.SMALL),
    (0, CompanySize.STARTUP),
)

# 경력(년) → CareerLevel. (하한 포함, 위에서부터 처음 맞는 구간)
CAREER_LEVEL_BOUNDS: tuple[tuple[int, CareerLevel], ...] = (
    (8, CareerLevel.SENIOR),
    (4, CareerLevel.MID),
    (1, CareerLevel.JUNIOR),
    (0, CareerLevel.NEWCOMER),
)


# ── 헬퍼 ────────────────────────────────────────────────────────────────────
def sql_in(column: str, enum_cls: type[StrEnum]) -> str:
    """Enum 값으로 CHECK 제약 문자열을 만든다.

    sql_in("size_type", CompanySize)
    -> "size_type IN ('startup', 'small', ..., 'unknown')"
    """
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"
