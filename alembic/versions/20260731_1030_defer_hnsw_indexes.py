"""defer hnsw indexes to after bulk load

Revision ID: 4c1f9a7d2e08
Revises: 01bed769534d
Create Date: 2026-07-31 10:30:00.000000

★ HNSW 인덱스를 스키마에서 떼어낸다.

baseline 은 빈 테이블에 HNSW 인덱스를 먼저 만들었다. 그러면 임베딩 적재가
INSERT 마다 그래프를 갱신하느라 몇 배 느려지고, 한 행씩 쌓아 만든 그래프는
한 번에 만든 것보다 품질도 낮다.

순서를 이렇게 바꾼다.

    ① alembic upgrade head        스키마만 (벡터 컬럼은 있고 인덱스는 없다)
    ② 수집 · 임베딩                대량 INSERT
    ③ uv run python -m app.cli vector-index --build

인덱스가 없어도 벡터 검색은 순차 스캔으로 동작한다. 느릴 뿐 틀리지 않는다.
DDL 원본은 app/domains/market/vector_index.py 에 있고 CLI 와 공유한다.

downgrade 는 인덱스를 도로 만든다(baseline 상태 복원).

models.py 의 __table_args__ 에 있는 Index 선언은 그대로 둔다. ③ 까지 끝낸
상태가 정상 상태이고, 그때 autogenerate 가 빈 diff 를 뱉어야 하기 때문이다.
② 와 ③ 사이에서 autogenerate 를 돌리면 인덱스 생성을 제안하는데, 그건
맞는 말이다 — 아직 안 만들었으니까.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from app.domains.market import vector_index

revision: str = "4c1f9a7d2e08"
down_revision: str | None = "01bed769534d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in vector_index.DROP_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for _, ddl in vector_index.INDEXES:
        op.execute(ddl)
