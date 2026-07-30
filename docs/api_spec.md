# API 명세서

**Base URL**: `http://localhost:8000` (개발) / `https://{host}` (운영)
**Prefix**: `/api/v1`
**Content-Type**: `application/json; charset=utf-8`

---

## 0. 공통

### 인증

| 모드 | 설정 | 전달 방식 |
|---|---|---|
| 개발 | `AUTH_MODE=dev` | `X-User-Id: dev-user` |
| 운영 | `AUTH_MODE=jwt` | `Authorization: Bearer {token}` |

운영 모드에서는 RS256 서명·만료를 검증하고 `sub` 클레임을 `user_id` 로 사용한다.
유저 정보는 저장하지 않으며, `user_id` 문자열만 세션 소유자 식별에 쓴다.

### 공통 에러 응답

```json
{
  "error": {
    "code": "SESSION_NOT_FOUND",
    "message": "세션을 찾을 수 없습니다.",
    "detail": null
  }
}
```

| HTTP | code | 설명 |
|---|---|---|
| 400 | `INVALID_REQUEST` | 요청 형식 오류 |
| 401 | `UNAUTHORIZED` | 토큰 없음·만료·서명 불일치 |
| 404 | `SESSION_NOT_FOUND` | 세션 없음 또는 타 유저 소유 |
| 422 | `VALIDATION_ERROR` | Pydantic 검증 실패 |
| 429 | `RATE_LIMITED` | 분당 요청수 또는 일일 토큰 한도 초과 |
| 500 | `INTERNAL_ERROR` | 처리되지 않은 서버 오류 |
| 503 | `LLM_UNAVAILABLE` | LLM API 장애 |

> 타 유저 세션 접근은 403이 아닌 **404** 로 응답한다. 세션 존재 여부 자체를 노출하지 않기 위함.

### 레이트리밋 헤더

```
X-RateLimit-Limit: 20
X-RateLimit-Remaining: 17
X-RateLimit-Reset: 1730000000
```

---

## 1. 챗봇 스트리밍

### `POST /api/v1/chat/stream`

질문을 보내고 SSE로 답변을 스트리밍 받는다.

> **프론트 주의**: 브라우저 `EventSource` 는 커스텀 헤더를 보낼 수 없다.
> `fetch` + `ReadableStream` 으로 구현할 것. `AbortController` 로 중단 시 서버가 감지해 LLM 호출을 취소한다.

**Request**

```json
{
  "session_id": "018f2a...",
  "message": "React 쓰는 회사는 뭘 같이 요구해?",
  "profile": {
    "fields": ["frontend"],
    "skills": ["React", "TypeScript"],
    "career_years": 0,
    "target_job": "프론트엔드 개발자",
    "target_companies": ["당근마켓"]
  }
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `session_id` | string(uuid) | N | 없으면 새 세션 자동 생성 |
| `message` | string(1~2000) | Y | 질문 |
| `profile` | object | N | 개인화 컨텍스트. 없으면 일반 답변 |

**Response**: `200 OK`, `Content-Type: text/event-stream`

```
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

> `X-Accel-Buffering: no` 는 리버스 프록시(nginx) 버퍼링 방지용. 없으면 스트리밍이 한 번에 몰려서 나온다.

---

### SSE 이벤트 계약

#### `session`
세션이 새로 생성되었거나 확정되었을 때 최초 1회.
```
event: session
data: {"session_id":"018f2a...","title":"React 관련 요구 기술","is_new":true}
```

#### `tool_start`
툴 호출 시작. UI에 "분석 중..." 표시용.
```
event: tool_start
data: {"tool":"get_related_skills","label":"기술 연관 관계를 분석하고 있어요"}
```

#### `graph`
차트 렌더링용 페이로드. **LLM이 생성한 텍스트가 아니라 툴이 반환한 원본 수치.**
```
event: graph
data: {
  "id":"g_01",
  "type":"bar",
  "title":"React와 함께 요구되는 기술",
  "unit":"공고 수",
  "data":[
    {"label":"TypeScript","value":412,"ratio":0.78},
    {"label":"Next.js","value":301,"ratio":0.57}
  ],
  "source":{"tool":"get_related_skills","posting_count":528,"period":"2026-07-01~2026-07-29"}
}
```

`type`: `bar` | `grouped_bar` | `line` | `network` | `table`

#### `token`
답변 텍스트 조각.
```
event: token
data: {"text":"React를 요구하는 공고에서는 "}
```

