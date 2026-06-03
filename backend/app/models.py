from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class BirthDetails(BaseModel):
    name: str | None = None
    date: str = Field(..., description="YYYY-MM-DD")
    time: str = Field(..., description="HH:MM, local birth time")
    place: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    message: str
    birth_details: BirthDetails | None = None
    conversation_id: str | None = None
    history: list[ChatMessage] = Field(default_factory=list)


class ToolActivity(BaseModel):
    name: str
    status: Literal["started", "completed", "failed"]
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = None


class AgentEvent(BaseModel):
    type: Literal["token", "tool", "error", "done"]
    content: str | None = None
    tool: ToolActivity | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

