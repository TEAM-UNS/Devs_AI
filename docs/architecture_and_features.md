# AI 서비스 구조 및 기능 명세서

> 담당 범위: **크롤링·적재**, **임베딩**, **AI 챗봇**
> 미포함: 유저/인증 발급(메인 백엔드), 로드맵 생성(차기), 대시보드 화면 API(메인 백엔드)

---

## 1. 폴더 구조

```
ai-service/
├── docker-compose.yml              postgres(pgvector) + redis
├── init.sql                        스키마 · 권한 · extension
├── alembic/                        마이그레이션
├── pyproject.toml
├── .env.example
│
├── app/
│   ├── main.py                     FastAPI 앱 · 라우터 등록 · lifespan
│   ├── worker.py                   arq WorkerSettings · cron 정의
│   │
│   ├── core/                       ── 도메인 아닌 것 ──
│   │   ├── config.py               환경설정 (pydantic-settings)
│   │   ├── database.py             async engine · sessionmaker · get_session
│   │   ├── redis.py                arq redis pool
│   │   ├── security.py             JWT 디코드 · 검증
│   │   ├── deps.py                 get_current_user
│   │   ├── enums.py                CompanySize · TechField · Requirement · ChunkSection
│   │   └── exceptions.py           도메인 예외 → HTTP 매핑
│   │
│   ├── llm/                        ══ 클린 아키텍처 적용 구간 ══
│   │   ├── port.py                 LLMPort · EmbedderPort (Protocol)
│   │   ├── chat_adapter.py         실제 LLM 어댑터
│   │   ├── embed_adapter.py        임베딩 API 어댑터
│   │   └── fake.py                 테스트용 어댑터 (API 키 불필요)
│   │
│   └── domains/
│       ├── market/                 ══ 데이터의 주인 ══
│       │   ├── models.py           company · company_source · job_posting
│       │   │                       posting_skill · posting_chunk
│       │   │                       skill · skill_alias · skill_field · tech_field
│       │   │                       crawl_run
│       │   ├── repository.py       upsert · 조회 (크롤러 전용, 쓰기)
│       │   ├── queries.py          ★ 읽기 전용 (챗봇 툴 전용)
│       │   ├── similarity.py       유사 기업 계산 (스택+설명+규모)
│       │   ├── schemas.py          DTO
│       │   └── dependencies.py
│       │
│       ├── crawler/                ══ 수집·적재만 ══
│       │   ├── service.py          수집 오케스트레이션 → market.repository
│       │   ├── extractor.py        스택 추출 (섹션 분할 + 별칭 매칭)
│       │   ├── chunker.py          본문 → 섹션 청크 분할
│       │   ├── embed_service.py    청크 임베딩 · 기업 프로필 임베딩
│       │   ├── schemas.py          RawJob
│       │   ├── tasks.py            ★ arq 태스크
│       │   └── sites/
│       │       ├── base.py         딜레이 · 재시도 · 감속 · 스냅샷
│       │       ├── saramin.py
│       │       ├── jobkorea.py
│       │       ├── wanted.py
│       │       └── jumpit.py
│       │
│       └── chat/                   ══ 어시스턴트 ══
│           ├── router.py           /chat/* 엔드포인트
│           ├── schemas.py          요청/응답 Pydantic
│           ├── models.py           chat_session · chat_message · chat_tool_call
│           ├── repository.py
│           ├── service.py          세션 생성 · 히스토리 · 저장
│           ├── dependencies.py
│           ├── stream.py           astream_events → SSE 직렬화
│           ├── guard.py            스코프 검사 · 레이트리밋
│           ├── graph/
│           │   ├── state.py        ChatState
│           │   ├── nodes.py        load_context · guard · agent · persist
│           │   └── build.py        StateGraph 조립 · checkpointer
│           └── tools/
│               ├── registry.py     ALL_TOOLS
│               ├── trend.py        인기 · 급상승 · 세그먼트별 · 연봉 통계
│               ├── skill.py        연관 기술 · 수요 · 스킬명 해소
│               ├── company.py      프로필 · 유사 · 비교 · 검색
│               ├── search.py       공고 벡터 검색
│               ├── personal.py     갭 분석
│               ├── meta.py         데이터 커버리지
│               └── charts.py       툴 결과 → 차트 페이로드
└── tests/
```

