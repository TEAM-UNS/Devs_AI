"""사이트 어댑터 공통 골격 — 딜레이 · 재시도 · 감속 · 스냅샷.

인터페이스
    fetch_list(keyword, page) -> AsyncIterator[RawJob]
    fetch_detail(url)        -> RawJob
    fetch_company(url)       -> RawCompany | None

공통 기능
    딜레이    요청 간 CRAWL_DELAY_SECONDS + 지터
    감속      403/429 발생 시 delay ×1.6 (최대 20초). 성공이 이어지면 서서히 복구
    재시도    지수 백오프, 최대 CRAWL_MAX_RETRY(3)회.
              소진 시 해당 페이지를 건너뛰고 다음으로 진행
    스냅샷    파싱 실패 시 원본 HTML/JSON 을 CRAWL_SNAPSHOT_DIR 에 저장
              (셀렉터 깨짐을 사후에 재현하기 위함)
    UA·헤더   CRAWL_USER_AGENT

셀렉터 미스는 예외를 올리되, service 가 "해당 사이트만 중단" 으로 처리한다.
"""
