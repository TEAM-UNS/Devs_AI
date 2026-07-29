"""market DTO — ORM 엔티티가 도메인 밖으로 새지 않게 하는 경계.

쓰기 입력 (crawler → repository)
    CompanyIn · JobPostingIn · PostingSkillIn · ChunkIn

읽기 출력 (queries → chat tools)
    SkillCount · SkillRising · SegmentStacks
    SalaryStats        median · q1 · q3 · min · max · sample_size
                       total_postings · disclosure_rate · breakdown · low_confidence
    SkillRelation      동반 스킬 · 동시출현수 · NPMI
    SkillDemand        분야별 · 규모별 · 경력별 분포
    CompanyProfile · CompanySummary · CompanySimilarity · CompanyComparison
    PostingHit         제목 · 회사 · 매칭 청크 · URL · 유사도
    SkillGapItem · DataCoverage

수치 필드는 반올림하지 않은 원본을 담는다. 표현은 차트/LLM 쪽 책임.
"""
