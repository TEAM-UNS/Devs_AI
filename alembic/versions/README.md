# 마이그레이션 운용 규칙

## 스키마 원본은 `init.sql`

로컬은 `docker compose up -d` 시 `init.sql` 이 자동 실행되어 스키마가 완성된다.
따라서 **첫 마이그레이션은 baseline stamp 로 처리**한다.

```bash
alembic stamp head
```

(아직 리비전이 하나도 없으면 아래 "베이스라인 리비전 만들기" 를 먼저 수행)

## 베이스라인 리비전 만들기

`app/domains/*/models.py` 를 작성한 뒤:

```bash
alembic revision --autogenerate -m "baseline"
```

생성된 파일을 `init.sql` 과 **눈으로 대조**한다. 특히 아래는 autogenerate 가
놓치거나 다르게 뽑는 항목이라 수동 확인이 필요하다.

| 항목 | 확인 포인트 |
|---|---|
| `CREATE EXTENSION vector` | autogenerate 에 안 잡힘 → `op.execute` 로 직접 추가 |
| HNSW 인덱스 | `postgresql_using="hnsw"` + `postgresql_with` 옵션 유지 |
| 부분 인덱스 | `postgresql_where` 조건이 init.sql 과 동일한지 |
| CHECK 제약 | 이름이 init.sql 과 같은지 (다르면 drop/create 가 반복됨) |
| 트리거 `touch_updated_at` | ORM 이 모르는 객체 → `op.execute` 로 관리 |
| 롤/GRANT | alembic 관리 대상 아님. 운영 반영은 별도 스크립트 |

## 이후 변경

스키마 변경은 `models.py` 수정 → autogenerate → 리뷰 순으로 진행하고,
`init.sql` 도 같은 내용으로 함께 갱신한다(신규 개발자 부트스트랩용).

## 벡터 차원 변경

`vector(1024)` 를 바꾸면 기존 임베딩이 전부 무효가 된다.
컬럼 타입 변경 + `embed_hash = NULL` 로 초기화 + 전체 재임베딩까지 한 리비전에 묶을 것.