### 의존 규칙

| 규칙 | 내용 |
|---|---|
| R1 | `market` 은 아무 도메인도 import 하지 않는다 |
| R2 | `crawler` → `market.repository` (쓰기)만 |
| R3 | `chat` → `market.queries` (읽기)만. `market.models` / `repository` 직접 참조 금지 |
| R4 | `llm/port.py` 는 어댑터를 모른다. 주입만 받는다 |
| R5 | 비즈니스 상수(규모 구간, 스무딩 계수 등)는 `core/enums.py` 에만 |

> R3은 `import-linter` 로 CI에서 강제 권장

---

## 2. 기능 명세서

### 2-1. 수집 (크롤링)

| 기능명 | 상세설명 | 입력 | 예외처리 |
|---|---|---|---|
| 공고 목록 수집 | 사이트별 검색 목록에서 공고 ID·제목·회사·조건을 수집 | site, keyword, page | 셀렉터 미스 시 경고 로그 후 해당 사이트 중단, 다른 사이트는 계속 진행 |
| 공고 상세 수집 | 상세 페이지에서 요강 전문·복지·전형절차·기업정보 수집. JSON-LD → 라벨-값 → CSS 셀렉터 3중 폴백 | posting url | 3중 폴백 전부 실패 시 목록 데이터만으로 저장, `description=null` |
| 기업정보 수집 | 기업 페이지에서 사원수·기업형태·설립일·매출·업종·기업소개·사업내용·인재상 수집 | company url | 기업 페이지 없으면 공고에서 얻은 정보만 사용 |
| 기업 통합 | `name_key`(괄호·주식회사·공백 제거) 기준으로 여러 사이트의 같은 기업을 하나로 병합. 사이트별 식별자는 `company_source` 에 보관 | company name | 동명 다른 기업 오병합 가능 → `company_source` 로 추적 가능하게 유지 |
| 기술스택 추출 | 본문을 자격요건/우대사항/주요업무/복지로 분할 → 복지 제외 → 별칭 사전 매칭 → **`tag`/`required`/`preferred`/`body` 4등급** 부여 | description, tags | 모호 스킬은 문맥 단서 없으면 미채택. `case_sensitive` 별칭은 대소문자 일치 필수 |
| 연봉 파싱 | `salary_raw` 를 `salary_min`/`salary_max`/`salary_type`/`salary_period` 로 구조화. **금액은 연봉(만원)으로 정규화 저장**하고 원본 표기는 `salary_raw` 에 보존 | salary_raw | 파싱 불가·외화 표기는 `salary_type=unknown`, 금액 컬럼 null |
| 이미지 공고 판별 | 본문 텍스트 200자 미만 + 이미지 존재 시 `body_is_image=true` | body html | 집계·임베딩 대상에서 제외 |
| 중복/변경 감지 | `content_hash` 비교. 동일하면 `collected_at` 만 갱신하고 재추출 생략 | source, source_job_id | 해시 충돌 무시 가능 수준 |
| 수집 이력 기록 | 사이트·키워드별 요청수·신규·갱신·스킵·오류를 `crawl_run` 에 기록 | — | 태스크 실패 시 `status=failed` 로 마감 |
| 차단 대응 | 403/429 발생 시 지수 백오프 + delay 1.6배 자동 증가(최대 20초) | — | 재시도 소진 시 해당 페이지 건너뛰고 다음 진행 |
| 미매칭 태그 리포트 | 사이트가 준 `tags_raw` 중 어떤 별칭에도 걸리지 않은 값을 빈도순으로 출력. 사전 보강 대상 발견용 | — | 자동으로 스킬을 생성하지 않는다. 사람이 판단해 시드에 추가 |
| 재추출(reparse) | 사전·분류 규칙 변경 후 재수집 없이 재계산. **대상: 스킬 + `field_id` + 연봉 파싱** | — | 원본(`description`, `tags_raw`, `job_categories`)이 없는 행은 스킵 |

