# 기술 분야 분류 재편

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "3e23078a364f"
down_revision: str | None = "6e3c0e6b14ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_FIELDS = [
    ("backend", "백엔드", 10),
    ("frontend", "프론트엔드", 20),
    ("mobile", "모바일", 30),
    ("data_ai", "데이터/AI", 40),
    ("devops", "DevOps/인프라", 50),
    ("security", "보안", 60),
    ("game", "게임", 70),
    ("embedded", "임베디드", 80),
]

REMAP = {
    "android": "mobile",
    "ios": "mobile",
    "data": "data_ai",
    "ai": "data_ai",
    "etc": None,
}

OLD_FIELDS = [
    ("backend", "백엔드", 10),
    ("frontend", "프론트엔드", 20),
    ("android", "안드로이드", 30),
    ("ios", "iOS", 40),
    ("data", "데이터", 50),
    ("devops", "DevOps/인프라", 60),
    ("ai", "AI/ML", 70),
    ("etc", "기타", 99),
]


def _upsert_fields(rows: list[tuple[str, str, int]]) -> None:
    values = ", ".join(f"('{code}', '{name}', {order})" for code, name, order in rows)
    op.execute(
        f"INSERT INTO market.tech_field (code, name, sort_order) VALUES {values} "
        "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, "
        "sort_order = EXCLUDED.sort_order"
    )


def upgrade() -> None:
    _upsert_fields(NEW_FIELDS)

    # 옛 행을 지우기 전에 옮겨야 한다. FK 가 ON DELETE SET NULL 이다
    for old_code, new_code in REMAP.items():
        if new_code is None:
            op.execute(
                "UPDATE market.job_posting SET field_id = NULL WHERE field_id = "
                f"(SELECT id FROM market.tech_field WHERE code = '{old_code}')"
            )
        else:
            op.execute(
                "UPDATE market.job_posting SET field_id = "
                f"(SELECT id FROM market.tech_field WHERE code = '{new_code}') "
                "WHERE field_id = "
                f"(SELECT id FROM market.tech_field WHERE code = '{old_code}')"
            )

    old_codes = ", ".join(f"'{c}'" for c in REMAP)
    op.execute(f"DELETE FROM market.tech_field WHERE code IN ({old_codes})")


def downgrade() -> None:
    _upsert_fields(OLD_FIELDS)

    # 손실 있는 다운그레이드다. 원래 어느 코드였는지 알 수 없어 대표값으로 되돌린다
    for new_code, old_code in (("mobile", "android"), ("data_ai", "data")):
        op.execute(
            "UPDATE market.job_posting SET field_id = "
            f"(SELECT id FROM market.tech_field WHERE code = '{old_code}') "
            "WHERE field_id = "
            f"(SELECT id FROM market.tech_field WHERE code = '{new_code}')"
        )

    op.execute(
        "DELETE FROM market.tech_field WHERE code IN ('mobile', 'data_ai', "
        "'security', 'game', 'embedded')"
    )