#### `done`
정상 종료.
```
event: done
data: {
  "message_id": 1042,
  "tools_used":["get_related_skills"],
  "graph_ids":["g_01"],
  "usage":{"input_tokens":2140,"output_tokens":318}
}
```

#### `error`
스트림 도중 오류. 이 이벤트 후 스트림이 종료된다.
```
event: error
data: {"code":"TOOL_TIMEOUT","message":"데이터 조회에 실패했습니다.","recoverable":true}
```

| code | 설명 | 후속 처리 |
|---|---|---|
| `TOOL_TIMEOUT` | 툴 3초 초과 | LLM이 조회 실패를 언급하며 답변 계속 |
| `CONTEXT_OVERFLOW` | 토큰 예산 초과 | 오래된 턴 제거 후 재시도 안내 |
| `OUT_OF_SCOPE` | 범위 밖 질문 | 거부 메시지 전송 후 종료 |
| `RATE_LIMITED` | 한도 초과 | 재시도 시각 안내 |
| `LLM_UNAVAILABLE` | LLM API 장애 | 잠시 후 재시도 안내 |

> `TOOL_TIMEOUT` 은 스트림을 끊지 않는다. 툴 실패를 LLM에 그대로 전달해 "데이터를 못 가져왔다"고 답하게 한다.

**이벤트 순서 예시**
```
session → tool_start → graph → token × N → done
```

---

## 2. 세션 관리

### `GET /api/v1/chat/sessions`

```
?limit=20&cursor={last_message_at}&archived=false
```

**Response** `200`
```json
{
  "sessions": [
    {
      "id": "018f2a...",
      "title": "React 관련 요구 기술",
      "message_count": 6,
      "last_message_at": "2026-07-29T10:12:03Z",
      "created_at": "2026-07-29T09:58:11Z"
    }
  ],
  "next_cursor": "2026-07-28T22:03:00Z",
  "has_more": true
}
```

### `POST /api/v1/chat/sessions`

빈 세션 생성. `/chat/stream` 에서 자동 생성되므로 선택적.

**Request**
```json
{ "title": "새 대화" }
```

**Response** `201`
```json
{ "id":"018f2b...", "title":"새 대화", "created_at":"2026-07-29T10:20:00Z" }
```

### `GET /api/v1/chat/sessions/{session_id}`

메시지 전체와 저장된 차트를 함께 반환. **재진입 시 그래프 복원용.**

**Response** `200`
```json
{
  "id": "018f2a...",
  "title": "React 관련 요구 기술",
  "messages": [
    { "id":1041, "seq":1, "role":"user",
      "content":"React 쓰는 회사는 뭘 같이 요구해?",
      "created_at":"2026-07-29T10:11:40Z" },
    { "id":1042, "seq":2, "role":"assistant",
      "content":"React를 요구하는 공고에서는...",
      "created_at":"2026-07-29T10:11:52Z",
      "tool_calls":[
        { "tool":"get_related_skills",
          "arguments":{"skill":"React","field":"frontend"},
          "chart_payload":{ "id":"g_01","type":"bar","title":"...","data":[] },
          "latency_ms":142, "is_error":false }
      ]
    }
  ]
}
```

### `PATCH /api/v1/chat/sessions/{session_id}`

```json
{ "title": "프론트엔드 스택 조사" }
```
**Response** `200` — 갱신된 세션 객체

### `DELETE /api/v1/chat/sessions/{session_id}`

세션·메시지·툴콜·LangGraph 체크포인트를 함께 삭제.
**Response** `204 No Content`

---

## 3. 추천 질문

### `GET /api/v1/chat/suggestions`

```
?field=backend
```

**Response** `200`
```json
{
  "suggestions": [
    { "text":"요즘 백엔드에서 제일 많이 요구하는 기술은?", "category":"trend" },
    { "text":"React 쓰는 회사들은 뭘 같이 요구해?", "category":"relation" },
    { "text":"스타트업과 대기업의 스택 차이는?", "category":"segment" }
  ]
}
```

프로필이 있으면(`profile` 쿼리 또는 토큰 기반) 개인화 항목이 앞에 추가된다.

---

## 4. 메타

### `GET /api/v1/meta/coverage`

챗봇의 `get_data_coverage` 툴과 같은 데이터. 화면 상단 고지 문구용.

