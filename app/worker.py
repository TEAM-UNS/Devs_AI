"""arq WorkerSettings · cron 정의.

    uv run arq app.worker.WorkerSettings

등록 태스크 (crawler/tasks.py 에 구현)
    crawl_dispatch    04:00  사이트×키워드 팬아웃
    crawl_site        (팬아웃 대상)
    embed_postings    (crawl_site 가 enqueue)
    embed_backfill    05:30
    embed_companies   06:00

설정
    max_jobs=4        임베딩 API 동시 호출 제한 고려
    job_timeout=600
    중복 방지         _job_id = f"crawl:{site}:{keyword}:{date}"

★ Windows: 이 모듈 최상단에서 WindowsSelectorEventLoopPolicy 를 설정해야
  psycopg async 가 동작한다. (README 의 "Windows 개발 환경 주의")
"""
