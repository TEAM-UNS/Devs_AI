-- ═══════════════════════════════════════════════════════════════════════════
--  jobstack ai-service · 초기 스키마
--
--  실행: docker-entrypoint-initdb.d 에 마운트되어 최초 1회 자동 실행
--        (POSTGRES_USER 권한 = 스키마 소유자)
--
--  구성
--    extension     vector, pg_trgm, pgcrypto
--    schema market  수집·집계 데이터 (crawler 가 쓰고, chat 은 읽기만)
--    schema chat    대화 데이터 (chat 전용, LangGraph checkpointer 포함)
--    role          ai_crawler(market RW) / ai_chat(market RO + chat RW)
--
--  벡터 차원은 1024 로 고정한다 (.env 의 EMBED_DIM 과 반드시 일치).
--  차원을 바꾸려면 컬럼 타입 변경 + 전체 재임베딩이 필요하다.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS chat;

SET search_path = market, chat, public;


-- ═══════════════════════════════════════════════════════════════════════════
--  공용: updated_at 자동 갱신
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ═══════════════════════════════════════════════════════════════════════════
--  market · 분류 체계
-- ═══════════════════════════════════════════════════════════════════════════

-- 기술 분야. core/enums.py 의 TechField 와 code 가 1:1 대응한다.
CREATE TABLE market.tech_field (
    id          serial       PRIMARY KEY,
    code        varchar(32)  NOT NULL UNIQUE,   -- backend · frontend · android · ios · data · devops · ai
    name        varchar(64)  NOT NULL,          -- 화면 표시명
    sort_order  int          NOT NULL DEFAULT 0,
    created_at  timestamptz  NOT NULL DEFAULT now()
);

INSERT INTO market.tech_field (code, name, sort_order) VALUES
    ('backend',   '백엔드',        10),
    ('frontend',  '프론트엔드',    20),
    ('android',   '안드로이드',    30),
    ('ios',       'iOS',           40),
    ('data',      '데이터',        50),
    ('devops',    'DevOps/인프라', 60),
    ('ai',        'AI/ML',         70),
    ('etc',       '기타',          99)
ON CONFLICT (code) DO NOTHING;


-- ═══════════════════════════════════════════════════════════════════════════
--  market · 기업
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE market.company (
    id                bigserial     PRIMARY KEY,

    -- 괄호·"주식회사"·공백 제거한 정규화 이름. 사이트 간 기업 통합의 기준.
    name_key          varchar(200)  NOT NULL UNIQUE,
    name              varchar(200)  NOT NULL,

    description       text,          -- 기업 소개
    business_content  text,          -- 사업 내용
    talent_profile    text,          -- 인재상

    size_type         varchar(20)    NOT NULL DEFAULT 'unknown',
    employee_count    int,
    industry          varchar(120),
    founded           varchar(20),   -- "2014" · "2014-03" 등 표기가 제각각이라 문자열 보관
    revenue           bigint,        -- 원 단위
    homepage          varchar(500),

    -- description + business_content + industry 를 합쳐 임베딩
    profile_embedding vector(1024),
    embed_hash        char(64),      -- 위 3개 필드 해시. 다르면 재임베딩 대상

    raw_fields        jsonb         NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz   NOT NULL DEFAULT now(),
    updated_at        timestamptz   NOT NULL DEFAULT now(),

    CONSTRAINT company_size_type_chk CHECK (
        size_type IN ('startup', 'small', 'medium', 'large', 'enterprise', 'unknown')
    ),
    CONSTRAINT company_employee_count_chk CHECK (employee_count IS NULL OR employee_count >= 0)
);

CREATE INDEX company_name_trgm_idx  ON market.company USING gin (name gin_trgm_ops);
CREATE INDEX company_size_type_idx  ON market.company (size_type);
-- 프로필 임베딩 미완료분 백필용
CREATE INDEX company_embed_todo_idx ON market.company (id) WHERE profile_embedding IS NULL;

CREATE TRIGGER company_touch BEFORE UPDATE ON market.company
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();


-- 같은 기업이 사이트마다 다른 식별자를 갖는다. 오병합 추적용 원장.
CREATE TABLE market.company_source (
    id                bigserial    PRIMARY KEY,
    company_id        bigint       NOT NULL REFERENCES market.company(id) ON DELETE CASCADE,
    source            varchar(20)  NOT NULL,
    source_company_id varchar(100) NOT NULL,
    url               varchar(500),
    collected_at      timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT company_source_source_chk CHECK (
        source IN ('saramin', 'jobkorea', 'wanted', 'jumpit')
    ),
    CONSTRAINT company_source_uk UNIQUE (source, source_company_id)
);

CREATE INDEX company_source_company_idx ON market.company_source (company_id);


