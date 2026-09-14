# RawJob 스키마 검증 테스트

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
        make(employmentType="정규직")

    assert any(e["type"] == "extra_forbidden" for e in caught.value.errors())


def test_unknown_field_is_rejected_on_merge() -> None:
    job = make()

    with pytest.raises(ValidationError) as caught:
        job.merged({"detail_fetched": True, "employment_typo": "정규직"})

    assert any(e["type"] == "extra_forbidden" for e in caught.value.errors())


def test_merge_keeps_untouched_fields() -> None:
    job = make(tech_stacks=["Python"], career_min=3)

    merged = job.merged({"detail_fetched": True, "employment_type": "정규직"})

    assert merged.employment_type == "정규직"
    assert merged.detail_fetched is True
    assert merged.tech_stacks == ["Python"]
    assert merged.career_min == 3
    assert job.detail_fetched is False


def test_employment_type_survives_the_round_trip() -> None:
    assert "employment_type" in RawJob.model_fields
    assert make(employment_type="계약직").employment_type == "계약직"


def test_content_hash_ignores_employment_type() -> None:
    # 해시에 넣으면 기존 공고가 전부 변경으로 잡혀 재수집된다
    assert make().content_hash() == make(employment_type="정규직").content_hash()
