from __future__ import annotations

import asyncio
import json

import pytest

from pyron.ai.base import CompletionRequest
from pyron.ai.gemini_provider import GeminiProvider
from pyron.ai.openai_provider import OpenAIProvider
from pyron.api.errors import ProviderError


class DummyResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def capture(monkeypatch, response_payload: dict) -> dict:
    captured: dict = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in dict(request.header_items()).items()}
        captured["body"] = json.loads(request.data)
        return DummyResponse(response_payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


def test_openai_sends_json_object_mode_and_never_the_key_in_url(monkeypatch):
    captured = capture(
        monkeypatch,
        {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}},
    )
    provider = OpenAIProvider(api_key="secret-key", model="gpt-4o-mini")
    response = asyncio.run(provider.complete(CompletionRequest(prompt="hi", json_mode=True)))

    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["headers"]["authorization"] == "Bearer secret-key"
    assert "secret-key" not in captured["url"]
    assert response.input_tokens == 3 and response.output_tokens == 4


def test_openai_requires_key_for_remote_endpoints(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAIProvider(api_key="", base_url="https://api.openai.com/v1")
    with pytest.raises(ProviderError):
        asyncio.run(provider.complete(CompletionRequest(prompt="hi")))


def test_gemini_uses_header_auth_and_json_mode(monkeypatch):
    captured = capture(
        monkeypatch,
        {
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 6},
        },
    )
    provider = GeminiProvider(api_key="gemini-key")
    response = asyncio.run(provider.complete(CompletionRequest(prompt="hi", json_mode=True)))

    assert captured["headers"]["x-goog-api-key"] == "gemini-key"
    assert "gemini-key" not in captured["url"]
    assert captured["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert response.input_tokens == 5 and response.output_tokens == 6


def test_gemini_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    provider = GeminiProvider(api_key="")
    with pytest.raises(ProviderError):
        asyncio.run(provider.complete(CompletionRequest(prompt="hi")))


def test_cost_uses_per_model_pricing():
    mini = OpenAIProvider(api_key="x", model="gpt-4o-mini")
    full = OpenAIProvider(api_key="x", model="gpt-4o")
    assert mini.estimate_cost(1_000_000, 0) == pytest.approx(0.15)
    assert full.estimate_cost(0, 1_000_000) == pytest.approx(10.0)
    assert GeminiProvider(api_key="x").estimate_cost(1_000_000, 0) == pytest.approx(0.30)
