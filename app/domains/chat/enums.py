from enum import StrEnum


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class StreamEvent(StrEnum):
    TITLE = "title"
    SESSION = "session"
    TOOL_START = "tool_start"
    GRAPH = "graph"
    TOKEN = "token"
    DONE = "done"
    ERROR = "error"


class Period(StrEnum):
    WEEK = "week"
    PREVIOUS_WEEK = "previous_week"
    MONTH = "month"
    PREVIOUS_MONTH = "previous_month"