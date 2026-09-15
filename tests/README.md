# tests

```
tests/
├── conftest.py   fake_embedder · db 픽스처 (postgres 에 못 붙으면 skip)
├── fixtures/     사이트 HTML 스냅샷 (사람인 · 잡코리아)
└── test_*.py     사이트 파서 · 본문 추출 · 스택 추출 · 청크 분할 · 증분 수집
                  · 임베딩 파이프라인 · 임베딩 어댑터 · 태스크 · CORS
```

## 원칙

- 외부 API 호출 금지. 임베딩은 `FakeEmbedder`(`app/infra/embedding/adapters/fake.py`),
  사이트는 저장된 스냅샷으로.
- 크롤러 테스트는 **네트워크를 타지 않는다**. 셀렉터가 깨졌는지는
  스냅샷 갱신 시점에만 확인한다.
- DB 테스트는 실제 postgres+pgvector 를 쓴다. 벡터 연산자(`<=>`)와
  부분 인덱스는 sqlite 로 대체 검증이 불가능하다.
