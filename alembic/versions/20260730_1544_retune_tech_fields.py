"""retune tech fields

분류 체계를 스킬 사전과 맞춘다.

    android · ios   → mobile
    data · ai       → data_ai
    etc             → NULL (매핑 실패를 "기타" 뒤에 숨기지 않는다)
    신규            security · game · embedded

기존 공고의 field_id 는 위 대응대로 옮긴 뒤 옛 행을 지운다.

Revision ID: 3e23078a364f
Revises: 6e3c0e6b14ca
Create Date: 2026-07-30 15:44:38.906005
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "3e23078a364f"
down_revision: str | None = "6e3c0e6b14ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, name, sort_order) — app/domains/market/seed_data.FIELD_CATALOG 와 일치해야 한다.
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

# 옛 코드 → 새 코드 (None 이면 field_id 를 비운다)
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

    # 공고를 새 분류로 옮긴다. 옛 행을 지우기 전에 해야 한다
    # (FK 가 ON DELETE SET NULL 이라 순서를 어기면 매핑이 통째로 날아간다).
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

    # mobile · data_ai 는 원래 두 갈래였다. 어느 쪽이었는지 정보가 없으므로
    # 대표값(android · data)으로 되돌린다. 손실이 있는 다운그레이드다.
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