**수집 대상 사이트**

| 사이트 | 방식 | 스택 태그 | 기업정보 |
|---|---|---|---|
| 사람인 | HTML 파싱 | 없음(본문 추출) | 사원수·기업형태·매출 |
| 잡코리아 | HTML 파싱 | 없음(본문 추출) | 사원수·기업형태 |
| 원티드 | 공개 XHR | 있음 | 기업소개·규모 |
| 점핏 | 공개 XHR | 있음 | 기업소개·업종 |

#### 스킬 사전 운영 정책

**사전이 유일한 권위다.** 사이트 태그로 스킬을 자동 생성하지 않는다. `AI/인공지능`, `SW/솔루션` 같은 비스킬 값이 유입되면 집계가 오염된다. 원본은 `tags_raw` 에 보존되므로 사전을 늘린 뒤 `reparse` 하면 복구된다.

| 속성 | 대상 | 목적 |
|---|---|---|
| `skill.is_common` | Git, Jira, Slack, Notion, Confluence | 전 직군 공통 도구. 트렌드 지표에서 기본 제외 |
| `skill.is_ambiguous` | Go, C, R, CAN 등 | 매칭 위치 ±40자 내 문맥 단서 필수 |
| `skill_alias.case_sensitive` | CAN, ES, R, C | 대소문자 일치 필수. 영어 문장의 `can`, `es` 오탐 원천 차단 |

`is_common` 판정 기준은 **"모든 직군이 당연히 쓰는가"** 이며, 애매하면 플래그하지 않는다.
- 플래그 안 함: Figma(프론트/디자인 협업에서 유의미), Linux(DevOps·임베디드 실제 요구사항)
- 과하게 걸면 트렌드에서 진짜 신호가 사라진다

**별칭 금지 목록** — 다른 의미와 충돌하므로 등록하지 않는다.
`node`(k8s 노드) · `compose`(docker compose) · `rest`(영어 단어) · `컨테이너` · `깃` · `비트` · `es`
시드 스크립트의 `--check` 가 별칭 충돌을 사전 검사한다.

**기술 분야 8개** (`etc` 버킷 없음)
`backend` · `frontend` · `mobile` · `data_ai` · `devops` · `security` · `game` · `embedded`

> `etc` 를 두면 "매핑 실패"와 "진짜 기타 직무"가 섞여 분류 정확도를 측정할 수 없다. 매핑 실패는 `NULL` 로 남겨 개선 대상으로 추적한다.

### 2-2. 임베딩

| 기능명 | 상세설명 | 입력 | 예외처리 |
|---|---|---|---|
| 청크 분할 | 공고 본문을 `responsibility`/`required`/`preferred` 3섹션으로 분할. 복지·전형절차는 제외 | posting.description | 섹션 헤더 없으면 전체를 `responsibility` 단일 청크로 |
| 공고 임베딩 | `embed_hash ≠ content_hash` 인 공고만 대상. 청크별 `chunk_hash` 비교로 변경분만 재임베딩 | posting_ids[] | API 실패 시 재시도 3회, 최종 실패 시 `embed_hash` 미갱신 → 다음 백필에서 재처리 |
| 기업 프로필 임베딩 | `description + business_content + industry` 를 합쳐 임베딩 | company_ids[] | 설명이 전부 비어 있으면 스킵 |
| 스킬 임베딩 | `skill.name + aliases` 를 임베딩. 200행 규모라 앱 시작 시 메모리 로드 | — | — |
| 백필 | 누락·실패분을 매일 청소 (최대 500건/회) | — | `body_is_image=true`, `description IS NULL` 제외 |

