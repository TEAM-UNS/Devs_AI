-- ═══════════════════════════════════════════════════════════════════════════
--  jobstack ai-service · 부트스트랩
--
--  실행: docker-entrypoint-initdb.d 에 마운트되어 최초 1회 자동 실행
--        (POSTGRES_USER 권한 = 스키마 소유자)
--
--  여기서는 "테이블이 들어갈 자리"만 만든다.
--    - extension  vector · pg_trgm · pgcrypto
--    - schema     market(수집 데이터) · chat(대화 데이터)
--    - role       ai_crawler(market RW) / ai_chat(market RO + chat RW)
--
--  ★ 테이블 · 인덱스 · 트리거는 alembic 이 만든다.
--    docker compose up -d 후 `alembic upgrade head` 를 실행할 것.
--    스키마의 단일 진실은 app/domains/*/models.py 다.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS chat;


-- ═══════════════════════════════════════════════════════════════════════════
--  롤 · 권한
--
--  개발: DATABASE_URL 하나(소유자)로 접속하므로 아래 롤은 쓰지 않아도 된다.
--  운영: 비밀번호를 반드시 교체하고 크롤러/챗봇 접속을 분리한다.
--
--  ALTER DEFAULT PRIVILEGES 를 먼저 걸어두면, 나중에 alembic 이 만드는
--  테이블에도 권한이 자동으로 붙는다.
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

-- CREATE 는 LangGraph checkpointer 가 자기 테이블을 만들기 위해 필요하다.
GRANT USAGE, CREATE ON SCHEMA chat TO ai_chat;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA chat TO ai_chat;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA chat TO ai_chat;
ALTER DEFAULT PRIVILEGES IN SCHEMA chat
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ai_chat;
ALTER DEFAULT PRIVILEGES IN SCHEMA chat
    GRANT USAGE, SELECT ON SEQUENCES TO ai_chat;
ALTER ROLE ai_chat SET search_path = chat, market, public;
