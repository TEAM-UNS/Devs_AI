"""market 테이블의 값 어휘. init.sql 의 CHECK 제약과 반드시 일치한다.

models.py 가 `sql_in()` 으로 CHECK 문자열을 여기서 생성하므로,
Enum 을 고치면 DDL 도 같이 따라온다.
"""

from enum import StrEnum


class CompanySize(StrEnum):
    STARTUP = "startup"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    ENTERPRISE = "enterprise"
    UNKNOWN = "unknown"


class TechField(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    MOBILE = "mobile"
    DATA_AI = "data_ai"
    DEVOPS = "devops"
    SECURITY = "security"
    GAME = "game"
    EMBEDDED = "embedded"


class Requirement(StrEnum):
    TAG = "tag"
    REQUIRED = "required"
    PREFERRED = "preferred"
    BODY = "body"


class SalaryPeriod(StrEnum):
    ANNUAL = "annual"
    MONTHLY = "monthly"
    HOURLY = "hourly"


class SalaryType(StrEnum):
    RANGE = "range"
    MIN_ONLY = "min_only"
    MAX_ONLY = "max_only"
    NEGOTIABLE = "negotiable"
    UNKNOWN = "unknown"


class CareerLevel(StrEnum):
    NEWCOMER = "newcomer"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"


class ChunkSection(StrEnum):
    RESPONSIBILITY = "responsibility"
    REQUIRED = "required"
    PREFERRED = "preferred"


class CrawlSource(StrEnum):
    SARAMIN = "saramin"
    JOBKOREA = "jobkorea"
    WANTED = "wanted"
    JUMPIT = "jumpit"


class RunKind(StrEnum):
    CRAWL = "crawl"
    EMBED = "embed"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


COMPANY_SIZE_BOUNDS: tuple[tuple[int, CompanySize], ...] = (
    (1000, CompanySize.ENTERPRISE),
    (300, CompanySize.LARGE),
    (100, CompanySize.MEDIUM),
    (30, CompanySize.SMALL),
    (0, CompanySize.STARTUP),
)


def sql_in(column: str, enum_cls: type[StrEnum]) -> str:
    return f"{column} IN (" + ", ".join(f"'{m.value}'" for m in enum_cls) + ")"