**배치 정책**: 청크 96개 단위로 임베딩 API 1회 호출. 태스크 1개당 API 1~2회.

### 2-3. 챗봇

| 기능명 | 상세설명 | 입력 | 예외처리 |
|---|---|---|---|
| 커리어 질의응답 | 수집·집계된 공고 데이터와 기술 연관 관계를 근거로 답변. 툴 호출 결과만 근거로 사용 | 텍스트 | 툴 전부 실패 시 "데이터를 조회하지 못했다"고 명시 |
| 스트리밍 응답 | SSE로 토큰 단위 스트리밍. 툴 시작·차트·완료 이벤트 분리 전송 | — | 연결 끊김 감지 시 그래프 실행 취소 |
| 인라인 그래프 | 툴이 반환한 원본 수치를 `graph` 이벤트로 별도 전송. 프론트가 렌더링 | — | 차트화 불가한 툴 결과는 텍스트만 |
| 세션 관리 | 세션 생성·목록·상세·삭제. 첫 질문으로 제목 자동 생성 | — | 타 유저 세션 접근 시 404 |
| 대화 이력 | LangGraph checkpointer가 `thread_id=session_id` 로 컨텍스트 유지. 표시용 이력은 `chat_message` | — | 토큰 예산 초과 시 오래된 턴부터 제거 |
| 차트 복원 | `chat_tool_call.chart_payload` 저장. 세션 재진입 시 LLM 재호출 없이 그래프 복원 | — | — |
| 스코프 가드 | 채용·기술스택·기업 분석 범위 밖 질문은 툴 호출 없이 거부 응답 | — | 판정 애매 시 통과시키고 LLM이 판단 |
| 레이트리밋 | 유저별 분당 요청수 · 일일 토큰 한도 | user_id | 초과 시 429 |
| 툴 루프 제한 | `recursion_limit=8` (툴 3~4회까지 허용) | — | 초과 시 현재까지 내용으로 마감 |
| 추천 질문 | 정적 목록 + 프로필 기반 동적 생성 | profile | 프로필 없으면 정적만 |

### 2-4. 챗봇 툴 명세 (13개)

#### `requirement` 등급 사용 규칙

`requirement` 는 4값이다: `tag`(사이트 제공 태그) > `required`(자격요건) > `preferred`(우대사항) > `body`(주요업무·도입부 등 단순 언급).

| 툴 | 사용 등급 | 이유 |
|---|---|---|
| `get_skill_gap` | `required` + `tag` | 실제로 요구되는 것만. 학습 우선순위 판단 |
| `get_popular_skills` | `required` + `preferred` + `tag` | "요구 기술" 의미. `body` 제외 |
| `get_rising_skills` | `required` + `preferred` + `tag` | 위와 동일 |
| `get_related_skills` | 파라미터로 선택 | `requirement='preferred'` 로 우대사항만 조회 가능 |
| `get_company_profile` | **전부** (`body` 포함) | "이 회사가 쓰는 스택"이라 단순 언급도 유효한 신호 |
| `compare_companies` | `required` + `tag` | 기업 간 요구사항 비교 |

> `body` 를 버리지 않는 이유: "우리는 AWS 위에서 운영합니다" 는 요구사항은 아니지만 그 회사의 기술 스택 정보로는 유효하다.

**`is_common` 처리**: `get_popular_skills` / `get_rising_skills` / `get_stacks_by_segment` 는 기본적으로 `is_common=true` 스킬을 제외한다. `include_common=true` 파라미터로 켤 수 있다. `get_company_profile` 은 협업툴 정보가 유용하므로 포함한다.

#### 트렌드 계열

