# alembic 실행 설정

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings
from app.core.database import metadata

# import 해야 metadata 에 테이블이 등록된다
from app.domains.chat import models as chat_models  # noqa: F401
from app.domains.market import models as market_models  # noqa: F401

MANAGED_SCHEMAS = {"market", "chat"}

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = metadata

VERSION_TABLE_SCHEMA = "public"


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    schema = getattr(obj, "schema", None)
    if type_ == "table":
        if schema not in MANAGED_SCHEMAS:
            return False
        # checkpoint 테이블은 LangGraph 가 직접 만든다
        if name.startswith("checkpoint"):
            return False
    return True


def render_item(type_, obj, autogen_context):
    module = obj.__class__.__module__ if obj is not None else ""
    if type_ == "type":
        if module.startswith("pgvector"):
            autogen_context.imports.add("import pgvector.sqlalchemy")
        elif module.startswith("sqlmodel"):
            autogen_context.imports.add("import sqlmodel")
    return False


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


# 동기 엔진으로 돈다. psycopg async 는 Windows 기본 이벤트 루프에서 동작하지 않는다
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
