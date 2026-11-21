from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class CompletionRequest(BaseModel):
    prompt: str
    system_instruction: str | None = None
    temperature: float = 0.2
    max_tokens: int | None = 4000
    # Ask the provider for JSON-only output. Cheaper and more widely supported
    # than a strict response schema, especially on OpenAI-compatible endpoints.
    json_mode: bool = False
    response_json_schema: dict[str, Any] | None = None


class CompletionResponse(BaseModel):
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw_response: Any = None


class BaseAIProvider(ABC):
    name: str = "base"
    model: str = ""
    # USD per million tokens, used for cost accounting.
    cost_per_mtok_input: float = 0.15
    cost_per_mtok_output: float = 0.60

    @abstractmethod
    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        ...

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.cost_per_mtok_input / 1_000_000
            + output_tokens * self.cost_per_mtok_output / 1_000_000
        )
