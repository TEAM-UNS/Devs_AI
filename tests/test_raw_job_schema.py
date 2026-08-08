"""RawJob 계약 — 어댑터 오타를 조용히 삼키지 않는다.

배경: employment_type 이 RawJob 에 없는데 세 어댑터가 모두 그 값을 넘겼다.
pydantic 기본값(extra="ignore")이라 조용히 버려졌고, job_posting 660건이
NULL 로 쌓이는 동안 아무도 몰랐다. 여기서 그 조합을 못 박는다.

    - 생성 경로:  RawJob(...)          → extra="forbid" 가 막는다
    - 상세 경로:  job.merged({...})    → 재검증하므로 여기서도 막힌다
                  (model_copy(update=) 는 검증을 건너뛴다 — 그래서 안 쓴다)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domains.crawler.schemas import RawJob


def make(**overrides) -> RawJob:
    return RawJob(
        source="jumpit",
        source_job_id="1",
        url="https://example.test/1",
        title="백엔드 개발자",
        company_name="테스트",
        **overrides,
    )


def test_unknown_field_is_rejected_on_construction() -> None:
    with pytest.raises(ValidationError) as caught:
        make(employmentType="정규직")  # 오타 (스네이크가 아니라 카멜)

    assert any(e["type"] == "extra_forbidden" for e in caught.value.errors())


def test_unknown_field_is_rejected_on_merge() -> None:
    """★ 상세 경로. model_copy(update=) 였다면 조용히 통과했을 것이다."""
    job = make()

    with pytest.raises(ValidationError) as caught:
        job.merged({"detail_fetched": True, "employment_typo": "정규직"})

    assert any(e["type"] == "extra_forbidden" for e in caught.value.errors())


def test_merge_keeps_untouched_fields() -> None:
    job = make(tech_stacks=["Python"], career_min=3)

    merged = job.merged({"detail_fetched": True, "employment_type": "정규직"})

    assert merged.employment_type == "정규직"
    assert merged.detail_fetched is True
    assert merged.tech_stacks == ["Python"]  # 안 건드린 값은 그대로
    assert merged.career_min == 3
    assert job.detail_fetched is False  # 원본은 불변


def test_employment_type_survives_the_round_trip() -> None:
    """이 필드가 사라졌던 게 사고의 원인이었다."""
    assert "employment_type" in RawJob.model_fields
    assert make(employment_type="계약직").employment_type == "계약직"


def test_content_hash_ignores_employment_type() -> None:
    """해시에 안 들어간다 — 넣으면 660건이 전부 '변경됨' 으로 잡혀 재수집된다."""
    assert make().content_hash() == make(employment_type="정규직").content_hash()
