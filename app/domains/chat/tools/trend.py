"""트렌드 툴 — 인기 · 급상승 · 세그먼트별 · 연봉 통계. → market.queries

get_popular_skills(field?, size_type?, career_level?, days=30, top=20)
    반환 스킬명 · 공고수 · 점유율 · 순위        차트 bar

get_rising_skills(field?, min_count=5, top=10)
    2주 구간 비교. 증감률 = (this+1)/(last+1)-1 스무딩
    min_count 미만 스킬 제외                    차트 bar

get_stacks_by_segment(group_by[size|career|location], field?, top=10)
    세그먼트별 상위 스킬                        차트 grouped bar

get_salary_stats(field?, career_level?, size_type?, company_id?, skill?)
    median · q1 · q3 · min · max · unit(만원)
    sample_size · total_postings · disclosure_rate
    breakdown{negotiable · range · min_only · unknown}
    sample_size < 10 이면 low_confidence=true
    집계 대상은 salary_type != negotiable, 분모는 전체 공고수  차트 box|bar
"""
