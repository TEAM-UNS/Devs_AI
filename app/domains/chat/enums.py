# chat enum

from enum import StrEnum


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


def sql_in(column: str, enum_cls: type[StrEnum]) -> str:
    return f"{column} IN (" + ", ".join(f"'{m.value}'" for m in enum_cls) + ")"