| 툴 | 파라미터 | 반환 | 근거 | 차트 |
|---|---|---|---|---|
| `get_popular_skills` | `field?`, `size_type?`, `career_level?`, `days=30`, `top=20` | 스킬명·공고수·점유율·순위 | `posting_skill` ⋈ `job_posting` | bar |
| `get_rising_skills` | `field?`, `min_count=5`, `top=10` | 스킬명·이번주·지난주·증감률 | 2주 구간 비교 | bar |
| `get_stacks_by_segment` | `group_by`(size\|career\|location), `field?`, `top=10` | 세그먼트별 상위 스킬 | `company.size_type` 등 | grouped bar |
| `get_salary_stats` | `field?`, `career_level?`, `size_type?`, `company_id?`, `skill?` | 중앙값·1/3분위·최소·최대 + **공개율** | `salary_min/max` where `salary_type IN (range, min_only)` | box \| bar |

> 증감률은 `(this+1)/(last+1)-1` 스무딩. `min_count` 미만 제외.

**`get_salary_stats` 반환 형식** — 공개율을 항상 함께 반환해 LLM이 자동으로 단서를 달게 한다.

```json
{
  "median": 4200, "q1": 3600, "q3": 5000, "min": 2800, "max": 7000,
  "unit": "만원",
  "sample_size": 58,
  "total_postings": 340,
  "disclosure_rate": 0.17,
  "breakdown": { "negotiable": 261, "range": 44, "min_only": 14, "unknown": 21 }
}
```

`sample_size < 10` 이면 `"low_confidence": true` 를 함께 반환한다. LLM은 이 경우 수치를 단정하지 않고 표본이 적음을 명시한다.

**연봉 정규화 규칙** — 월급·시급이 섞이면 중앙값이 무의미해진다. 저장 시 연봉(만원) 기준으로 통일한다.

| 원문 표기 | salary_min | salary_max | type | period |
|---|---|---|---|---|
| `3,000~4,000만원` | 3000 | 4000 | range | annual |
| `3000~4000` (단위 없음) | 3000 | 4000 | range | annual |
| `2,600만원 이상` | 2600 | null | min_only | annual |
| `월 300만원` | 3600 | 3600 | range | monthly |
| `시급 12,000원` | null | null | unknown | hourly |
| `회사내규에 따름` / `면접 후 결정` / `협의 후 결정` | null | null | negotiable | null |
| `$80,000` (외화) | null | null | unknown | null |

- `period` 는 원본 기준을 기록하되 `salary_min/max` 는 **항상 연봉 만원 단위**로 저장한다 (월급은 ×12)
- `hourly` 는 통계에서 제외한다 (근무시간 미상으로 연봉 환산 불가)
- 통계 쿼리는 `salary_type IN ('range','min_only')` 만 사용한다

#### 기술 관계 계열

| 툴 | 파라미터 | 반환 | 근거 |
|---|---|---|---|
| `get_related_skills` | `skill`, `field?`, `requirement?`, `top=10` | 동반 스킬·동시출현수·NPMI | 동시출현 집계 |
| `get_skill_demand` | `skill` | 분야별·규모별·경력별 수요 분포 | `posting_skill` 분해 |
| `resolve_skill` | `query` | 후보 스킬 3~5개 + 유사도 | `skill.embedding` (메모리) |

#### 기업 계열

| 툴 | 파라미터 | 반환 | 특이사항 |
|---|---|---|---|
| `get_company_profile` | `name` | 기본정보·기업소개·인재상·요구스택 top10·공고수·경력분포 | 동명 다수 시 `status=ambiguous` + 후보 목록 반환 |
| `find_similar_companies` | `company_id`, `top=5` | 유사기업·점수·공통스킬 | `0.5×스택코사인 + 0.35×설명코사인 + 0.15×규모근접` |
| `compare_companies` | `company_ids[]`(2~5) | 기업별 상위스택·공통스택·공고수·규모 | 2개 미만이면 오류 반환 |
| `search_companies` | `query`, `size_type?`, `field?`, `top=10` | 기업 목록 + 매칭 근거 문장 | pgvector + 메타 필터 한 쿼리 |

