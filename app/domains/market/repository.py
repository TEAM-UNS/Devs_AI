"""쓰기 담당 — 크롤러/임베딩 전용. (챗봇은 접근 금지, R3)

전부 postgres 의 INSERT ... ON CONFLICT 를 쓴다.
"먼저 SELECT 해서 있으면 UPDATE" 는 동시 실행 시 경합이 나므로 쓰지 않는다.

부분 갱신 규칙
    상세 수집에 실패하면 description 같은 필드가 None 으로 온다. 그때
    기존 값을 지우면 안 되므로 COALESCE(신규, 기존) 으로 덮어쓴다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import enums
from app.domains.market.models import (
    Company,
    CompanySource,
    CrawlRun,
    JobPosting,
    PostingChunk,
    PostingSkill,
    Skill,
    SkillAlias,
    SkillField,
    TechField,
)
from app.domains.market.schemas import SkillDictionaryRow, UnmatchedTag

# 상세 실패 시 기존 값을 유지해야 하는 컬럼
#
# ★ INSERT 에 넣는 컬럼은 여기나 upsert_posting 의 set_ 둘 중 하나에 반드시
#   있어야 한다. 어느 쪽에도 없으면 ON CONFLICT 에서 조용히 빠져 **기존 행은
#   영원히 갱신되지 않는다** — 새로 들어온 공고만 값이 차고, 이미 있는 공고는
#   재수집을 몇 번 해도 옛날 값 그대로다.
#   employment_type · salary_period · body_extract_failed 셋이 실제로 이
#   구멍에 빠져 있었다. tests/test_upsert_columns.py 가 이 규칙을 강제한다.
_COALESCE_ON_UPDATE = (
    "description",
    "welfare",
    "salary_raw",
    "salary_min",
    "salary_max",
    "location",
    "education",
    # 상세(view-ajax)를 못 받으면 None 으로 온다. 알아낸 값을 지우지 않는다.
    "employment_type",
    # salary_min/max 와 짝이다. 금액만 남고 기준이 사라지면 통계가 왜곡된다.
    "salary_period",
    "posted_at",
    "expires_at",
    "company_id",
    "field_id",
)


# ── 분류 ────────────────────────────────────────────────────────────────────
async def get_field_ids(session: AsyncSession) -> dict[str, int]:
    """tech_field code → id. 매 공고마다 조회하지 않도록 한 번에 받아둔다."""
    rows = (await session.exec(select(TechField.code, TechField.id))).all()
    return {code: fid for code, fid in rows}


# ── 기업 ────────────────────────────────────────────────────────────────────
async def upsert_company(
    session: AsyncSession,
    *,
    name: str,
    name_key: str,
    description: str | None = None,
    homepage: str | None = None,
    founded: str | None = None,
    industry: str | None = None,
    employee_count: int | None = None,
    revenue: int | None = None,
    size_type: enums.CompanySize = enums.CompanySize.UNKNOWN,
) -> int:
    """name_key 기준 병합. 기존 값이 있으면 지우지 않는다."""
    stmt = pg_insert(Company).values(
        name_key=name_key,
        name=name,
        description=description,
        homepage=(homepage or None) and homepage[:500],
        founded=(founded or None) and founded[:20],
        industry=(industry or None) and industry[:120],
        employee_count=employee_count,
        revenue=revenue,
        size_type=size_type.value,
    )
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[Company.name_key],
        set_={
            "name": excluded.name,
            "description": func.coalesce(excluded.description, Company.description),
            "homepage": func.coalesce(excluded.homepage, Company.homepage),
            "founded": func.coalesce(excluded.founded, Company.founded),
            "industry": func.coalesce(excluded.industry, Company.industry),
            "employee_count": func.coalesce(excluded.employee_count, Company.employee_count),
            "revenue": func.coalesce(excluded.revenue, Company.revenue),
            # 이미 알아낸 규모를 unknown 으로 덮어쓰지 않는다.
            # NULLIF 만 쓰면 NULL 이 들어가 NOT NULL 위반이 나므로 COALESCE 로 감싼다.
            "size_type": func.coalesce(
                func.nullif(excluded.size_type, enums.CompanySize.UNKNOWN.value),
                Company.size_type,
            ),
        },
    ).returning(Company.id)
    company_id = (await session.exec(stmt)).one()
    return company_id if isinstance(company_id, int) else company_id[0]


async def upsert_company_source(
    session: AsyncSession,
    *,
    company_id: int,
    source: str,
    source_company_id: str,
    url: str | None = None,
) -> None:
    """사이트별 기업 식별자. 오병합을 사후에 추적하기 위한 원장."""
    stmt = pg_insert(CompanySource).values(
        company_id=company_id,
        source=source,
        source_company_id=source_company_id,
        url=url,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="company_source_uk",
        set_={"company_id": stmt.excluded.company_id, "url": stmt.excluded.url},
    )
    await session.exec(stmt)


# ── 스킬 ────────────────────────────────────────────────────────────────────
async def upsert_skills(session: AsyncSession, names: Iterable[str]) -> dict[str, int]:
    """스킬 이름들을 보장 생성하고 name → id 를 돌려준다."""
    unique = sorted({n.strip() for n in names if n and n.strip()})
    if not unique:
        return {}

    # 이미 있으면 건드리지 않는다 (updated_at 이 매 수집마다 흔들리는 것 방지)
    await session.exec(
        pg_insert(Skill)
        .values([{"name": n} for n in unique])
        .on_conflict_do_nothing(index_elements=[Skill.name])
    )
    rows = (await session.exec(select(Skill.name, Skill.id).where(Skill.name.in_(unique)))).all()
    return {name: sid for name, sid in rows}


async def load_skill_entries(session: AsyncSession) -> list[SkillDictionaryRow]:
    """추출기용 별칭 사전 전체. 200행 규모라 한 번에 읽어 메모리에 올린다.

    정규 표기(name)는 별칭 테이블에 없지만 매칭 대상이므로 여기서 합쳐준다.
    단, 대소문자 구분 별칭에 같은 표기가 있으면 그쪽이 우선이다
    (예: "C" 는 cs_aliases 로만 매칭해야 한다).
    """
    skills = (
        await session.exec(select(Skill.id, Skill.name, Skill.is_ambiguous, Skill.is_common))
    ).all()
    aliases = (
        await session.exec(select(SkillAlias.skill_id, SkillAlias.alias, SkillAlias.case_sensitive))
    ).all()

    insensitive: dict[int, list[str]] = defaultdict(list)
    sensitive: dict[int, list[str]] = defaultdict(list)
    for skill_id, alias, case_sensitive in aliases:
        (sensitive if case_sensitive else insensitive)[skill_id].append(alias)

    rows: list[SkillDictionaryRow] = []
    for skill_id, name, is_ambiguous, is_common in skills:
        cs = sensitive[skill_id]
        plain = list(insensitive[skill_id])
        if not any(a.lower() == name.lower() for a in cs):
            plain.append(name)
        rows.append(
            SkillDictionaryRow(
                skill_id=skill_id,
                name=name,
                is_ambiguous=is_ambiguous,
                is_common=is_common,
                aliases=plain,
                cs_aliases=cs,
            )
        )
    return rows


async def count_unmatched_tags(
    session: AsyncSession, *, source: str | None = None
) -> tuple[list[UnmatchedTag], int, int]:
    """사이트 태그 중 사전에 없는 값을 빈도순으로.

    (미매칭 목록, 전체 태그 수, 매칭된 태그 수) 를 돌려준다.
    tags_raw 가 jsonb 배열이라 jsonb_array_elements_text 로 편다.

    별칭 비교는 소문자로 한다. 대소문자 구분 별칭("CAN")도 태그 매칭에서는
    사이트가 직접 준 값이라 신뢰하므로 소문자 비교로 충분하다.
    """
    where_source = "WHERE p.source = :source" if source else ""
    sql = text(
        f"""
        WITH tags AS (
            SELECT lower(trim(t.tag)) AS tag
            FROM market.job_posting p,
                 LATERAL jsonb_array_elements_text(p.tags_raw) AS t(tag)
            {where_source}
        ),
        marked AS (
            SELECT tag,
                   EXISTS (
                       SELECT 1 FROM market.skill_alias a WHERE lower(a.alias) = tags.tag
                       UNION ALL
                       SELECT 1 FROM market.skill s WHERE lower(s.name) = tags.tag
                   ) AS matched
            FROM tags
        )
        SELECT tag, matched, COUNT(*) AS cnt
        FROM marked GROUP BY tag, matched ORDER BY cnt DESC
        """
    )
    params = {"source": source} if source else {}
    rows = (await session.exec(sql.bindparams(**params))).all()

    unmatched = [UnmatchedTag(tag=tag, count=cnt) for tag, matched, cnt in rows if not matched]
    total = sum(cnt for _, _, cnt in rows)
    matched_count = sum(cnt for _, matched, cnt in rows if matched)
    return unmatched, total, matched_count


async def upsert_tech_fields(
    session: AsyncSession, rows: Sequence[tuple[str, str, int]]
) -> dict[str, int]:
    """(code, name, sort_order) 목록을 반영하고 code → id 를 돌려준다."""
    if rows:
        stmt = pg_insert(TechField).values(
            [{"code": c, "name": n, "sort_order": o} for c, n, o in rows]
        )
        await session.exec(
            stmt.on_conflict_do_update(
                index_elements=[TechField.code],
                set_={"name": stmt.excluded.name, "sort_order": stmt.excluded.sort_order},
            )
        )
    return await get_field_ids(session)


async def upsert_skill(
    session: AsyncSession,
    *,
    name: str,
    category: str | None,
    is_ambiguous: bool,
    is_common: bool = False,
) -> int:
    stmt = pg_insert(Skill).values(
        name=name, category=category, is_ambiguous=is_ambiguous, is_common=is_common
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Skill.name],
        set_={
            "category": stmt.excluded.category,
            "is_ambiguous": stmt.excluded.is_ambiguous,
            "is_common": stmt.excluded.is_common,
        },
    ).returning(Skill.id)
    skill_id = (await session.exec(stmt)).one()
    return skill_id if isinstance(skill_id, int) else skill_id[0]


async def replace_skill_aliases(
    session: AsyncSession,
    skill_id: int,
    aliases: Sequence[str],
    cs_aliases: Sequence[str] = (),
) -> int:
    """이 스킬의 별칭을 통째로 교체한다. 실제로 심긴 개수를 돌려준다.

    alias 는 전역 UNIQUE 다. 다른 스킬이 이미 쓰고 있으면 조용히 건너뛴다
    (사전에 중복이 있다는 뜻이므로 시드 스크립트가 미리 잡아낸다).
    """
    await session.exec(delete(SkillAlias).where(SkillAlias.skill_id == skill_id))
    rows = [{"skill_id": skill_id, "alias": a, "case_sensitive": False} for a in aliases]
    rows += [{"skill_id": skill_id, "alias": a, "case_sensitive": True} for a in cs_aliases]
    if not rows:
        return 0
    stmt = (
        pg_insert(SkillAlias)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[SkillAlias.alias])
        .returning(SkillAlias.id)
    )
    return len((await session.exec(stmt)).all())


async def replace_skill_fields(
    session: AsyncSession, skill_id: int, field_ids: Sequence[int]
) -> None:
    await session.exec(delete(SkillField).where(SkillField.skill_id == skill_id))
    if not field_ids:
        return
    await session.exec(
        pg_insert(SkillField)
        .values([{"skill_id": skill_id, "field_id": fid} for fid in field_ids])
        .on_conflict_do_nothing()
    )


async def iter_postings_for_reparse(
    session: AsyncSession, *, source: str | None = None, limit: int | None = None
) -> list[tuple[int, str | None, list[str], dict[str, Any], int | None]]:
    """재추출 대상. (posting_id, description, tags_raw, raw_fields, field_id)

    이미 저장된 본문만 쓴다. 재수집하지 않는다.
    raw_fields 의 job_categories 로 분야도 다시 계산할 수 있게 함께 넘긴다.
    """
    stmt = select(
        JobPosting.id,
        JobPosting.description,
        JobPosting.tags_raw,
        JobPosting.raw_fields,
        JobPosting.field_id,
    )
    if source:
        stmt = stmt.where(JobPosting.source == source)
    stmt = stmt.order_by(JobPosting.id)
    if limit:
        stmt = stmt.limit(limit)
    return [
        (pid, desc, tags or [], raw or {}, field_id)
        for pid, desc, tags, raw, field_id in (await session.exec(stmt)).all()
    ]


async def update_posting_body(
    session: AsyncSession,
    *,
    source: str,
    source_job_id: str,
    description: str | None,
    body_is_image: bool,
    body_extract_failed: bool,
) -> bool:
    """본문만 갈아끼운다. 스냅샷 재파싱 전용.

    수집 원본(회사·조건·해시)은 건드리지 않는다.
    갱신된 행이 있으면 True.
    """
    result = await session.exec(
        JobPosting.__table__.update()
        .where(JobPosting.source == source, JobPosting.source_job_id == source_job_id)
        .values(
            description=description,
            body_is_image=body_is_image,
            body_extract_failed=body_extract_failed,
        )
    )
    return bool(result.rowcount)


async def mark_body_extract_failed(session: AsyncSession, posting_id: int) -> bool:
    """본문 검증에 실패한 공고를 표시한다. 집계 쿼리가 제외 대상으로 쓴다.

    이미지 공고는 건드리지 않는다. 둘은 배타적이다 —
    이미지 공고는 "원래 텍스트가 없는 것"이고 실패는 "우리가 못 가져온 것"이다.
    """
    result = await session.exec(
        JobPosting.__table__.update()
        .where(JobPosting.id == posting_id, JobPosting.body_is_image.is_(False))
        .values(body_extract_failed=True)
    )
    return bool(result.rowcount)


async def update_posting_field(
    session: AsyncSession, posting_id: int, field_id: int | None
) -> None:
    """분야 재매핑. 분류 규칙이나 tech_field 체계가 바뀐 뒤 쓴다."""
    await session.exec(
        JobPosting.__table__.update().where(JobPosting.id == posting_id).values(field_id=field_id)
    )


async def replace_posting_skills(
    session: AsyncSession,
    posting_id: int,
    rows: Sequence[tuple[int, enums.Requirement, int]],
) -> None:
    """공고의 스킬 목록을 통째로 교체한다 (부분 갱신하지 않는다).

    rows 는 (skill_id, requirement, mentions).
    """
    await session.exec(delete(PostingSkill).where(PostingSkill.posting_id == posting_id))
    if not rows:
        return
    await session.exec(
        pg_insert(PostingSkill)
        .values(
            [
                {
                    "posting_id": posting_id,
                    "skill_id": skill_id,
                    "requirement": requirement.value,
                    "mentions": mentions,
                }
                for skill_id, requirement, mentions in rows
            ]
        )
        .on_conflict_do_nothing()
    )


# ── 공고 ────────────────────────────────────────────────────────────────────
async def get_content_hashes(
    session: AsyncSession, source: str, source_job_ids: Sequence[str]
) -> dict[str, str | None]:
    """변경 감지용. 이미 있는 공고의 content_hash 를 한 번에 가져온다."""
    if not source_job_ids:
        return {}
    rows = (
        await session.exec(
            select(JobPosting.source_job_id, JobPosting.content_hash).where(
                JobPosting.source == source,
                JobPosting.source_job_id.in_(list(source_job_ids)),
            )
        )
    ).all()
    return {sjid: h for sjid, h in rows}


async def recent_source_ids(session: AsyncSession, source: str, *, days: int) -> set[str]:
    """최근 days 일 안에 수집한 source_job_id 집합. ★ 증분 수집의 핵심.

    목록 단계에서 이 집합으로 거른다. 상세를 받아 온 뒤 content_hash 를
    비교하면 요청은 이미 나가 있어 절감 효과가 0이다.

    days=0 이면 필터를 끈다(빈 집합) — 전량 재수집.
    """
    if days <= 0:
        return set()
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await session.exec(
            select(JobPosting.source_job_id).where(
                JobPosting.source == source,
                JobPosting.collected_at >= cutoff,
            )
        )
    ).all()
    return {row if isinstance(row, str) else row[0] for row in rows}


async def touch_posting(session: AsyncSession, source: str, source_job_id: str) -> None:
    """내용이 그대로일 때. collected_at 만 갱신하고 재추출은 생략한다."""
    await session.exec(
        JobPosting.__table__.update()
        .where(JobPosting.source == source, JobPosting.source_job_id == source_job_id)
        .values(collected_at=datetime.now(UTC))
    )


def _conflict_update_set(excluded: Any) -> dict[str, Any]:
    """ON CONFLICT DO UPDATE 의 갱신 목록.

    upsert_posting 에서 분리해 둔 이유는 테스트가 이 결과와 service 가 INSERT
    하는 컬럼을 대조하기 위해서다 (tests/test_upsert_columns.py).
    여기 없는 컬럼은 **기존 행에 영원히 반영되지 않는다**.
    """
    set_: dict[str, Any] = {
        "title": excluded.title,
        "company_name_raw": excluded.company_name_raw,
        "tags_raw": excluded.tags_raw,
        "career_min": excluded.career_min,
        "career_max": excluded.career_max,
        "salary_type": excluded.salary_type,
        # 이 둘은 매 파싱의 판정 결과다. COALESCE 하면 한 번 true 가 된 공고가
        # 파서를 고친 뒤에도 영원히 true 로 남는다 — 최신 판정으로 덮어쓴다.
        "body_is_image": excluded.body_is_image,
        "body_extract_failed": excluded.body_extract_failed,
        "content_hash": excluded.content_hash,
        "collected_at": excluded.collected_at,
        "raw_fields": excluded.raw_fields,
    }
    for column in _COALESCE_ON_UPDATE:
        set_[column] = func.coalesce(getattr(excluded, column), getattr(JobPosting, column))
    return set_


async def upsert_posting(session: AsyncSession, values: dict[str, Any]) -> int:
    """(source, source_job_id) 기준 upsert. 공고 id 를 돌려준다."""
    stmt = pg_insert(JobPosting).values(**values)

    stmt = stmt.on_conflict_do_update(
        constraint="job_posting_uk", set_=_conflict_update_set(stmt.excluded)
    ).returning(JobPosting.id)
    posting_id = (await session.exec(stmt)).one()
    return posting_id if isinstance(posting_id, int) else posting_id[0]


# ── 임베딩 ──────────────────────────────────────────────────────────────────
# 임베딩 대상에서 빼는 조건. 세 곳(지정 대상 · 백필 · 카운트)에서 같은 조건을
# 써야 해서 한 곳에 모아 둔다. 조건이 갈라지면 백필이 영원히 같은 행을 집는다.
def _embeddable_posting_clause() -> Any:
    return and_(
        JobPosting.description.is_not(None),
        JobPosting.body_is_image.is_(False),
        JobPosting.body_extract_failed.is_(False),
    )


def _needs_embedding_clause() -> Any:
    """embed_hash 가 content_hash 와 다른 것 = 아직 임베딩이 최신이 아니다."""
    return or_(
        JobPosting.embed_hash.is_(None),
        JobPosting.embed_hash.is_distinct_from(JobPosting.content_hash),
    )


async def iter_postings_to_embed(
    session: AsyncSession,
    *,
    posting_ids: Sequence[int] | None = None,
    limit: int | None = None,
) -> list[tuple[int, str, str]]:
    """임베딩 대상. (posting_id, description, content_hash)

    posting_ids 를 줘도 embed_hash 조건은 그대로 건다. 태스크가 재시도되어
    같은 id 로 다시 들어와도 이미 끝난 공고는 걸러지므로 멱등해진다.
    """
    stmt = (
        select(JobPosting.id, JobPosting.description, JobPosting.content_hash)
        .where(_embeddable_posting_clause(), _needs_embedding_clause())
        .order_by(JobPosting.id)
    )
    if posting_ids is not None:
        if not posting_ids:
            return []
        stmt = stmt.where(JobPosting.id.in_(list(posting_ids)))
    if limit:
        stmt = stmt.limit(limit)

    return [
        (pid, description, content_hash)
        for pid, description, content_hash in (await session.exec(stmt)).all()
        if description and content_hash
    ]


async def count_postings_to_embed(session: AsyncSession) -> int:
    """백필이 남긴 잔량. 완료 확인용."""
    stmt = (
        select(func.count())
        .select_from(JobPosting)
        .where(_embeddable_posting_clause(), _needs_embedding_clause())
    )
    row = (await session.exec(stmt)).one()
    return int(row if isinstance(row, int) else row[0])


async def get_chunk_state(session: AsyncSession, posting_id: int) -> dict[tuple[str, int], str]:
    """이 공고의 기존 청크 상태. (section, seq) → chunk_hash

    벡터가 비어 있는 행은 '해시가 없는 것' 으로 취급해 재임베딩 대상이 된다.
    (이전 실행이 청크만 쓰고 죽은 경우)
    """
    rows = (
        await session.exec(
            select(
                PostingChunk.section,
                PostingChunk.seq,
                PostingChunk.chunk_hash,
                PostingChunk.embedding.is_(None).label("no_vector"),
            ).where(PostingChunk.posting_id == posting_id)
        )
    ).all()
    return {
        (str(section), seq): ("" if no_vector else chunk_hash)
        for section, seq, chunk_hash, no_vector in rows
    }


async def upsert_posting_chunk(
    session: AsyncSession,
    *,
    posting_id: int,
    section: str,
    seq: int,
    content: str,
    chunk_hash: str,
    embedding: Sequence[float],
    token_count: int | None = None,
) -> None:
    """청크 1건. (posting_id, section, seq) 기준 upsert."""
    stmt = pg_insert(PostingChunk).values(
        posting_id=posting_id,
        section=section,
        seq=seq,
        content=content,
        chunk_hash=chunk_hash,
        embedding=list(embedding),
        token_count=token_count,
    )
    await session.exec(
        stmt.on_conflict_do_update(
            constraint="posting_chunk_uk",
            set_={
                "content": stmt.excluded.content,
                "chunk_hash": stmt.excluded.chunk_hash,
                "embedding": stmt.excluded.embedding,
                "token_count": stmt.excluded.token_count,
            },
        )
    )


async def delete_stale_chunks(
    session: AsyncSession, posting_id: int, keep: Sequence[tuple[str, int]]
) -> int:
    """이번 분할 결과에 없는 청크를 지운다.

    본문이 짧아지면 seq 가 줄어든다. 안 지우면 예전 벡터가 검색에 계속
    걸린다 — 원문에는 이미 없는 문장이 근거로 인용되는 사고가 난다.
    """
    stmt = delete(PostingChunk).where(PostingChunk.posting_id == posting_id)
    if keep:
        stmt = stmt.where(
            tuple_(PostingChunk.section, PostingChunk.seq).not_in([(s, q) for s, q in keep])
        )
    return int((await session.exec(stmt)).rowcount or 0)


async def set_posting_embed_hash(session: AsyncSession, posting_id: int, embed_hash: str) -> None:
    """★ 임베딩이 전부 성공한 뒤에만 부른다.

    중간에 실패했는데 갱신하면 그 공고는 영원히 백필 대상에서 빠진다.
    """
    await session.exec(
        JobPosting.__table__.update()
        .where(JobPosting.id == posting_id)
        .values(embed_hash=embed_hash)
    )


async def iter_companies_to_embed(
    session: AsyncSession,
    *,
    company_ids: Sequence[int] | None = None,
    limit: int | None = None,
) -> list[tuple[int, str | None, str | None, str | None, str | None]]:
    """기업 프로필 임베딩 대상.

    (id, description, business_content, industry, embed_hash)
    세 필드가 전부 비어 있는 기업은 SQL 단계에서 제외한다 — 합쳐도 빈
    문자열이라 임베딩할 게 없다.
    """
    stmt = (
        select(
            Company.id,
            Company.description,
            Company.business_content,
            Company.industry,
            Company.embed_hash,
        )
        .where(
            or_(
                Company.description.is_not(None),
                Company.business_content.is_not(None),
                Company.industry.is_not(None),
            )
        )
        .order_by(Company.id)
    )
    if company_ids is not None:
        if not company_ids:
            return []
        stmt = stmt.where(Company.id.in_(list(company_ids)))
    if limit:
        stmt = stmt.limit(limit)
    return list((await session.exec(stmt)).all())


async def set_company_embedding(
    session: AsyncSession,
    company_id: int,
    *,
    embedding: Sequence[float],
    embed_hash: str,
) -> None:
    await session.exec(
        Company.__table__.update()
        .where(Company.id == company_id)
        .values(profile_embedding=list(embedding), embed_hash=embed_hash)
    )


async def count_chunks_by_section(session: AsyncSession) -> list[tuple[str, int]]:
    """검증 쿼리용. SELECT section, COUNT(*) ... GROUP BY 1 과 같다."""
    rows = (
        await session.exec(
            select(PostingChunk.section, func.count())
            .group_by(PostingChunk.section)
            .order_by(PostingChunk.section)
        )
    ).all()
    return [(str(section), int(count)) for section, count in rows]


# ── 실행 이력 ───────────────────────────────────────────────────────────────
async def start_run(
    session: AsyncSession,
    *,
    kind: enums.RunKind,
    source: str | None = None,
    keyword: str | None = None,
) -> int:
    run = CrawlRun(kind=kind, source=source, keyword=keyword, status=enums.RunStatus.RUNNING)
    session.add(run)
    await session.flush()
    return run.id


async def fail_stale_runs(session: AsyncSession, *, older_than_seconds: int) -> int:
    """죽은 태스크가 남긴 running 행을 failed 로 마감한다. 마감한 건수를 돌려준다.

    워커가 SIGKILL 되거나 컨테이너가 재시작되면 finish_run 이 불리지 못해
    crawl_run 이 영원히 running 으로 남는다. 그러면 "어제 수집이 아직 도는
    중인가?" 를 이력만 보고 판단할 수 없다.

    ★ 기준을 job_timeout 보다 길게 잡는다. 그 시간을 넘겨 살아 있는 태스크는
      존재할 수 없으므로(arq 가 먼저 죽인다), 워커가 여러 개여도 남의 실행을
      잘못 마감할 위험이 없다.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    result = await session.exec(
        CrawlRun.__table__.update()
        .where(
            CrawlRun.status == enums.RunStatus.RUNNING.value,
            CrawlRun.started_at < cutoff,
        )
        .values(
            status=enums.RunStatus.FAILED.value,
            finished_at=datetime.now(UTC),
            message="워커가 중단되어 마감되지 못한 실행 (기동 시 정리)",
        )
    )
    return int(result.rowcount or 0)


async def finish_run(
    session: AsyncSession,
    run_id: int,
    *,
    status: enums.RunStatus,
    fetched: int = 0,
    inserted: int = 0,
    updated: int = 0,
    skipped: int = 0,
    embedded: int = 0,
    errors: int = 0,
    message: str | None = None,
) -> None:
    await session.exec(
        CrawlRun.__table__.update()
        .where(CrawlRun.id == run_id)
        .values(
            status=status.value,
            fetched=fetched,
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            embedded=embedded,
            errors=errors,
            message=message,
            finished_at=datetime.now(UTC),
        )
    )
