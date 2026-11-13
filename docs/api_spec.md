# API 명세서

**Base URL**: `http://localhost:8000` (개발) / `http://ai-api:8000` (운영, `devs-be_backend` 도커 네트워크 내부)
**Content-Type**: `application/json; charset=utf-8`

## 구현 현황

| 엔드포인트 | 상태 |
|---|---|
| `POST /api/chat/stream` | **구현됨** |
| `GET /health` | **구현됨** |
| 세션 목록·상세·수정·삭제 | **만들지 않는다** — 백엔드가 DB 를 직접 읽는다 |
| 추천 질문 · 메타 · 운영용 | 미구현 (아래 7절) |

---

## 0. 공통

### 호출 주체와 인증

AI 서버는 **백엔드만 호출한다.** 프론트는 이 서버를 직접 부르지 않는다.

- AI 서버는 외부에 열려 있지 않다. 백엔드와 같은 도커 네트워크(`devs-be_backend`)에서 `http://ai-api:8000` 으로만 닿는다
- 호스트의 8000 은 `127.0.0.1` 에만 바인딩돼 있다. 서버 안에서 디버깅할 때만 쓴다
- **인증은 백엔드가 맡는다.** 백엔드가 토큰을 검증하고 그 결과로 `user_id` 를 채워 보낸다
- AI 서버는 요청 본문의 `user_id` 를 그대로 신뢰한다. 별도 토큰·헤더 검증은 없다

```json
{ "user_id": 1, "message": "..." }
```

남의 `session_id` 로는 접근할 수 없다(404).

`user_id` 는 `public.tbl_user(user_id)` 를 가리키는 FK 다. `tbl_user` 에 없는 값이면
세션을 만들기 전에 **404 `USER_NOT_FOUND`** 로 끊는다. `session_id` 를 함께 보내도 유저 검사가 먼저다.

### 개인화 프로필

요청으로 받지 않는다. `user_id` 로 **DB 에서 직접 읽는다.**

```
public.tbl_user.personal_history   → 경력
public.tbl_user_major.field_id     → 관심 직군
public.tbl_user_skill.skill_id     → 보유 스킬
```

프로필이 있으면 챗봇이 직군·경력·보유 스킬을 감안해 답한다. 없으면 일반 답변으로 간다.

경력 값은 백엔드 5단계를 우리 4단계로 맞춘다.

| `personal_history` | 챗봇 `career_level` |
|---|---|
| `NO_EXPERIENCE` · `ENTRY_LEVEL` | `newcomer` |
| `JUNIOR` | `junior` |
| `MIDDLE` | `mid` |
| `SENIOR` | `senior` |

