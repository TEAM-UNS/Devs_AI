"""LLMPort 구현 — 대화 모델 어댑터 (Anthropic).

책임
    - 메시지/툴 스키마를 벤더 포맷으로 변환
    - 스트리밍 원본 이벤트 → port.py 의 공통 LLMEvent 로 번역
    - 429 · 5xx · 연결 오류를 UpstreamError 로 정규화 (재시도 포함)
    - usage(입출력 토큰) 집계 반환 → 레이트리밋/토큰 예산에 사용

anthropic SDK 를 import 하는 곳은 이 파일뿐이다.
모델 기본값은 settings.LLM_MODEL (claude-opus-5).
"""
