"""upsert_posting 의 ON CONFLICT 가 모든 컬럼을 다루는지 검사한다.

배경: employment_type · salary_period · body_extract_failed 세 컬럼이 INSERT
값에는 들어가는데 ON CONFLICT DO UPDATE 의 set_ 에는 없었다. 그래서

    - 새로 들어온 공고는 값이 찬다
    - **이미 있는 공고는 몇 번을 재수집해도 옛날 값 그대로다**

파서를 고치고 --force-reextract 로 전량 재수집을 돌려도 아무것도 안 바뀌는
현상으로 드러났다. 컬럼을 늘릴 때마다 사람이 두 목록을 대조하는 것은 실패하는
방식이라, 여기서 자동으로 대조한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.domains.market import repository
from app.domains.market.models import JobPosting

SERVICE = Path(repository.__file__).parent.parent / "crawler" / "service.py"

# 충돌 키. 갱신 대상이 아니다.
CONFLICT_KEYS = {"source", "source_job_id"}
# DB 기본값/트리거가 관리한다.
DB_MANAGED = {"id", "created_at", "updated_at"}
# 임베딩 파이프라인이 따로 관리한다. 수집이 건드리면 안 된다.
EMBED_OWNED = {"embed_hash"}


def _columns_service_inserts() -> set[str]:
    """service._save_one 이 upsert_posting 에 넘기는 컬럼 이름.

    raw_fields 안에 중첩된 JSON 키(url · locations 등)는 컬럼이 아니므로
    실제 테이블 컬럼과 교집합을 취해 걸러낸다.
    """
    source = SERVICE.read_text(encoding="utf-8")
    block = source.split("await repository.upsert_posting(")[1].split("},\n        )")[0]
    names = set(re.findall(r'"(\w+)":', block))
    return names & {c.name for c in JobPosting.__table__.columns}


def _columns_on_conflict_updates() -> set[str]:
    """ON CONFLICT DO UPDATE 가 실제로 갱신하는 컬럼.

    문자열을 읽지 않고 실제 statement 를 만들어 확인한다.
    """
    values = {c.name: None for c in JobPosting.__table__.columns if c.name != "id"}
    stmt = pg_insert(JobPosting).values(**values)
    set_ = dict(repository._conflict_update_set(stmt.excluded))
    return set(set_)


def test_every_inserted_column_is_updated_on_conflict() -> None:
    """★ INSERT 하는 컬럼은 ON CONFLICT 에서도 반드시 다뤄져야 한다."""
    inserted = _columns_service_inserts() - CONFLICT_KEYS
    updated = _columns_on_conflict_updates()

    missing = sorted(inserted - updated)
    assert not missing, (
        f"ON CONFLICT 에서 빠진 컬럼: {missing}. "
        "기존 행이 영원히 갱신되지 않는다. "
        "_COALESCE_ON_UPDATE 나 upsert_posting 의 set_ 에 추가하라."
    )


def test_the_three_columns_that_were_missing_are_covered() -> None:
    """실제로 구멍에 빠져 있던 셋. 회귀 방지."""
    updated = _columns_on_conflict_updates()
    for column in ("employment_type", "salary_period", "body_extract_failed"):
        assert column in updated, f"{column} 이 다시 빠졌다"


def test_embed_hash_is_never_touched_by_the_crawler() -> None:
    """수집이 embed_hash 를 건드리면 임베딩 상태가 통째로 꼬인다."""
    assert not (_columns_on_conflict_updates() & EMBED_OWNED)
    assert not (_columns_service_inserts() & EMBED_OWNED)


def test_parse_verdicts_overwrite_rather_than_coalesce() -> None:
    """body_is_image · body_extract_failed 는 매 파싱의 판정이다.

    COALESCE 로 두면 한 번 true 가 된 공고가 파서를 고친 뒤에도 true 로 남는다.
    """
    for column in ("body_is_image", "body_extract_failed"):
        assert column not in repository._COALESCE_ON_UPDATE
