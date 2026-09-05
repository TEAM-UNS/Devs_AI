# 프로젝트 규칙

IT 채용공고를 수집·분석해 AI 챗봇으로 답변하는 서비스의 데이터/AI 파트.
담당 범위: 크롤링·적재, 임베딩, 챗봇. 유저/인증 발급과 화면은 다른 팀.

## 기술 스택

- Python 3.12, 전부 async
- FastAPI / SQLAlchemy 2.0 async / Alembic
- **드라이버는 psycopg3 로 통일** (asyncpg 쓰지 말 것 — LangGraph checkpointer와 통일)
- Postgres 16 + pgvector, Redis
- arq (배치), LangGraph (챗봇)
- 패키지 관리: uv

## 아키텍처 규칙

- app/domains/{market,crawler,chat} 3개 도메인
- market 은 데이터의 주인. 아무 도메인도 import 하지 않는다
- crawler → market.repository (쓰기)만
- chat → market.queries (읽기)만. market.models / repository 직접 참조 금지
- 외부 API(LLM, 임베딩)만 포트/어댑터로 분리. 리포지토리는 인터페이스 만들지 않는다
- Enum · 비즈니스 상수는 그 값을 소유한 도메인에 둔다
  (market/enums.py, chat/enums.py). core 에는 인프라(config·database·redis)와
  예외 기반 클래스만 둔다
- 예외는 core/exceptions.py 에 AppError + 핸들러만. 구체 예외는 각 도메인이 소유한다

## 코딩 규칙

- 동기 DB 호출 금지. 이벤트 루프가 멈춘다
- 트랜잭션 커밋은 service 계층에서. repository 는 flush 까지만
- 타입 힌트 필수, mypy strict 지향
- 명세서에 없는 DB 컬럼을 임의로 추가하지 말 것. 필요하면 먼저 물어볼 것

## 스킬 사전 규칙

- 사전이 유일한 권위다. 사이트 태그로 스킬을 자동 생성하지 말 것
- 미매칭 태그는 리포트로만 출력하고, 사람이 판단해 시드에 추가한다
- 별칭 금지: node, compose, rest, 컨테이너, 깃, 비트, es (다른 의미와 충돌)
- is_common(전 직군 공통 도구)은 5개만: Git, Jira, Slack, Notion, Confluence

## 크롤링 규칙

- 셀렉터·엔드포인트를 기억에 의존해 추측하지 말 것. 실제 응답을 받아 확인한 뒤 작성
- 모든 응답 원본을 data/raw/{site}/ 에 저장
- 요청 간 딜레이 준수. 403이 반복되면 감속하고, 무리하게 우회하지 말 것
- 사이트는 한 개씩 완성. 여러 개 동시 작업 금지

## 완료 기준

"코드를 작성했다"는 완료가 아니다. 직접 실행해서 DB에 데이터가 들어간 것을
SQL로 확인하고 결과를 보여줘야 완료다.