### 공통 에러 응답

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "요청이 너무 잦습니다. 잠시 후 다시 시도해주세요.",
    "detail": { "reset": 1791116400 }
  }
}
```

| HTTP | code | 설명 |
|---|---|---|
| 400 | `INVALID_REQUEST` | 요청 형식 오류 |
| 404 | `USER_NOT_FOUND` | `user_id` 가 `tbl_user` 에 없음 |
| 404 | `SESSION_NOT_FOUND` | 세션 없음 또는 타 유저 소유 |
| 422 | `VALIDATION_ERROR` | 요청 값 검증 실패. `detail` 에 필드별 사유 |
| 429 | `RATE_LIMITED` | 분당 요청수 또는 일일 토큰 한도 초과. `detail.reset` 은 해제 시각(epoch) |
| 500 | `INTERNAL_ERROR` | 처리되지 않은 서버 오류 |
| 502 | `LLM_UNAVAILABLE` | LLM API 장애 |

> 타 유저 세션 접근은 403 이 아닌 **404** 로 응답한다. 세션 존재 여부 자체를 노출하지 않기 위함.

### 레이트리밋

분당 요청 수와 일일 토큰 사용량 두 가지를 본다. 둘 다 `0` 이면 제한 없음.

```
CHAT_RATE_LIMIT_PER_MINUTE=20
CHAT_DAILY_TOKEN_BUDGET=200000
```

정상 응답에 헤더가 붙는다.

```
X-RateLimit-Limit: 20
X-RateLimit-Remaining: 17
X-RateLimit-Reset: 1791116400
```

- 분당 카운터는 **분 단위 키**라 분이 바뀌면 초기화된다
- 토큰 예산은 **사후 차감**이다. 한 턴은 한도를 넘길 수 있고, 다음 요청이 막힌다
- 하루 경계는 **KST 자정**이다

---

## 1. 챗봇 스트리밍

### `POST /api/chat/stream`

질문을 보내고 SSE 로 답변을 스트리밍 받는다. 호출은 백엔드가 하고, 백엔드가 스트림을 프론트로 넘긴다.

**백엔드 중계 시 지킬 것**

SSE 내용은 가공하지 않고 그대로 프론트에 넘기면 된다. 아래는 스프링부트에서 중계할 때 깨지기 쉬운 지점이다.

- **요청 필드는 snake_case.** `user_id` · `session_id` 로 보낸다. Jackson 기본값(`userId`)으로 보내면 422 가 난다
- **`Flux<String>` 으로 받지 않는다.** `text/event-stream` 으로 반환하면 스프링이 각 줄을 `data:` 로 한 번 더 감싸 `event:` 이름이 깨진다. `ServerSentEvent<String>` 으로 받아 그대로 반환할 것
- **버퍼링 없이 흘려보낸다.** 응답을 모았다가 보내면 답변이 한 번에 몰려 나온다
  - 앞단에 nginx 가 있으면 `proxy_buffering off` 또는 응답에 `X-Accel-Buffering: no`. AI 서버가 보낸 헤더는 자동으로 전달되지 않는다
  - `server.compression` 이 `text/event-stream` 에 걸리면 버퍼링된다
- **타임아웃을 넉넉히 잡는다.** 툴 호출이 섞이면 한 턴이 수십 초 걸린다. MVC 는 `spring.mvc.async.request-timeout` 기본값에 긴 답변이 잘릴 수 있다
- **프론트 연결이 끊기면 AI 서버로의 요청도 끊는다.** 그래야 LLM 호출이 취소된다. WebClient 는 구독 취소로 자동 전파되지만, RestTemplate 등 blocking 방식으로 스트림을 복사하면 끊기지 않아 사용자가 떠난 뒤에도 토큰이 나간다
- **스트리밍 전 에러는 일반 JSON 응답이다.** 429 · 404 · 422 는 SSE 가 아니라 공통 에러 형식으로 온다. 같은 상태 코드로 프론트에 내려줄 것. 스트리밍 시작 뒤 오류는 200 안의 `event: error` 다
- `X-RateLimit-*` 헤더는 필요하면 그대로 프론트에 전달한다

**Request**

```json
{
  "user_id": 1,
  "session_id": 42,
  "message": "React 쓰는 회사는 뭘 같이 요구해?"
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `user_id` | integer | Y | `tbl_user.user_id` |
| `message` | string(1~2000) | Y | 질문 |
| `session_id` | integer | N | 없으면 새 세션 자동 생성 |

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
맨 처음 1회. 새 세션이면 `is_new` 가 `true`.
```
event: session
data: {"session_id":42,"is_new":true}
```

#### `title`
**새 세션일 때만.** 제목 생성은 답변과 동시에 시작하지만, 보내는 건 답변 텍스트가 다 나온 뒤
`done` 직전이다. 제목 생성에 실패하거나 스트림이 `error` 로 끝나면 오지 않는다.
```
event: title
data: {"title":"React 관련 요구 기술"}
```

#### `tool_start`
툴 호출 시작. UI 에 "분석 중..." 표시용.
```
event: tool_start
data: {"tool":"get_related_skills","label":"기술 연관 관계를 분석하고 있어요"}
```

#### `graph`
차트 렌더링용 페이로드. **LLM 이 생성한 텍스트가 아니라 툴이 반환한 원본 수치.**
```
event: graph
data: {
  "id":"g_01",
  "type":"bar",
  "title":"backend 직군에서 많이 요구되는 기술",
  "unit":"공고 수",
  "data":[
    {"label":"Java","value":412},
    {"label":"AWS","value":301}
  ]
}
```

`type` 별 모양이 다르다.

| `type` | 쓰는 툴 | 데이터 |
|---|---|---|
| `bar` | 인기 기술 · 연관 기술 · 수요 · 연봉 분포 · 기업 프로필 · 스킬 갭 | `data: [{label, value}]` |
| `grouped_bar` | 급상승 (직전 기간 vs 최근 기간) | `series: [이름…]` + `data: [{label, values: [n, m]}]` |
| `table` | 구간 비교 · 기업 비교 | `columns: [이름…]` + `rows: [[…]]` |

`id` 는 **답변마다 `g_01` 부터 다시 센다.** 대화 전체에서 유일하지 않으므로 차트는 그 답변 말풍선에
붙이고, 키가 필요하면 말풍선(답변) 단위로 잡는다.

#### `token`
답변 텍스트 조각. **마크다운이 섞여 있다**(`**굵게**`, 목록 등). 프론트에서 마크다운으로 렌더한다.
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

`usage` 는 그 턴에 쓴 토큰 합계다. 툴 호출 때문에 모델이 여러 번 불리면 전부 더한 값이고,
같은 값이 `chat_message` 행에도 저장된다. 제목 생성에 쓴 토큰은 포함하지 않는다.

#### `error`
스트림 도중 오류. 이 이벤트 후 스트림이 종료된다.
```
event: error
data: {"code":"LLM_UNAVAILABLE","message":"답변 생성에 실패했습니다. 잠시 후 다시 시도해주세요.","recoverable":true}
```

| code | 설명 | 후속 처리 |
|---|---|---|
| `LLM_UNAVAILABLE` | LLM API 장애 | 잠시 후 재시도 안내 |
| `INTERNAL_ERROR` | 그 외 서버 오류 | 종료 처리 |

> **스트리밍이 시작된 뒤의 오류는 HTTP 상태가 이미 200 이다.** 레이트리밋·세션 없음처럼
> 스트리밍 전에 걸러지는 것만 4xx 로 나간다.

**이벤트 순서 예시**
```
session → tool_start → graph → token × N → title → done
```

- `session` 은 항상 맨 처음, `done` 은 정상 종료 시 마지막이다
- `tool_start` · `graph` · `token` 은 섞여서 여러 번 온다. 툴 → 글 → 다시 툴 순서도 가능하니 가정하지 말 것
- `title` 은 새 세션일 때만, `done` 직전에 온다
- 오류면 `error` 가 마지막이고 `title` · `done` 은 오지 않는다

### 중간에 끊겼을 때

프론트가 연결을 끊으면 서버가 감지해 LLM 호출을 취소한다. **거기까지 생성된 답변은
저장된다.** 다시 들어오면 중간까지 쓰인 답변이 대화에 남아 있다.

---

## 2. 세션 관리 — 만들지 않는다

백엔드가 `chat` 스키마를 직접 읽는다. 왕복이 하나 줄어서 그렇게 정했다.
백엔드 DB 계정에 `chat` 스키마 `SELECT` 권한이 있어야 한다.

```sql
chat.chat_session      id · user_id · title · last_message_at · created_at
chat.chat_message      id · session_id · role · content · input_tokens · output_tokens · created_at
chat.chat_tool_call    id · message_id · tool_name · arguments · chart_payload
```

- 메시지 순서는 `id` 오름차순이다 (`seq` 컬럼은 없앴다)
- 대화 목록 정렬은 `chat_session.last_message_at` 내림차순
- **재진입 시 차트 복원**은 `chat_tool_call.chart_payload` 를 그대로 쓰면 된다.
  `graph` 이벤트로 보낸 것과 같은 JSON 이다
- 유저를 지우면 `ON DELETE CASCADE` 로 세션·메시지·툴콜이 함께 지워진다

---

## 3. 챗봇이 쓰는 툴

LLM 이 질문을 보고 알아서 고른다. 백엔드가 지정하지 않는다.

| 툴 | 하는 일 | 차트 |
|---|---|---|
| `get_popular_skills` | 많이 요구하는 기술 순위 | bar |
| `get_rising_skills` | 급상승 기술 + 신규 등장 기술 | grouped_bar |
| `compare_segments` | 회사 규모·경력·지역별 스택 비교 | table |
| `get_salary_stats` | 연봉 중앙값·사분위 | bar |
| `get_skill_demand` | 특정 기술의 수요 분포 | bar |
| `get_related_skills` | 함께 요구되는 기술 | bar |
| `get_company_profile` | 기업 한 곳의 공고 수·요구 기술·경력 분포. 이름이 애매하면 후보 목록 | bar |
| `compare_companies` | 기업 두 곳 이상의 요구 기술 비교 | table |
| `get_similar_companies` | 스택·사업 설명이 비슷한 기업 | - |
| `search_postings` | 설명 문장과 의미가 비슷한 공고 검색 | - |
| `search_companies` | 업종·분야 설명으로 기업 검색 | - |
| `analyze_skill_gap` | 보유 기술 대비 부족한 기술. 비우면 프로필의 보유 스킬을 쓴다 | bar |
| `get_data_coverage` | 수집 기간·공고 수·직군 분포 등 데이터 범위 | - |

---

## 4. `GET /health`

```json
{ "db": true, "redis": true }
```

실패한 항목은 `false` 로 표시한다. 상태 코드는 항상 `200`.

---

## 5. 데이터 기준

답변에 쓰이는 수치의 기준이다. 화면 고지 문구를 만들 때 참고.

- **공고의 날짜는 게시일(`posted_at`) 기준**이다. 수집일이 아니다
- 게시일이 없는 공고는 기간 집계에서 빠진다
- 기술 집계는 사전에 있는 스킬만 센다. 공통 도구(Git·Jira 등)는 기본 제외
- 급상승은 증가율만 보지 않는다. 표본이 작은 기술이 상위를 먹지 않도록 보정하고,
  직전 기간에 없던 기술은 "신규 등장" 으로 분리한다

---

## 6. 프론트 연동 참고

프론트는 이 서버를 직접 부르지 않고 **백엔드 엔드포인트**로 스트림을 받는다.
경로·인증 헤더는 백엔드 명세를 따르고, `user_id` 는 백엔드가 토큰에서 채우므로 프론트가 보내지 않는다.
이벤트 형식은 1절 그대로 전달된다. 아래는 SSE 수신 쪽 참고용이다.

> 브라우저 `EventSource` 는 POST 와 커스텀 헤더(인증 토큰)를 보낼 수 없다.
> `fetch` + `ReadableStream` 으로 구현할 것.

```js
const ctrl = new AbortController();

const res = await fetch("{백엔드 챗 엔드포인트}", {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  body: JSON.stringify({ session_id, message }),
  signal: ctrl.signal,
});

const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
// SSE 프레임 파싱: "event: {name}\ndata: {json}\n\n"
```

중단은 `ctrl.abort()`.

| 이벤트 | UI 동작 |
|---|---|
| `session` | 새 세션이면 사이드바 목록에 추가 |
| `title` | 사이드바·헤더의 제목 갱신 |
| `tool_start` | `label` 을 로딩 인디케이터에 표시 |
| `graph` | 현재 답변 말풍선에 차트 렌더링 (`id` 는 답변 안에서만 유일) |
| `token` | 말풍선에 append (마크다운 렌더) |
| `done` | 로딩 해제, `message_id` 보관 |
| `error` | `recoverable` 이면 인라인 경고, 아니면 종료 처리 |

---

## 7. 미구현 · 미확정

| 항목 | 현재 | 비고 |
|---|---|---|
| 인증 | 백엔드 담당. AI 서버는 본문 `user_id` 신뢰 | 확정 |
| 추천 질문 API | 미구현 | 필요해지면 추가 |
| 수집 현황 API | 미구현 | 챗봇 툴(`get_data_coverage`)로 제공 중 |
| 운영용 내부 API | 미구현 | 현재는 CLI(`app.cli`)로 처리 |
| 로드맵 API | 미설계 | 챗봇 완료 후 |