**Response** `200`
```json
{
  "collected_from": "2026-07-01",
  "collected_to": "2026-07-29",
  "total_postings": 12480,
  "active_postings": 8210,
  "image_only_ratio": 0.17,
  "salary_disclosure_rate": 0.19,
  "requirement_breakdown": { "tag": 1840, "required": 5120, "preferred": 3910, "body": 6220 },
  "by_field": { "backend":4210, "frontend":3180, "data_ai":1620 },
  "company_count": 2840,
  "last_crawl_at": "2026-07-29T04:38:12Z"
}
```

### `GET /health`

```json
{ "status":"ok", "db":"ok", "redis":"ok", "llm":"ok" }
```

의존성 중 하나라도 실패하면 `503` 과 함께 실패 항목을 표시한다.

---

## 5. 운영용 (내부)

`X-Internal-Key` 헤더 필요. 개발 환경에서만 노출.

### `POST /internal/crawl/trigger`

```json
{ "site":"jumpit", "keyword":null, "pages":3, "with_detail":true }
```
**Response** `202`
```json
{ "job_id":"crawl:jumpit:all:2026-07-29", "status":"queued" }
```

### `GET /internal/crawl/runs`

```
?source=saramin&limit=20
```
**Response** `200`
```json
{
  "runs":[
    { "id":881, "kind":"crawl", "source":"saramin", "keyword":"백엔드",
      "status":"ok", "fetched":412, "inserted":180, "updated":24,
      "skipped":208, "errors":0, "embedded":0,
      "started_at":"2026-07-29T04:00:02Z", "finished_at":"2026-07-29T04:31:40Z" }
  ]
}
```

### `GET /internal/skills/unmatched`

사이트 태그 중 사전에 없는 값을 빈도순으로 반환. **사전 보강 대상 발견용.**
자동으로 스킬을 생성하지 않는다 — 사람이 판단해 시드에 추가한다.

```
?limit=50&source=jumpit
```
**Response** `200`
```json
{
  "unmatched": [
    { "tag": "Figma", "count": 24, "sources": ["jumpit","wanted"] },
    { "tag": "AI/인공지능", "count": 18, "sources": ["jumpit"] }
  ],
  "total_tags": 4820,
  "matched_ratio": 0.83
}
```

`matched_ratio` 가 사전 재현율의 근사치다. 별칭을 보강하고 `reparse` 한 뒤 이 값이 올라가는지로 효과를 측정한다.

### `POST /internal/reparse`

사전·분류 규칙 변경 후 재수집 없이 재계산. **대상: 스킬 + `field_id` + 연봉 파싱.**

```json
{ "scope": "all", "source": null }
```
**Response** `202`
```json
{ "job_id":"reparse:2026-07-30", "target_count": 12480 }
```

> `requirement` 를 4값으로 확장하는 마이그레이션 직후에는 **반드시 `scope=all` 로 1회 실행**해야 한다. 기존 행의 `body` 등급이 `preferred` 로 뭉개져 있어 재추출 없이는 복원되지 않는다.

### `POST /internal/embed/backfill`

```json
{ "limit": 500 }
```
**Response** `202` — `{ "job_id":"embed:backfill:2026-07-29", "queued": 342 }`

---

## 6. 프론트 연동 참고

### 스트리밍 수신 예시

```js
const ctrl = new AbortController();

const res = await fetch("/api/v1/chat/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json",
             "Authorization": `Bearer ${token}` },
  body: JSON.stringify({ session_id, message }),
  signal: ctrl.signal,
});

const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
// SSE 프레임 파싱: "event: {name}\ndata: {json}\n\n"
```

중단은 `ctrl.abort()`. 서버가 disconnect를 감지해 LLM 호출을 취소하므로 토큰이 낭비되지 않는다.

### 이벤트 처리 권장

| 이벤트 | UI 동작 |
|---|---|
| `session` | 새 세션이면 사이드바 목록에 추가 |
| `tool_start` | `label` 을 로딩 인디케이터에 표시 |
| `graph` | `id` 로 자리를 잡아두고 차트 렌더링 |
| `token` | 말풍선에 append |
| `done` | 로딩 해제, `message_id` 보관 |
| `error` | `recoverable` 이면 인라인 경고, 아니면 종료 처리 |

---

## 7. 미확정 항목

| 항목 | 현재 | 확정 필요 시점 |
|---|---|---|
| JWT 알고리즘 | RS256 가정 | 메인 백엔드 인증 구현 시 |
| `sub` 클레임 타입 | 문자열로 수용 | 동일 |
| 공개키 전달 | 파일 경로 설정 | 동일 |
| 로드맵 API | 미설계 | 챗봇 완료 후 |