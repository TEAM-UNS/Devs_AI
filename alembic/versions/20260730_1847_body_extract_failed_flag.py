# body_extract_failed 컬럼 추가

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "01bed769534d"
down_revision: str | None = "8b80cea40590"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_posting",
        sa.Column(
            "body_extract_failed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        schema="market",
    )


def downgrade() -> None:
    op.drop_column("job_posting", "body_extract_failed", schema="market")
