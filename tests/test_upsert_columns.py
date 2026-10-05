# 공고 upsert 갱신 컬럼 테스트

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import functions

from app.domains.crawler import repository
from app.domains.crawler.models import JobPosting

SERVICE = Path(repository.__file__).parent.parent / "crawler" / "service.py"

CONFLICT_KEYS = {"source", "source_job_id"}
DB_MANAGED = {"id", "created_at", "updated_at"}
EMBED_OWNED = {"embed_hash"}


def _columns_service_inserts() -> set[str]:
    source = SERVICE.read_text(encoding="utf-8")
    block = source.split("await repository.upsert_posting(")[1].split("},\n        )")[0]
    names = set(re.findall(r'"(\w+)":', block))
    return names & {c.name for c in JobPosting.__table__.columns}


def _conflict_set() -> dict[str, object]:
    values = {c.name: None for c in JobPosting.__table__.columns if c.name != "id"}
    stmt = pg_insert(JobPosting).values(**values)
    return dict(repository._conflict_update_set(stmt.excluded))


def _columns_on_conflict_updates() -> set[str]:
    return set(_conflict_set())


def test_every_inserted_column_is_updated_on_conflict() -> None:
    inserted = _columns_service_inserts() - CONFLICT_KEYS
    updated = _columns_on_conflict_updates()

    missing = sorted(inserted - updated)
    assert not missing, (
        f"ON CONFLICT 에서 빠진 컬럼: {missing}. "
        "기존 행이 영원히 갱신되지 않는다. "
        "repository._conflict_update_set 에 추가하라."
    )


def test_the_three_columns_that_were_missing_are_covered() -> None:
    updated = _columns_on_conflict_updates()
    for column in ("employment_type", "salary_period", "body_extract_failed"):
        assert column in updated, f"{column} 이 다시 빠졌다"


def test_embed_hash_is_never_touched_by_the_crawler() -> None:
    assert not (_columns_on_conflict_updates() & EMBED_OWNED)
    assert not (_columns_service_inserts() & EMBED_OWNED)


def test_parse_verdicts_overwrite_rather_than_coalesce() -> None:
    # COALESCE 로 두면 한 번 true 가 된 값이 파서를 고친 뒤에도 남는다
    set_ = _conflict_set()
    for column in ("body_is_image", "body_extract_failed"):
        assert not isinstance(set_[column], functions.coalesce)
