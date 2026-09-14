# HNSW 인덱스를 대량 적재 뒤로 미룬다

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from app.domains.market import vector_index

revision: str = "4c1f9a7d2e08"
down_revision: str | None = "01bed769534d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 인덱스는 적재 뒤 uv run python -m app.cli vector-index --build 로 만든다
def upgrade() -> None:
    for statement in vector_index.DROP_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for _, ddl in vector_index.INDEXES:
        op.execute(ddl)
