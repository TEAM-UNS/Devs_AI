"""RawJob · RawCompany — 사이트 어댑터가 뱉는 정규화 이전 형태.

RawJob
    source · source_job_id · url · title · company_name
    career_raw · employment_type · education · location
    description_html · welfare · tags[] · salary_raw
    posted_at · expires_at · has_image · raw(원본 dict)

RawCompany
    source · source_company_id · name · url
    employee_count_raw · company_type · founded · revenue_raw
    industry · description · business_content · talent_profile

사이트별 필드 차이는 전부 여기서 흡수한다.
market 의 DTO(CompanyIn · JobPostingIn) 변환은 service.py 책임.
"""