-- ═══════════════════════════════════════════════════════════════════════════
--  market · 공고
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE market.job_posting (
    id              bigserial     PRIMARY KEY,

    source          varchar(20)   NOT NULL,
    source_job_id   varchar(100)  NOT NULL,

    company_id      bigint        REFERENCES market.company(id) ON DELETE SET NULL,
    field_id        int           REFERENCES market.tech_field(id) ON DELETE SET NULL,

    title           varchar(300)  NOT NULL,
    career_min      int,          -- 신입 = 0, 무관 = NULL
    career_max      int,
    employment_type varchar(30),  -- 정규직 · 계약직 · 인턴 ...
    education       varchar(30),
    location        varchar(120),

    description     text,         -- 요강 전문 (3중 폴백 전부 실패 시 NULL)
    welfare         text,

    -- 연봉: salary_raw 를 파싱해 구조화. 파싱 실패 시 type=unknown + 금액 NULL
    salary_raw      text,
    salary_min      int,          -- 만원 단위
    salary_max      int,
    salary_type     varchar(16)   NOT NULL DEFAULT 'unknown',

    body_is_image   boolean       NOT NULL DEFAULT false,  -- 본문 200자 미만 + 이미지 존재
    posted_at       timestamptz,
    expires_at      timestamptz,

    content_hash    char(64),     -- 본문 변경 감지. 동일하면 재추출 생략
    embed_hash      char(64),     -- content_hash 와 다르면 재임베딩 대상

    collected_at    timestamptz   NOT NULL DEFAULT now(),
    raw_fields      jsonb         NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),

    CONSTRAINT job_posting_source_chk CHECK (
        source IN ('saramin', 'jobkorea', 'wanted', 'jumpit')
    ),
    CONSTRAINT job_posting_salary_type_chk CHECK (
        salary_type IN ('range', 'min_only', 'max_only', 'negotiable', 'unknown')
    ),
    CONSTRAINT job_posting_career_chk CHECK (
        career_min IS NULL OR career_max IS NULL OR career_min <= career_max
    ),
    CONSTRAINT job_posting_salary_chk CHECK (
        salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max
    ),
    CONSTRAINT job_posting_uk UNIQUE (source, source_job_id)
);

CREATE INDEX job_posting_company_idx     ON market.job_posting (company_id);
CREATE INDEX job_posting_field_posted_idx ON market.job_posting (field_id, posted_at DESC);
CREATE INDEX job_posting_posted_idx      ON market.job_posting (posted_at DESC);
CREATE INDEX job_posting_collected_idx   ON market.job_posting (collected_at DESC);
CREATE INDEX job_posting_career_idx      ON market.job_posting (career_min, career_max);
CREATE INDEX job_posting_location_idx    ON market.job_posting (location);

-- 연봉 통계 대상(금액 공개 공고)만 좁게 태우는 부분 인덱스
CREATE INDEX job_posting_salary_idx ON market.job_posting (field_id, salary_min, salary_max)
    WHERE salary_type IN ('range', 'min_only', 'max_only');

-- embed_backfill 대상 스캔용. 이미지 공고·본문 없는 공고는 애초에 제외한다.
CREATE INDEX job_posting_embed_todo_idx ON market.job_posting (id)
    WHERE body_is_image = false
      AND description IS NOT NULL
      AND (embed_hash IS NULL OR embed_hash IS DISTINCT FROM content_hash);

CREATE TRIGGER job_posting_touch BEFORE UPDATE ON market.job_posting
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();


-- ═══════════════════════════════════════════════════════════════════════════
--  market · 스킬
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE market.skill (
    id            serial       PRIMARY KEY,
    name          varchar(80)  NOT NULL UNIQUE,   -- 정규화 표기 ("Spring Boot")
    category      varchar(32),                    -- language · framework · db · infra · tool
    is_ambiguous  boolean      NOT NULL DEFAULT false,  -- Go · C · R — 문맥 단서 없으면 미채택
    embedding     vector(1024),                   -- name + aliases. 앱 시작 시 메모리 로드
    created_at    timestamptz  NOT NULL DEFAULT now(),
    updated_at    timestamptz  NOT NULL DEFAULT now()
);

CREATE TRIGGER skill_touch BEFORE UPDATE ON market.skill
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();


-- 본문 매칭용 표기 변형. alias 는 소문자·공백제거 정규화 후 저장한다.
CREATE TABLE market.skill_alias (
    id        serial       PRIMARY KEY,
    skill_id  int          NOT NULL REFERENCES market.skill(id) ON DELETE CASCADE,
    alias     varchar(120) NOT NULL UNIQUE
);

CREATE INDEX skill_alias_skill_idx ON market.skill_alias (skill_id);


