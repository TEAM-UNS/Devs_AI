# 공고 데이터 Enum

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
