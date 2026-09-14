# requirement body 와 스킬 플래그, salary_period 추가

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8b80cea40590"
down_revision: str | None = "3e23078a364f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_posting",
        sa.Column("salary_period", sa.String(length=8), nullable=True),
        schema="market",
    )
    op.add_column(
        "skill",
        sa.Column("is_common", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="market",
    )
    op.add_column(
        "skill_alias",
        sa.Column("case_sensitive", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="market",
    )

    # autogenerate 는 CHECK 제약을 비교하지 않는다
    op.drop_constraint(
        "posting_skill_requirement_chk", "posting_skill", schema="market", type_="check"
    )
    op.create_check_constraint(
        "posting_skill_requirement_chk",
        "posting_skill",
        "requirement IN ('required', 'preferred', 'tag', 'body')",
        schema="market",
    )
    op.create_check_constraint(
        "job_posting_salary_period_chk",
        "job_posting",
        "salary_period IS NULL OR salary_period IN ('annual', 'monthly', 'hourly')",
        schema="market",
    )


def downgrade() -> None:
    # body 를 preferred 로 먼저 되돌려야 3값 제약을 걸 수 있다. 다시 올리면 reparse 가 필요하다
    op.execute(
        "UPDATE market.posting_skill SET requirement = 'preferred' WHERE requirement = 'body'"
    )
    op.drop_constraint(
        "job_posting_salary_period_chk", "job_posting", schema="market", type_="check"
    )
    op.drop_constraint(
        "posting_skill_requirement_chk", "posting_skill", schema="market", type_="check"
    )
    op.create_check_constraint(
        "posting_skill_requirement_chk",
        "posting_skill",
        "requirement IN ('required', 'preferred', 'tag')",
        schema="market",
    )

    op.drop_column("skill_alias", "case_sensitive", schema="market")
    op.drop_column("skill", "is_common", schema="market")
    op.drop_column("job_posting", "salary_period", schema="market")
