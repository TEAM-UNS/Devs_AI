"""★ arq 태스크 — worker.py 의 WorkerSettings.functions 에 등록된다.

    crawl_dispatch()            04:00 cron. 사이트×키워드 조합으로 팬아웃
                                _job_id = f"crawl:{site}:{keyword}:{date}" 로 중복 차단
    crawl_site(site, keyword)   수집 → upsert → 변경분 embed_postings enqueue
                                재시도 3
    embed_postings(ids)         청크 분할 → 배치 임베딩 → upsert. 재시도 3
    embed_backfill()            05:30. 누락·실패분 최대 500건. 재시도 2
    embed_companies()           06:00. 기업 설명 변경분. 재시도 2

공통
    - 시작 시 crawl_run(status=running) 기록, 종료 시 success/partial/failed 마감
    - 태스크가 죽어도 crawl_run 이 running 으로 남지 않게 on_job_end 에서 정리
    - job_timeout=600, max_jobs=4
"""
