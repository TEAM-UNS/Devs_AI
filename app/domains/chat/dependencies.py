"""chat 의존성 — 어댑터 선택 · 서비스 조립.

    LLMDep         USE_FAKE_LLM 이면 fake.FakeLLM, 아니면 chat_adapter
    EmbedderDep    질의 임베딩용 (search_* 툴)
    ChatServiceDep repository + graph + llm 조립

여기가 유일한 어댑터 선택 지점이다. 도메인 코드는 port 만 본다.
"""
