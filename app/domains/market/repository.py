"""쓰기 담당 — 크롤러/임베딩 전용. (챗봇은 접근 금지, R3)

전부 postgres 의 INSERT ... ON CONFLICT 를 쓴다.
"먼저 SELECT 해서 있으면 UPDATE" 는 동시 실행 시 경합이 나므로 쓰지 않는다.

부분 갱신 규칙
    상세 수집에 실패하면 description 같은 필드가 None 으로 온다. 그때
    기존 값을 지우면 안 되므로 COALESCE(신규, 기존) 으로 덮어쓴다.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.market import enums
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

# ★ INSERT 에 넣는 컬럼은 여기나 upsert_posting 의 set_ 둘 중 하나에 반드시
_COALESCE_ON_UPDATE = (
    "description",
    "welfare",
    "salary_raw",
    "salary_min",
    "salary_max",
    "location",
    "education",
    "employment_type",
    "salary_period",
    "posted_at",
    "expires_at",
    "company_id",
    "field_id",
)


# ── 분류 ────────────────────────────────────────────────────────────────────
async def get_field_ids(session: AsyncSession) -> dict[str, int]:
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
    unique = sorted({n.strip() for n in names if n and n.strip()})
    if not unique:
        return {}

    await session.exec(
        pg_insert(Skill)
        .values([{"name": n} for n in unique])
        .on_conflict_do_nothing(index_elements=[Skill.name])
    )
    rows = (await session.exec(select(Skill.name, Skill.id).where(Skill.name.in_(unique)))).all()
    return {name: sid for name, sid in rows}


async def load_skill_entries(session: AsyncSession) -> list[SkillDictionaryRow]:
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


# 본문 스캔에서 걸러낼 일반 단어. 기술명이 아니라 영어 산문·직무 용어다.
# 완전하지 않아도 된다 — 사람이 읽는 후보 목록이라 상위권만 조용해지면 쓸 만하다.
_BODY_STOPWORDS = frozenset(
    """
    and the with for you our are can will not that this from have has your who its all any
    data code web app apps api apis rest system systems service services server servers
    software hardware platform platforms framework frameworks library libraries tool tools
    engineer engineers engineering developer developers development develop team teams
    experience experienced skill skills work working job jobs role roles position
    company business product products project projects solution solutions
    design designing architecture architectures management manage manager
    support technical technology technologies application applications environment
    process processes performance quality test testing analysis research
    new using use used based level high low more most other than time year years
    http https www com net org github google amazon microsoft apple
    """.split()
)


async def count_unmatched_body_terms(
    session: AsyncSession, *, source: str | None = None, min_count: int = 10, limit: int = 200
) -> list[UnmatchedTag]:
    """공고 본문에서 사전에 없는데 자주 나오는 기술 후보를 뽑는다.

    ★ count_unmatched_tags 는 tags_raw 만 본다. 그런데 최근에 뜬 기술일수록
      사이트 태그 목록에는 아직 없고 본문에만 적힌다. 실제로 RAG(611건) ·
      pgvector(23건) 같은 벡터 스택이 통째로 누락돼 있었는데, 태그 리포트에는
      한 건도 안 떴다. 본문을 봐야 그 구멍이 보인다.

    태그와 달리 정답이 깔끔하지 않다. 영문 토큰만 보고 불용어를 걸러도 노이즈가
    남는다. 사람이 읽고 판단하는 후보 목록일 뿐 자동 등재용이 아니다.
    """
    where_source = "AND p.source = :source" if source else ""
    sql = text(
        f"""
        WITH words AS (
            SELECT lower(m[1]) AS term
            FROM market.job_posting p,
                 LATERAL regexp_matches(
                     p.description,
                     -- 영문/숫자로 시작하고 . + # / - 를 포함할 수 있는 토큰.
                     -- Node.js · C++ · CI/CD · GPT-4 같은 표기를 살린다.
                     '[A-Za-z][A-Za-z0-9]*(?:[.+#/-][A-Za-z0-9]+)*',
                     'g'
                 ) AS m
            WHERE p.description IS NOT NULL
              AND p.body_is_image IS FALSE
              {where_source}
        ),
        filtered AS (
            SELECT term FROM words
            WHERE length(term) BETWEEN 3 AND 30
              AND term NOT IN (SELECT lower(alias) FROM market.skill_alias)
              AND term NOT IN (SELECT lower(name) FROM market.skill)
              AND term !~ '^[0-9]'
              AND term <> ALL(:stopwords)
        )
        SELECT term, COUNT(*) AS cnt
        FROM filtered
        GROUP BY term
        HAVING COUNT(*) >= :min_count
        ORDER BY cnt DESC
        LIMIT :limit
        """
    )
    params: dict[str, Any] = {
        "min_count": min_count,
        "limit": limit,
        "stopwords": list(_BODY_STOPWORDS),
    }
    if source:
        params["source"] = source
    rows = (await session.exec(sql.bindparams(**params))).all()
    return [UnmatchedTag(tag=term, count=cnt) for term, cnt in rows]


async def upsert_tech_fields(
    session: AsyncSession, rows: Sequence[tuple[str, str, int]]
) -> dict[str, int]:
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
) -> list[tuple[int, str | None, list[str], dict[str, Any], int | None, str]]:
    stmt = select(
        JobPosting.id,
        JobPosting.description,
        JobPosting.tags_raw,
        JobPosting.raw_fields,
        JobPosting.field_id,
        # 분야 분류의 보조 신호. 카테고리가 비었거나 동점일 때 제목으로 가른다.
        JobPosting.title,
    )
    if source:
        stmt = stmt.where(JobPosting.source == source)
    stmt = stmt.order_by(JobPosting.id)
    if limit:
        stmt = stmt.limit(limit)
    return [
        (pid, desc, tags or [], raw or {}, field_id, title or "")
        for pid, desc, tags, raw, field_id, title in (await session.exec(stmt)).all()
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
    result = await session.exec(
        JobPosting.__table__.update()
        .where(JobPosting.id == posting_id, JobPosting.body_is_image.is_(False))
        .values(body_extract_failed=True)
    )
    return bool(result.rowcount)


async def update_posting_field(
    session: AsyncSession, posting_id: int, field_id: int | None
) -> None:
    await session.exec(
        JobPosting.__table__.update().where(JobPosting.id == posting_id).values(field_id=field_id)
    )


async def replace_posting_skills(
    session: AsyncSession,
    posting_id: int,
    rows: Sequence[tuple[int, enums.Requirement, int]],
) -> None:
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
    await session.exec(
        JobPosting.__table__.update()
        .where(JobPosting.source == source, JobPosting.source_job_id == source_job_id)
        .values(collected_at=datetime.now(UTC))
    )


def _conflict_update_set(excluded: Any) -> dict[str, Any]:
    set_: dict[str, Any] = {
        "title": excluded.title,
        "company_name_raw": excluded.company_name_raw,
        "tags_raw": excluded.tags_raw,
        "career_min": excluded.career_min,
        "career_max": excluded.career_max,
        "salary_type": excluded.salary_type,
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
    stmt = pg_insert(JobPosting).values(**values)

    stmt = stmt.on_conflict_do_update(
        constraint="job_posting_uk", set_=_conflict_update_set(stmt.excluded)
    ).returning(JobPosting.id)
    posting_id = (await session.exec(stmt)).one()
    return posting_id if isinstance(posting_id, int) else posting_id[0]


# ── 임베딩 ──────────────────────────────────────────────────────────────────
def _embeddable_posting_clause() -> Any:
    return and_(
        JobPosting.description.is_not(None),
        JobPosting.body_is_image.is_(False),
        JobPosting.body_extract_failed.is_(False),
    )


def _needs_embedding_clause() -> Any:
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
    stmt = (
        select(func.count())
        .select_from(JobPosting)
        .where(_embeddable_posting_clause(), _needs_embedding_clause())
    )
    row = (await session.exec(stmt)).one()
    return int(row if isinstance(row, int) else row[0])


async def get_chunk_state(session: AsyncSession, posting_id: int) -> dict[tuple[str, int], str]:
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
    stmt = delete(PostingChunk).where(PostingChunk.posting_id == posting_id)
    if keep:
        stmt = stmt.where(
            tuple_(PostingChunk.section, PostingChunk.seq).not_in([(s, q) for s, q in keep])
        )
    return int((await session.exec(stmt)).rowcount or 0)


async def set_posting_embed_hash(session: AsyncSession, posting_id: int, embed_hash: str) -> None:
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
