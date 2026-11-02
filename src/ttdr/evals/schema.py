from typing import Literal

from pydantic import BaseModel

Grade = Literal["correct", "incorrect", "not_attempted"]


class Evidence(BaseModel):
    doc: str
    page: int | None = None


class Item(BaseModel):
    id: str
    suite: str
    question: str
    answer: str
    tags: dict[str, str] = {}
    evidence: list[Evidence] = []


class Answer(BaseModel):
    id: str
    text: str
    latency_s: float
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    error: str | None = None
    retrieved: list[str] = []
    cited: list[str] = []
    stats: dict[str, int] = {}
    """Harness counters: LLM and tool calls by name, tokens."""


class Graded(BaseModel):
    id: str
    grade: Grade
    method: Literal["exact", "numeric", "abstain", "judge", "error"]
    judge_reason: str | None = None
