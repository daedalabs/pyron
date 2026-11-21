"""Shared test doubles."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pyron.ai.base import BaseAIProvider, CompletionRequest, CompletionResponse
from pyron.api.contracts import GenerateRequest, Source, SourceKind, SubjectSpec


def evidence_ids(prompt: str) -> list[str]:
    region = prompt
    match = re.search(
        r"----- EVIDENCE START -----\r?\n([\s\S]*?)\r?\n----- EVIDENCE END -----", prompt
    )
    if match:
        region = match.group(1)
    return re.findall(r"\[(E_[A-Za-z0-9_]+)\]", region)


class FakeProvider(BaseAIProvider):
    """Deterministic provider that returns configured JSON per task.

    ``sections`` may map a section id to a payload, or be a callable receiving
    ``(section_id, evidence_ids, prompt)`` and returning a payload.
    """

    name = "fake"
    model = "fake-1"

    def __init__(
        self,
        outline: dict[str, Any] | None = None,
        sections: dict[str, Any] | Callable[[str, list[str], str], Any] | None = None,
        review: dict[str, Any] | None = None,
    ):
        self.outline = outline or {
            "title": "Fake Title",
            "outline": [
                {"id": "sec_1", "numeral": "I", "title": "Topic One", "level": 1},
                {"id": "sec_2", "numeral": "II", "title": "Topic Two", "level": 1},
            ],
        }
        self.sections = sections if sections is not None else {}
        self.review = review if review is not None else {"questions": []}
        self.calls: list[CompletionRequest] = []

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.calls.append(req)
        if "TASK: GENERATE_OUTLINE" in req.prompt:
            data = self.outline
        elif "TASK: DRAFT_SECTION" in req.prompt:
            match = re.search(r"Outline Node \[([^\]]+)\]", req.prompt)
            section_id = match.group(1) if match else "sec_1"
            ids = evidence_ids(req.prompt)
            if callable(self.sections):
                data = self.sections(section_id, ids, req.prompt)
            else:
                data = self.sections.get(section_id, {"claims": [], "blocks": []})
        elif "TASK: REVIEW_QUESTIONS" in req.prompt:
            data = self.review
        else:
            data = {}
        text = json.dumps(data)
        return CompletionResponse(text=text, input_tokens=10, output_tokens=5)

    @property
    def json_mode_used(self) -> bool:
        return bool(self.calls) and all(call.json_mode for call in self.calls)


def make_request(
    notes: str = (
        "Threads share page tables. Processes do not share virtual memory.\n\n"
        "Context switches between threads avoid TLB invalidation."
    ),
    title: str = "Test Lecture",
    include_review: bool = True,
) -> GenerateRequest:
    return GenerateRequest(
        subject=SubjectSpec(code="[TEST101]", name="Testing", lecturer="Prof. Test"),
        title=title,
        sources=[Source(kind=SourceKind.NOTES, data=notes.encode("utf-8"))],
        options={"include_review_questions": include_review},
    )
