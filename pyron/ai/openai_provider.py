from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

from pyron.ai.base import BaseAIProvider, CompletionRequest, CompletionResponse
from pyron.api.errors import ProviderError
from pyron.config import load_env_file

load_env_file()

# USD per million tokens. Unknown models fall back to the mini/flash class price.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
}


def _pricing_for(model: str) -> tuple[float, float]:
    if model in _MODEL_PRICING:
        return _MODEL_PRICING[model]
    for prefix, price in _MODEL_PRICING.items():
        if model.startswith(prefix):
            return price
    return (0.15, 0.60)


class OpenAIProvider(BaseAIProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or ""
        self.base_url = (
            base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        ).rstrip("/")
        self.model = model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        self.cost_per_mtok_input, self.cost_per_mtok_output = _pricing_for(self.model)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        is_local = "localhost" in self.base_url or "127.0.0.1" in self.base_url
        if not self.api_key and not is_local:
            raise ProviderError(
                "OpenAI API key is required. Set OPENAI_API_KEY in .env or pass --api-key."
            )

        url = f"{self.base_url}/chat/completions"
        messages: list[dict[str, str]] = []
        if req.system_instruction:
            messages.append({"role": "system", "content": req.system_instruction})
        messages.append({"role": "user", "content": req.prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": req.temperature,
        }
        if req.max_tokens:
            payload["max_tokens"] = req.max_tokens
        if req.response_json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": req.response_json_schema,
                },
            }
        elif req.json_mode:
            # `json_object` is supported by OpenAI and by OpenAI-compatible
            # servers (Ollama, vLLM, Groq, OpenRouter), unlike strict schemas.
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        def _send_request() -> CompletionResponse:
            try:
                request_data = json.dumps(payload).encode("utf-8")
                request = urllib.request.Request(
                    url, data=request_data, headers=headers, method="POST"
                )
                with urllib.request.urlopen(request, timeout=90) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise ProviderError(
                    f"OpenAI API HTTP error {exc.code}: {detail}",
                    details={"code": exc.code},
                ) from exc
            except urllib.error.URLError as exc:
                raise ProviderError(f"OpenAI network error: {exc.reason}") from exc
            except json.JSONDecodeError as exc:
                raise ProviderError(f"OpenAI returned non-JSON response: {exc}") from exc

            choices = body.get("choices") or []
            if not choices:
                raise ProviderError("OpenAI returned no choices.")
            content = (choices[0].get("message") or {}).get("content") or ""
            if not content:
                raise ProviderError("OpenAI returned an empty message.")
            usage = body.get("usage", {})
            return CompletionResponse(
                text=content,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                raw_response=body,
            )

        return await asyncio.to_thread(_send_request)
