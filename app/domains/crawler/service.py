"""수집 오케스트레이션: sites → extractor/chunker → market.repository (R2).

흐름
    1. 사이트 어댑터로 목록 수집 → 상세 수집
    2. 기업 정규화(name_key) → upsert_company + company_source
    3. content_hash 비교
         동일  → collected_at 만 갱신하고 재추출 생략 (skipped++)
         변경  → extractor 로 스킬 추출 → upsert_posting + replace_posting_skills
    4. 연봉 파싱 (salary_raw → min/max/type)
    5. 이미지 공고 판별 (본문 200자 미만 + 이미지 존재 → body_is_image=true)
    6. 변경분 posting_id 를 반환 → tasks 가 embed_postings 를 enqueue

예외
    - 셀렉터 미스: 경고 로그 후 해당 사이트만 중단, 다른 사이트는 계속
    - 상세 3중 폴백(JSON-LD → 라벨-값 → CSS) 전부 실패: 목록 데이터만 저장,
      description=null
    - 기업 페이지 없음: 공고에서 얻은 정보만 사용
"""
