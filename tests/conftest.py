"""공용 픽스처.

settings_override   .env 무시하고 테스트 설정 주입
db                  테스트 DB 세션 (트랜잭션 롤백 격리)
                    pgvector 가 필요하므로 sqlite 로 대체할 수 없다.
                    docker-compose 의 postgres 에 test 스키마를 쓰거나
                    일회용 컨테이너를 띄운다
fake_llm            app.llm.fake.FakeLLM
fake_embedder       app.llm.fake.FakeEmbedder (해시 기반 결정적 벡터)
client              httpx AsyncClient + FastAPI app (의존성 오버라이드)
site_snapshot       크롤러 테스트용 저장된 HTML/JSON 원본
"""
