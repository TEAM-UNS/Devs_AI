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
    """기술 분야. market.tech_field.code 와 1:1 대응.

    분류에 못 맞추는 공고는 field_id 를 NULL 로 둔다.
    "etc" 버킷을 만들면 매핑 실패와 진짜 기타 직무가 섞여서 구분되지 않는다.
    """

    BACKEND = "backend"
    FRONTEND = "frontend"
    MOBILE = "mobile"
    DATA_AI = "data_ai"
    DEVOPS = "devops"
    SECURITY = "security"
    GAME = "game"
    EMBEDDED = "embedded"


class Requirement(StrEnum):
    """공고가 스킬을 요구하는 강도. 신뢰도 순서는 tag > required > preferred > body.

    툴별 사용 등급 (명세 2-4)
        get_skill_gap        required + tag
        get_popular_skills   required + preferred + tag   (body 제외)
        get_rising_skills    required + preferred + tag
        get_company_profile  전부 (body 포함 — "이 회사가 쓰는 스택")
        compare_companies    required + tag
    """

    TAG = "tag"  # 사이트가 제공한 스택 태그 (원티드 · 점핏)
    REQUIRED = "required"  # 자격요건
    PREFERRED = "preferred"  # 우대사항
    BODY = "body"  # 주요업무 · 도입부의 단순 언급


class SalaryPeriod(StrEnum):
    """원문의 급여 기준. salary_min/max 는 항상 연봉 만원으로 환산해 저장한다."""

    ANNUAL = "annual"
    MONTHLY = "monthly"
    HOURLY = "hourly"  # 근무시간 미상이라 연환산 불가 → 통계 제외


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


# ═══════════════════════════════════════════════════════════════════════════
#  집계 정책 상수 — market/queries.py · similarity.py 가 쓴다
#
#  여기 값이 곧 "무엇을 신호로 볼 것인가" 다. 쿼리 안에 리터럴로 박으면
#  정책을 바꿀 때 여러 함수를 동시에 고쳐야 하고, 하나를 빠뜨려도 에러가
#  나지 않는다 — 숫자만 조용히 달라진다.
# ═══════════════════════════════════════════════════════════════════════════

# ── requirement 등급 세트 (명세 2-4) ────────────────────────────────────────
# 툴마다 어느 등급까지 신호로 볼지가 다르다. Requirement 의 docstring 에 산문으로
# 적혀 있던 표를 실행 가능한 상수로 옮긴 것이다.

# "요구 기술" — body(주요업무의 단순 언급)는 요구가 아니므로 뺀다.
#   get_popular_skills · get_rising_skills · get_stacks_by_segment · get_related_skills
REQUIREMENT_DEMAND: tuple[Requirement, ...] = (
    Requirement.TAG,
    Requirement.REQUIRED,
    Requirement.PREFERRED,
)

# "실제로 요구되는 것" — 우대사항까지 빼고 필수만. 학습 우선순위·기업 비교용.
#   get_skill_gap · compare_companies
REQUIREMENT_STRICT: tuple[Requirement, ...] = (Requirement.TAG, Requirement.REQUIRED)

# "이 회사가 쓰는 스택" — body 포함. "우리는 AWS 위에서 운영합니다" 는 요구사항이
# 아니지만 그 회사의 기술 정보로는 유효하다.
#   get_company_profile
REQUIREMENT_ALL: tuple[Requirement, ...] = tuple(Requirement)


# ── 연봉 통계 ───────────────────────────────────────────────────────────────
# 중앙값·사분위 집계에 넣을 salary_type.
#
# ★ max_only("4,000만원 이하")를 뺀 이유 — salary_min 이 NULL 이라 대푯값을
#   정할 수 없다. salary_max 로 세면 "이하" 를 상한값 그 자체로 취급해 통계가
#   위로 끌린다. min_only("2,600만원 이상")는 salary_min 이 있어서 "적어도
#   이만큼" 이라는 대푯값이 성립한다 — 국내 공고에서 압도적으로 흔한 표기이기도
#   하다. (models.py 의 부분 인덱스는 max_only 도 포함한다. 그건 "급여 정보가
#   있는 행" 을 고르는 인덱스라 범위가 더 넓은 것이고, 모순이 아니다.)
SALARY_STAT_TYPES: tuple[SalaryType, ...] = (SalaryType.RANGE, SalaryType.MIN_ONLY)

