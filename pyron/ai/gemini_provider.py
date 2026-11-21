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


class GeminiProvider(BaseAIProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or ""
        )
        self.model = model or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
        self.cost_per_mtok_input = 0.30
        self.cost_per_mtok_output = 2.50

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        if not self.api_key:
            raise ProviderError(
                "Gemini API key is required. Set GEMINI_API_KEY in .env or pass --api-key."
            )

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": req.prompt}]}],
            "generationConfig": {"temperature": req.temperature},
        }
        if req.max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = req.max_tokens
        if req.system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": req.system_instruction}]}
        if req.response_json_schema:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            payload["generationConfig"]["responseSchema"] = req.response_json_schema
        elif req.json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        # The key travels in a header, never in the URL query string.
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
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
                    f"Gemini API HTTP error {exc.code}: {detail}",
                    details={"code": exc.code},
                ) from exc
            except urllib.error.URLError as exc:
                raise ProviderError(f"Gemini network error: {exc.reason}") from exc
            except json.JSONDecodeError as exc:
                raise ProviderError(f"Gemini returned non-JSON response: {exc}") from exc

            candidates = body.get("candidates") or [{}]
            candidate = candidates[0]
            parts = (candidate.get("content") or {}).get("parts") or [{}]
            text = parts[0].get("text", "")
            if not text:
                reason = candidate.get("finishReason") or "empty response"
                raise ProviderError(
                    f"Gemini returned no text ({reason}); the prompt may have been blocked."
                )
            usage = body.get("usageMetadata", {})
            return CompletionResponse(
                text=text,
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0),
                raw_response=body,
            )

        return await asyncio.to_thread(_send_request)