#### 탐색·개인화·메타

| 툴 | 파라미터 | 반환 | 특이사항 |
|---|---|---|---|
| `search_postings` | `query`, `field?`, `career_max?`, `section?`, `top=10` | 공고 제목·회사·매칭 청크·URL | 순위/비율 질문에는 사용 금지(툴 설명에 명시) |
| `get_skill_gap` | `my_skills[]`, `field?`, `company_ids?`, `top=15` | 부족 스킬 + 수요 순위 | `requirement in (required, tag)` 만 |
| `get_data_coverage` | — | 수집 기간·총 공고수·분야별 분포·최종 수집시각 | 시계열·신뢰도 질문 전 선행 호출 권장 |

### 2-5. 조건부 답변 항목 (답하되 단서 필수)

| 항목 | 한계 | 처리 |
|---|---|---|
| 연봉·급여 | 국내 공고 상당수가 "회사내규에 따름"이라 표본이 편향됨. 금액을 공개하는 곳은 상대적으로 채용에 적극적이거나 규모가 큰 기업일 가능성 | `get_salary_stats` 가 `disclosure_rate` 와 `sample_size` 를 항상 반환. 시스템 프롬프트에서 **금액을 말할 때 공개율과 표본 수를 함께 말하도록** 강제 |

**연봉 답변 예시**

> "백엔드 신입 공고 340건 중 실제 금액이 적힌 건 58건(17%)이었고, 그 기준으로는 중앙값이 3,600만원, 사분위 범위가 3,200~4,000만원입니다. 나머지 83%는 '회사내규에 따름'이라 실제 시장 전체와는 차이가 있을 수 있어요."

시스템 프롬프트 규칙:
- 금액을 언급할 때 `sample_size` 와 `disclosure_rate` 를 반드시 함께 서술
- `low_confidence=true` 면 "표본이 적어 참고용"임을 명시
- "평균 연봉은 X입니다" 같은 단정 표현 금지. "공개된 공고 기준으로는" 을 앞에 붙일 것

### 2-6. 답변 불가 항목 (의도적 제외)

| 항목 | 사유 | 처리 |
|---|---|---|
| 1년 이상 시계열 비교 | 수집 시작 시점부터의 데이터만 존재 | `get_data_coverage` 로 범위 고지 후 한계 인정 |
| 기업 분위기·평판 | 리뷰 데이터 없음 | 인재상·기업소개로 우회, "공고 기준"임을 명시 |
| 합격 확률 | 지원 결과 데이터 없음 | 매칭률로 대체. "확률"이라는 표현 금지 |
| 공고 실시간 유효성 | 수집 시점 기준 | 수집 시각을 함께 반환 |

---

## 3. 배치 스케줄 (arq)

| 시각 | 태스크 | 내용 | 재시도 |
|---|---|---|---|
| 04:00 | `crawl_dispatch` | 사이트×키워드 조합으로 `crawl_site` 팬아웃 | 1 |
| — | `crawl_site` | 수집 → upsert → 변경분 `embed_postings` enqueue | 3 |
| — | `embed_postings` | 청크 분할 → 배치 임베딩 → upsert | 3 |
| 05:30 | `embed_backfill` | 누락·실패분 최대 500건 재처리 | 2 |
| 06:00 | `embed_companies` | 기업 설명 변경분 임베딩 | 2 |

**중복 방지**: `_job_id = f"crawl:{site}:{keyword}:{date}"` 형식으로 동일 작업 중복 큐잉 차단
**동시성**: `max_jobs=4` (임베딩 API 호출 제한 고려)
**타임아웃**: `job_timeout=600`

---

## 4. 스키마 변경 이력

프롬프트 2(스택 추출기) 구현 중 발견된 문제로 아래를 변경했다.
**프롬프트 3 이후 작업은 이 변경을 전제로 한다.**