# salary_min/max 의 단위. 저장 시 연봉 만원으로 통일한다(월급은 ×12).
SALARY_UNIT = "만원"

# ★ disclosure_rate 의 분모는 SALARY_STAT_TYPES 가 아니라 **전체 공고수** 다.
#   집계 대상을 분모로 쓰면 공개율이 항상 1.0 이 된다.
#   breakdown 은 SalaryType 전체를 실어야 합계가 total_postings 와 맞는다
#   (max_only 포함 — 통계에서 뺐다고 집계 자체에서 빼면 숫자가 안 맞는다).


# ── 증감률 (get_rising_skills) ──────────────────────────────────────────────
# 증감률 = (this + K) / (last + K) - 1
#
# K 없이 나누면 지난주 0건이던 스킬이 이번주 1건만 나와도 증가율이 무한대가
# 되어 상위권을 전부 잡음이 차지한다. K=1 은 "1건은 우연일 수 있다" 를
# 표현하는 최소한의 스무딩이다.
RISING_SMOOTHING = 1

# 비교 구간. 이번 N일 vs 직전 N일.
RISING_WINDOW_DAYS = 7

# 이번 구간 공고수가 이보다 적으면 순위에서 제외한다. 스무딩만으로는
# 표본 3건짜리가 상위에 오는 것을 못 막는다.
RISING_MIN_COUNT = 5


# ── 동시출현 (get_related_skills) ───────────────────────────────────────────
# NPMI 계산에 넣을 최소 동시출현 횟수. 1~2회 함께 나온 쌍은 NPMI 가 높게
# 계산되기 쉬운데(희소할수록 PMI 가 커진다) 실제로는 우연이다.
NPMI_MIN_COOCCURRENCE = 3


# ── 표본 신뢰도 ─────────────────────────────────────────────────────────────
# 집계에 들어간 행이 이보다 적으면 결과에 low_confidence=true 를 함께 실어
# 보낸다. 툴 결과를 읽는 것은 사람이 아니라 LLM 이라, 근거를 주지 않으면
# 표본 3건짜리 중앙값도 단정적으로 말한다.
LOW_CONFIDENCE_SAMPLE_SIZE = 10


# ── 유사 기업 점수 (similarity.py) ──────────────────────────────────────────
# 점수 = Σ(가중치 × 성분). 합은 1.0 이어야 한다 — 아니면 점수가 0~1 을 벗어나
# find_similar_companies 의 임계값이 의미를 잃는다.
#
#   stack        두 기업의 요구 스킬 벡터(스킬별 공고 비중) 코사인
#   description  company.profile_embedding 코사인 (pgvector <=>)
#   size         employee_count 로그 거리. 없으면 size_type 구간 거리로 대체
#
# 스택에 가장 큰 가중치를 두는 것은 "비슷한 회사" 질문의 의도가 대개 기술
# 스택이기 때문이다. 설명 임베딩은 업종·문화를 잡아 주지만 채용 맥락에서는
# 보조 신호고, 규모는 같은 스택이면 갈리는 정도라 가장 작게 뒀다.
SIMILARITY_WEIGHTS: dict[str, float] = {
    "stack": 0.5,
    "description": 0.35,
    "size": 0.15,
}

if abs(sum(SIMILARITY_WEIGHTS.values()) - 1.0) > 1e-9:  # pragma: no cover - 기동 시 1회
    raise ValueError(f"SIMILARITY_WEIGHTS 합이 1.0 이 아닙니다: {SIMILARITY_WEIGHTS}")


# ── 헬퍼 ────────────────────────────────────────────────────────────────────
def sql_in(column: str, enum_cls: type[StrEnum]) -> str:
    """Enum 값으로 CHECK 제약 문자열을 만든다.

    sql_in("size_type", CompanySize)
    -> "size_type IN ('startup', 'small', ..., 'unknown')"
    """
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"
