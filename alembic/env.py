"""alembic 실행 컨텍스트.

- 접속 URL 은 app.core.config 의 DATABASE_URL 을 그대로 쓴다.
- market / chat 두 스키마를 모두 비교 대상에 넣는다(include_schemas=True).
- pgvector 컬럼은 autogenerate 시 `import pgvector.sqlalchemy` 가 필요하므로
  render_item 으로 import 문을 강제한다.

★ 마이그레이션은 **동기 엔진**으로 돌린다.
  psycopg 는 Windows 기본 이벤트 루프(ProactorEventLoop)에서 async 모드를
  쓸 수 없다. 마이그레이션은 비동기일 이유가 없으므로 URL 의 +psycopg 를
  그대로 동기 드라이버로 사용한다. (런타임 앱은 README 의 Windows 항목 참고)

주의: 모델(app/domains/*/models.py)이 아직 비어 있으면 autogenerate 결과도
비어 있다. 스키마 원본은 현재 init.sql 이다.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings
from app.core.database import metadata

# 모델 모듈을 import 해야 SQLModel.metadata 에 테이블이 등록된다.
from app.domains.chat import models as chat_models  # noqa: F401
from app.domains.market import models as market_models  # noqa: F401

# 관리 대상 스키마. 여기 없는 스키마의 객체는 비교에서 통째로 제외한다.
MANAGED_SCHEMAS = {"market", "chat"}

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = metadata

# alembic_version 테이블 위치. 스키마를 나눴으므로 public 에 고정한다.
VERSION_TABLE_SCHEMA = "public"


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """관리 대상 밖 객체를 비교에서 제외.

    - public 등 다른 스키마 (pgvector · 확장 · alembic_version)
    - LangGraph checkpointer 가 chat 스키마에 직접 만드는 테이블
    """
    schema = getattr(obj, "schema", None)
    if type_ == "table":
        if schema not in MANAGED_SCHEMAS:
            return False
        if name.startswith("checkpoint"):
            return False
    return True


def render_item(type_, obj, autogen_context):
    """마이그레이션 파일에 필요한 import 를 자동으로 넣는다.

    vector  → import pgvector.sqlalchemy
    AutoString 등 SQLModel 타입 → import sqlmodel
    """
    module = obj.__class__.__module__ if obj is not None else ""
    if type_ == "type":
        if module.startswith("pgvector"):
            autogen_context.imports.add("import pgvector.sqlalchemy")
        elif module.startswith("sqlmodel"):
            autogen_context.imports.add("import sqlmodel")
    return False  # 기본 렌더러에 위임


def _configure(connection=None, url=None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        render_item=render_item,
        compare_type=True,
        compare_server_default=True,
        version_table_schema=VERSION_TABLE_SCHEMA,
        literal_binds=url is not None,
        dialect_opts={"paramstyle": "named"},
    )


def run_migrations_offline() -> None:
    _configure(url=config.get_main_option("sqlalchemy.url"))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