| 변경 | 내용 | 배경 |
|---|---|---|
| `posting_skill.requirement` | 3값 → **4값** (`body` 추가) | 명세 오류. 등급은 4단계인데 컬럼이 3값이라 `body` 가 `preferred` 로 뭉개짐 |
| `skill.is_common` | boolean 신규 | Git 등 전 직군 공통 도구를 트렌드 지표에서 제외 |
| `skill_alias.case_sensitive` | boolean 신규 | 영어 문장의 `can`, `es` 오탐 차단 |
| `job_posting.salary_period` | varchar(8) 신규 | 월급·시급 혼입으로 중앙값 왜곡 방지 |
| `tech_field` | `etc` 제거, 8개로 확정 | 매핑 실패와 기타 직무가 섞여 정확도 측정 불가 |

### 마이그레이션 순서 (필수)

```
① requirement 제약 변경 + 신규 컬럼 추가
② 전체 reparse          ← 부분 실행 금지
③ 검증
```

**②를 건너뛰면 안 된다.** 기존 행은 `body → preferred` 로 이미 뭉개져 있어 되돌릴 수 없고, `description` 에서 재추출해야만 복원된다. 마이그레이션만 하고 reparse를 생략하면 과거 데이터가 계속 오염된 상태로 남는다.

### 검증 쿼리

```sql
-- ① requirement 4값이 실제로 분포하는지
SELECT requirement, COUNT(*) FROM market.posting_skill GROUP BY 1;
--    body 가 0이면 reparse가 안 돌았다는 뜻

-- ② is_common 이 트렌드에서 빠지는지
SELECT s.name, s.is_common, COUNT(*) c
FROM market.posting_skill ps JOIN market.skill s ON s.id = ps.skill_id
GROUP BY 1,2 ORDER BY c DESC LIMIT 15;

-- ③ 연봉 정규화 상태
SELECT salary_type, salary_period, COUNT(*),
       MIN(salary_min), MAX(salary_max)
FROM market.job_posting GROUP BY 1,2 ORDER BY 3 DESC;
--    salary_min 이 300 같은 월급 단위로 남아 있으면 정규화 실패

-- ④ 분야 매핑 상태
SELECT COALESCE(f.code,'(NULL)'), COUNT(*)
FROM market.job_posting p LEFT JOIN market.tech_field f ON f.id = p.field_id
GROUP BY 1 ORDER BY 2 DESC;

-- ⑤ 미매칭 태그 (사전 보강 대상)
SELECT tag, COUNT(*) cnt FROM (
  SELECT unnest(string_to_array(tags_raw, ',')) AS tag
  FROM market.job_posting WHERE tags_raw IS NOT NULL) t
WHERE NOT EXISTS (SELECT 1 FROM market.skill_alias a
                  WHERE lower(trim(t.tag)) = lower(a.alias))
GROUP BY 1 ORDER BY 2 DESC LIMIT 50;
```

### 추출 품질 측정 (권장)

미매칭 태그 리포트를 주기적으로 돌려 사전을 보강하고, 그 효과를 수치로 남긴다.

```
사전 v1 → 미매칭 상위 50개 검토 → 별칭 N개 추가 → reparse → 재현율 재측정
```

점핏·원티드가 주는 태그를 정답으로 삼으면 재현율을 자동 계산할 수 있다. 회고 자료로 활용 가능.

---

## 5. 환경 전환

| 항목 | 개발 | 운영 전환 시 |
|---|---|---|
| 인증 | `AUTH_MODE=dev`, `X-User-Id` 헤더 | `AUTH_MODE=jwt`, RS256 공개키 검증 |
| 유저 식별 | 헤더 값 그대로 | 토큰 `sub` 클레임 |
| CORS | `localhost:*` | 실제 도메인 |
| DB 권한 | 단일 유저 | `market` SELECT 전용 / `chat` 전체 |

메인 백엔드와 합의 필요 항목: JWT 알고리즘, `sub` 클레임 타입, 공개키 전달 방법.