-- 스킬 ↔ 분야 다대다 (React 는 frontend, Kotlin 은 backend + android)
CREATE TABLE market.skill_field (
    skill_id  int NOT NULL REFERENCES market.skill(id)      ON DELETE CASCADE,
    field_id  int NOT NULL REFERENCES market.tech_field(id) ON DELETE CASCADE,
    PRIMARY KEY (skill_id, field_id)
);

CREATE INDEX skill_field_field_idx ON market.skill_field (field_id);


-- 공고 ↔ 스킬. 트렌드·동시출현·갭분석의 기반 테이블.
CREATE TABLE market.posting_skill (
    posting_id   bigint       NOT NULL REFERENCES market.job_posting(id) ON DELETE CASCADE,
    skill_id     int          NOT NULL REFERENCES market.skill(id)       ON DELETE CASCADE,
    requirement  varchar(16)  NOT NULL,   -- required · preferred · tag
    mentions     int          NOT NULL DEFAULT 1,

    PRIMARY KEY (posting_id, skill_id),
    CONSTRAINT posting_skill_requirement_chk CHECK (
        requirement IN ('required', 'preferred', 'tag')
    )
);

-- 스킬 기준 역방향 조회 (get_popular_skills · get_skill_demand)
CREATE INDEX posting_skill_skill_idx ON market.posting_skill (skill_id, requirement);


-- ═══════════════════════════════════════════════════════════════════════════
--  market · 임베딩 청크
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE market.posting_chunk (
    id           bigserial    PRIMARY KEY,
    posting_id   bigint       NOT NULL REFERENCES market.job_posting(id) ON DELETE CASCADE,
    section      varchar(20)  NOT NULL,   -- responsibility · required · preferred
    seq          int          NOT NULL DEFAULT 0,
    content      text         NOT NULL,
    chunk_hash   char(64)     NOT NULL,   -- 변경분만 재임베딩
    embedding    vector(1024),
    token_count  int,
    created_at   timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT posting_chunk_section_chk CHECK (
        section IN ('responsibility', 'required', 'preferred')
    ),
    CONSTRAINT posting_chunk_uk UNIQUE (posting_id, section, seq)
);

-- posting_id 단독 조회는 posting_chunk_uk (posting_id, section, seq) 가 커버한다.
CREATE INDEX posting_chunk_section_idx ON market.posting_chunk (section);


