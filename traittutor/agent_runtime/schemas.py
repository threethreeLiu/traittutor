from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, Field


class AgentMode(StrEnum):
    LEARN = "learn"
    ASSIST = "assist"


class Intent(StrEnum):
    LEARNING = "learning"
    RESEARCH = "research"
    WRITING = "writing"
    PLANNING = "planning"
    FILE_TASK = "file_task"
    EXECUTION = "execution"
    GENERAL = "general"


class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    mode: AgentMode
    session_id: str | None = None
    user_id: str | None = None
    materials: list[str] = Field(default_factory=list)
    profile_id: str | None = None
    # UI language is a reply-language preference, not merely a display hint.
    language: str = "en"


class ToolPolicyDecision(BaseModel):
    action: str
    decision: str
    reason: str


class AgentRunResult(BaseModel):
    run_id: str
    intent: Intent
    agent: str
    content: str
    gateway_request_id: str
    policy: list[ToolPolicyDecision] = Field(default_factory=list)
