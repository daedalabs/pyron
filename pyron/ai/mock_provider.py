"""Deterministic offline provider.

This provider exists for plumbing, tests, and zero-key demos. It never invents
facts: every claim is a verbatim sentence lifted from the supplied evidence, and
it deliberately refuses to author review questions, because a fabricated answer
key is worse than no answer key.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pyron.ai.base import BaseAIProvider, CompletionRequest, CompletionResponse

# Administrative lines that must never become outline topics.
_METADATA = re.compile(
    r"^\s*(?:initial\s+title|instructor|lecturer|professor|department|university|"
    r"college|faculty|course|subject|term|batch|members|trans\s+head|"
    r"trans\s+group\s+leader|group\s+leader|editors?|topic\s+outline|outline|"
    r"table\s+of\s+contents|references|bibliography|"
    r"prepared\s+by|presented\s+by)\b",
    re.IGNORECASE,
)
_COURSE_CODE = re.compile(r"^\s*[A-Z]{2,6}\s*\d{2,4}\b")
_EVIDENCE_BLOCK = re.compile(r"\[(E_[^\]]+)\]\s*(?:\(([^)]+)\))?\s*([\s\S]*?)(?=\n\[E_|\Z)")
_EVIDENCE_REGION = re.compile(
    r"----- EVIDENCE START -----\r?\n([\s\S]*?)\r?\n----- EVIDENCE END -----"
)
_LIST_PREFIX = re.compile(r"^(?:[-*•]|\d+[.)]|[IVXLC]{1,5}\.|[a-zA-Z]\.)\s+")
_INVISIBLE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NUMBER = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?:st|nd|rd|th)?(?!\d)")


def _evidence_region(prompt: str) -> str:
    """Only the material between the evidence markers is source text.

    Without this, the JSON instructions that follow the evidence are parsed as
    if they were lecture content (and then correctly-ish rejected as claims).
    """
    match = _EVIDENCE_REGION.search(prompt)
    return match.group(1) if match else prompt


def _clean_line(line: str) -> str:
    line = _INVISIBLE.sub("", line).replace("\xa0", " ")
    line = re.sub(r"\s+", " ", line).strip()
    while True:
        stripped = _LIST_PREFIX.sub("", line).strip()
        if stripped == line:
            break
        line = stripped
    return line


def _is_topic(line: str) -> bool:
    cleaned = line.strip().rstrip(":").strip()
    if len(cleaned) < 5 or len(cleaned) > 70:
        return False
    if cleaned.startswith("[") or _METADATA.match(cleaned) or _COURSE_CODE.match(cleaned):
        return False
    return not cleaned.lower().startswith(("slide", "task:", "evidence", "generate", "subject:"))


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class MockProvider(BaseAIProvider):
    name = "mock"
    model = "mock"
    cost_per_mtok_input = 0.0
    cost_per_mtok_output = 0.0

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        prompt = req.prompt
        if "TASK: GENERATE_OUTLINE" in prompt:
            response = self._outline(prompt)
        elif "TASK: DRAFT_SECTION" in prompt:
            response = self._section(prompt)
        elif "TASK: REVIEW_QUESTIONS" in prompt:
            # Intentionally empty: see module docstring.
            response = {"questions": []}
        else:
            response = {"status": "ok"}
        text = json.dumps(response, ensure_ascii=False)
        return CompletionResponse(
            text=text,
            input_tokens=_approx_tokens(prompt),
            output_tokens=_approx_tokens(text),
        )

    def _outline(self, prompt: str) -> dict[str, Any]:
        topics: list[str] = []
        for _eid, _kind, block in _EVIDENCE_BLOCK.findall(_evidence_region(prompt)):
            first_line = block.strip().splitlines()[0] if block.strip() else ""
            if _is_topic(first_line):
                cleaned = first_line.strip().rstrip(":").strip()
                if cleaned not in topics:
                    topics.append(cleaned)

        if len(topics) < 3:
            topics = [
                "Core Concepts & Definitions",
                "Mechanisms & Processes",
                "Trade-Offs & Analysis",
                "Summary & Review",
            ]

        numerals = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]
        nodes = [
            {
                "id": f"sec_{idx + 1}",
                "numeral": numerals[idx % len(numerals)],
                "title": title,
                "level": 1,
            }
            for idx, title in enumerate(topics[:6])
        ]
        title_match = re.search(r"Initial Title:\s*([^\n]+)", prompt)
        title = title_match.group(1).strip() if title_match else topics[0]
        return {"title": title, "outline": nodes}

    def _section(self, prompt: str) -> dict[str, Any]:
        node_match = re.search(r"Outline Node\s*\[([^\]]+)\]:\s*([^\n]+)", prompt)
        sec_id = node_match.group(1).strip() if node_match else "sec_1"
        raw_title = node_match.group(2).strip() if node_match else "Topic"
        sec_title = re.sub(r"^[IVXLCDM]+\.\s*", "", raw_title).strip()
        max_claims = 15

        # Build candidates per evidence block so every source (slides,
        # transcript, notes) is represented, not just the first block.
        blocks: list[dict[str, Any]] = []
        for eid, kind, block in _EVIDENCE_BLOCK.findall(_evidence_region(prompt)):
            source_kind = (kind or "").lower()
            if "figure" in source_kind:
                continue
            spoken = any(k in source_kind for k in ("transcript", "notes", "textbook"))
            candidates: list[str] = []
            for line in block.splitlines():
                line = _clean_line(line)
                if not line or self._is_title_line(line, sec_title):
                    continue
                if line.startswith("[SPEAKER NOTES]: "):
                    line = line[len("[SPEAKER NOTES]: ") :]
                for sentence in _SENTENCE.split(line):
                    sentence = sentence.strip()
                    if len(sentence) < 25 or _METADATA.match(sentence):
                        continue
                    # Heading fragments ("PACELC THEOREM EXTENSION:") are not
                    # claims; they carry no proposition to verify.
                    if sentence.endswith(":") or (sentence.isupper() and len(sentence) < 80):
                        continue
                    candidates.append(sentence)
            if candidates:
                blocks.append({"eid": eid, "spoken": spoken, "candidates": candidates})

        if not blocks:
            # Some decks are entirely short headings ("Beneficence", "Autonomy").
            # Keep them rather than emitting an empty section.
            for eid, kind, block in _EVIDENCE_BLOCK.findall(_evidence_region(prompt)):
                source_kind = (kind or "").lower()
                if "figure" in source_kind:
                    continue
                spoken = any(k in source_kind for k in ("transcript", "notes", "textbook"))
                candidates = []
                for line in block.splitlines():
                    line = _clean_line(line)
                    if len(line) < 8 or self._is_title_line(line, sec_title):
                        continue
                    if _METADATA.match(line):
                        continue
                    candidates.append(line)
                if candidates:
                    blocks.append({"eid": eid, "spoken": spoken, "candidates": candidates})

        claims: list[dict[str, Any]] = []
        bullets: list[dict[str, Any]] = []
        seen: set[str] = set()
        per_block = max(2, max_claims // max(1, len(blocks)))

        for block in blocks:
            for sentence in block["candidates"][:per_block]:
                if len(claims) >= max_claims:
                    break
                if sentence in seen:
                    continue
                seen.add(sentence)

                speech_act = "assertion"
                lowered = sentence.lower()
                if block["spoken"]:
                    if re.search(
                        r"\b(?:exam\s+(?:hint|note|favorite)|high[- ]yield|board\s+favorite)\b",
                        lowered,
                    ):
                        speech_act = "exam_hint"
                    elif re.search(r"\b(?:mnemonic|acronym)\b", lowered):
                        speech_act = "mnemonic"
                    elif re.search(r"\b(?:warning|contraindication|caution|hazard)\b", lowered):
                        speech_act = "warning"
                    elif sentence.endswith("?"):
                        speech_act = "question"

                cid = f"{sec_id}_C{len(claims) + 1:02d}"
                claims.append(
                    {
                        "cid": cid,
                        "text": sentence,
                        "evidence": [block["eid"]],
                        "support": "explicit",
                        "speech_act": speech_act,
                        "numbers": _NUMBER.findall(sentence),
                    }
                )
                bullets.append({"level": 1, "text": sentence, "claim_ids": [cid]})
            if len(claims) >= max_claims:
                break

        # No fabricated filler: an empty result makes the pipeline fall back to
        # verbatim slide lines (or report honestly that nothing was mapped).
        return {
            "claims": claims,
            "blocks": [{"kind": "bullets", "payload": {"items": bullets}}] if bullets else [],
        }

    @staticmethod
    def _is_title_line(line: str, section_title: str) -> bool:
        def normalize(value: str) -> str:
            return re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()

        line_norm = normalize(line)
        title_norm = normalize(section_title)
        if not title_norm or not line_norm:
            return False
        return (
            line_norm == title_norm
            or line_norm.startswith(title_norm)
            or (len(line_norm) >= 15 and title_norm.startswith(line_norm))
        )
