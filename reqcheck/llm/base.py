"""Интерфейс LLM-клиента: запрос со схемой ответа → результат с валидированными данными."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class LLMRequest:
    step: str  # "extract", "review#3", "lens:consistency" …
    system: str
    user: str
    schema: type[BaseModel]
    max_output_tokens: int = 16384
    meta: dict = field(default_factory=dict, compare=False)  # только для FakeProvider, в кэш-ключ не входит

    def with_user(self, user: str) -> "LLMRequest":
        return replace(self, user=user)


@dataclass
class LLMResult:
    raw: str
    data: BaseModel | None = None
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thinking: int = 0
    latency_s: float = 0.0
    attempts: int = 1
    cache_hit: bool = False
    finish_reason: str = ""


class LLMClient(Protocol):
    name: str
    model: str

    def generate(self, req: LLMRequest) -> LLMResult: ...
