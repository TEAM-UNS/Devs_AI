"""JWT 디코드 · 검증.

AUTH_MODE
    dev   검증 없음. X-User-Id 헤더 값을 그대로 user_id 로 사용
    jwt   RS256 공개키(또는 JWKS)로 서명 검증 후 sub 클레임 사용

검증 항목: 서명 · exp · iss · aud
실패 시 core.exceptions 의 인증 예외로 변환한다(여기서 HTTP 를 알 필요 없음).

메인 백엔드와 합의 필요: 알고리즘(RS256 가정), sub 클레임 타입(문자열 가정),
공개키 전달 방법(PEM 직접 주입 vs JWKS 엔드포인트).
"""