-- ── 벡터 인덱스 (HNSW · 코사인) ────────────────────────────────────────────
-- 데이터가 비어 있을 때 만들어 두면 이후 INSERT 시 점진적으로 채워진다.
-- 수집량이 크게 늘면 (m, ef_construction) 재조정 + REINDEX 를 검토한다.
CREATE INDEX posting_chunk_embedding_idx ON market.posting_chunk
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE INDEX company_profile_embedding_idx ON market.company
    USING hnsw (profile_embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- skill 은 200행 규모라 인덱스 없이 순차 스캔이 더 빠르다. (메모리 로드 후 사용)


-- ═══════════════════════════════════════════════════════════════════════════
--  market · 실행 이력
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE market.crawl_run (
    id           bigserial    PRIMARY KEY,
    kind         varchar(16)  NOT NULL,   -- crawl · embed
    source       varchar(20),
    keyword      varchar(120),
    status       varchar(16)  NOT NULL DEFAULT 'running',

    fetched      int          NOT NULL DEFAULT 0,   -- 요청/수집 건수
    inserted     int          NOT NULL DEFAULT 0,   -- 신규
    updated      int          NOT NULL DEFAULT 0,   -- 갱신
    skipped      int          NOT NULL DEFAULT 0,   -- content_hash 동일
    embedded     int          NOT NULL DEFAULT 0,   -- 임베딩된 청크 수
    errors       int          NOT NULL DEFAULT 0,

    message      text,                              -- 실패 사유 · 마지막 예외
    started_at   timestamptz  NOT NULL DEFAULT now(),
    finished_at  timestamptz,

    CONSTRAINT crawl_run_kind_chk   CHECK (kind IN ('crawl', 'embed')),
    CONSTRAINT crawl_run_status_chk CHECK (status IN ('running', 'success', 'partial', 'failed'))
);

CREATE INDEX crawl_run_kind_started_idx ON market.crawl_run (kind, started_at DESC);
-- get_data_coverage 의 "최종 수집시각" 조회용
CREATE INDEX crawl_run_source_started_idx ON market.crawl_run (source, started_at DESC);


-- ═══════════════════════════════════════════════════════════════════════════
--  chat · 대화
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE chat.chat_session (
    id               uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          varchar(64)  NOT NULL,   -- dev: X-User-Id / jwt: sub 클레임
    title            varchar(200),            -- 첫 질문으로 자동 생성
    message_count    int          NOT NULL DEFAULT 0,
    last_message_at  timestamptz,
    created_at       timestamptz  NOT NULL DEFAULT now(),
    updated_at       timestamptz  NOT NULL DEFAULT now(),
    deleted_at       timestamptz              -- soft delete. 조회 시 IS NULL 조건 필수
);

CREATE INDEX chat_session_user_idx ON chat.chat_session (user_id, last_message_at DESC)
    WHERE deleted_at IS NULL;

CREATE TRIGGER chat_session_touch BEFORE UPDATE ON chat.chat_session
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();


-- 표시용 이력. LLM 컨텍스트는 LangGraph checkpointer 가 따로 관리한다.
CREATE TABLE chat.chat_message (
    id           bigserial    PRIMARY KEY,
    session_id   uuid         NOT NULL REFERENCES chat.chat_session(id) ON DELETE CASCADE,
    seq          int          NOT NULL,
    role         varchar(16)  NOT NULL,
    content      text         NOT NULL DEFAULT '',
    token_count  int,
    created_at   timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT chat_message_role_chk CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    CONSTRAINT chat_message_uk UNIQUE (session_id, seq)
);

-- 세션별 메시지 순서 조회는 chat_message_uk (session_id, seq) 가 커버한다.


-- 툴 호출 로그. chart_payload 로 세션 재진입 시 그래프를 LLM 재호출 없이 복원한다.
CREATE TABLE chat.chat_tool_call (
    id             bigserial    PRIMARY KEY,
    message_id     bigint       NOT NULL REFERENCES chat.chat_message(id) ON DELETE CASCADE,
    seq            int          NOT NULL DEFAULT 0,   -- 한 턴에 여러 툴 호출
    tool_name      varchar(64)  NOT NULL,
    arguments      jsonb        NOT NULL DEFAULT '{}'::jsonb,
    result         jsonb,
    chart_payload  jsonb,       -- 차트화 불가 툴이면 NULL
    latency_ms     int,
    is_error       boolean      NOT NULL DEFAULT false,
    created_at     timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX chat_tool_call_message_idx ON chat.chat_tool_call (message_id, seq);
-- 툴별 성능·실패율 확인용
CREATE INDEX chat_tool_call_tool_idx ON chat.chat_tool_call (tool_name, created_at DESC);


-- LangGraph checkpointer(AsyncPostgresSaver) 테이블은 앱 시작 시 .setup() 이
-- 생성한다. ai_chat 의 search_path 첫 스키마(chat)에 만들어지도록 아래에서
-- CREATE 권한을 부여한다.


-- ═══════════════════════════════════════════════════════════════════════════
--  롤 · 권한
--
--  개발: DATABASE_URL 하나(소유자)로 접속하므로 아래 롤은 쓰지 않아도 된다.
--  운영: 비밀번호를 반드시 교체하고, 크롤러/챗봇 접속을 분리한다.
-- ═══════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_crawler') THEN
        CREATE ROLE ai_crawler LOGIN PASSWORD 'change-me-crawler';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_chat') THEN
        CREATE ROLE ai_chat LOGIN PASSWORD 'change-me-chat';
    END IF;
END
$$;

-- ── ai_crawler : market 전체 쓰기 / chat 접근 없음 ─────────────────────────
GRANT USAGE ON SCHEMA market TO ai_crawler;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA market TO ai_crawler;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA market TO ai_crawler;
ALTER DEFAULT PRIVILEGES IN SCHEMA market
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ai_crawler;
ALTER DEFAULT PRIVILEGES IN SCHEMA market
    GRANT USAGE, SELECT ON SEQUENCES TO ai_crawler;
ALTER ROLE ai_crawler SET search_path = market, public;

-- ── ai_chat : market 읽기 전용 / chat 전체 (R3 을 DB 레벨에서 강제) ────────
GRANT USAGE  ON SCHEMA market TO ai_chat;
GRANT SELECT ON ALL TABLES IN SCHEMA market TO ai_chat;
ALTER DEFAULT PRIVILEGES IN SCHEMA market GRANT SELECT ON TABLES TO ai_chat;

-- CREATE 는 checkpointer 가 자기 테이블을 만들기 위해 필요하다.
GRANT USAGE, CREATE ON SCHEMA chat TO ai_chat;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA chat TO ai_chat;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA chat TO ai_chat;
ALTER DEFAULT PRIVILEGES IN SCHEMA chat
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ai_chat;
ALTER DEFAULT PRIVILEGES IN SCHEMA chat
    GRANT USAGE, SELECT ON SEQUENCES TO ai_chat;
ALTER ROLE ai_chat SET search_path = chat, market, public;
