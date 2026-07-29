"""본문 → 섹션 청크 분할 (임베딩 단위).

    responsibility   주요업무
    required         자격요건
    preferred        우대사항

규칙
    - 복지 · 전형절차 · 회사소개 상용구는 제외 (검색 노이즈)
    - 섹션 헤더를 못 찾으면 전체를 responsibility 단일 청크로
    - 섹션이 길면 seq 를 늘려 분할. 토큰 상한은 임베딩 모델 기준
    - 각 청크마다 chunk_hash(정규화 텍스트 sha256) 계산 → 변경분만 재임베딩
    - body_is_image=true 또는 description IS NULL 인 공고는 대상 아님
"""
