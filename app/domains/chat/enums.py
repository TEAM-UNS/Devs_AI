from enum import StrEnum


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class StreamEvent(StrEnum):
    SESSION = "session"
    TOOL_START = "tool_start"
    GRAPH = "graph"
    TOKEN = "token"
    DONE = "done"
    ERROR = "error"