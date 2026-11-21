from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pyron.ai.base import BaseAIProvider, CompletionRequest
from pyron.api.contracts import (
    Block,
    ClaimCard,
    EvidenceSpan,
    GenerateRequest,
    OutlineNode,
    ReviewQuestion,
    ReviewSet,
    RunManifest,
    Section,
    SourceKind,
    TransDocument,
    TransWarning,
    Usage,
    VerifiedClaim,
)
from pyron.api.errors import InputError, ProviderError
from pyron.ingestion.base import BaseExtractor
from pyron.ingestion.notes_extractor import NotesExtractor
from pyron.ingestion.pdf_extractor import PDFExtractor
from pyron.ingestion.pptx_extractor import PPTXExtractor
from pyron.ingestion.transcript_parser import TranscriptParser
from pyron.synthesis.claim_utils import (
    ClaimMatcher,
    normalize_text,
    split_claim_text,
)
from pyron.synthesis.prompt_templates import (
    DRAFT_SECTION_SYSTEM,
    OUTLINE_SYSTEM,
    REVIEW_QUESTIONS_SYSTEM,
)
from pyron.synthesis.tag_policy import extract_numerals, verify_claim

MAX_CLAIM_WORDS = 40
MAX_SECTION_CHARS = 6000
MAX_OUTLINE_SPANS = 40

_ADMINISTRATIVE = re.compile(
    r"^\s*(?:initial\s+title|instructor|lecturer|professor|department|university|"
    r"college|faculty|course\s+code|batch|members|trans\s+head)\b",
    re.IGNORECASE,
)

OUTLINE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "outline": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "numeral": {"type": "string"},
                    "title": {"type": "string"},
                    "level": {"type": "integer"},
                },
                "required": ["id", "numeral", "title"],
            },
        },
    },
    "required": ["title", "outline"],
}

SECTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cid": {"type": "string"},
                    "text": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "support": {"type": "string"},
                    "speech_act": {"type": "string"},
                    "numbers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "evidence"],
            },
        },
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["kind", "payload"],
            },
        },
    },
    "required": ["claims", "blocks"],
}

REVIEW_QUESTIONS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "stem": {"type": "string"},
                    "options": {"type": "object"},
                    "correct_answer": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["id", "stem", "options", "correct_answer"],
            },
        }
    },
    "required": ["questions"],
}


def _distribute(items: list[Any], total: int) -> list[list[Any]]:
    """Spread items over ``total`` buckets without duplicating any item."""
    buckets: list[list[Any]] = [[] for _ in range(max(1, total))]
    if not items or total <= 0:
        return buckets
    count = len(items)
    for index, item in enumerate(items):
        bucket = min(total - 1, index * total // count)
        buckets[bucket].append(item)
    return buckets


class SynthesisPipeline:
    def __init__(self, ai_provider: BaseAIProvider):
        self.ai = ai_provider
        self.extractors: list[BaseExtractor] = [
            PDFExtractor(),
            PPTXExtractor(),
            TranscriptParser(),
            NotesExtractor(),
        ]

    async def run(self, req: GenerateRequest) -> TransDocument:
        run_id = req.idempotency_key or f"run_{uuid.uuid4().hex[:10]}"
        timestamp = datetime.now(UTC).isoformat()
        warnings: list[TransWarning] = []
        usage = Usage()

        all_spans, source_hashes = self._ingest(req.sources, warnings)
        if not all_spans:
            raise InputError(
                "No readable evidence found in the supplied sources. "
                "Supported inputs: .pptx/.pdf slides, .vtt/.srt transcripts, "
                ".txt/.md notes."
            )

        evidence_map = {span.eid: span for span in all_spans}

        title, outline = await self._generate_outline(req, all_spans, warnings, usage)
        sections, stats = await self._generate_sections(
            req, outline, all_spans, evidence_map, warnings, usage
        )
        review_set: ReviewSet | None = None
        if req.options.include_review_questions:
            review_set = await self._generate_review_questions(
                req, title, sections, warnings, usage
            )
            if review_set is not None and not review_set.questions:
                warnings.append(
                    TransWarning(
                        code="review_questions_empty",
                        message=(
                            "No review questions were produced. The offline mock "
                            "provider does not author questions; configure a real "
                            "AI provider to generate them."
                        ),
                    )
                )

        manifest = RunManifest(
            run_id=run_id,
            timestamp=timestamp,
            model=getattr(self.ai, "model", None) or self.ai.name,
            source_hashes=source_hashes,
            options={**req.options.model_dump(), "stats": stats},
        )

        assets = {
            span.media_path: span.media_bytes
            for span in all_spans
            if span.media_path and span.media_bytes
        }

        return TransDocument(
            schema_version="1.0",
            doc_id=run_id,
            title=title,
            subject=req.subject,
            outline=outline,
            sections=sections,
            evidence=all_spans,
            review=review_set,
            usage=usage,
            warnings=warnings,
            manifest=manifest,
            assets=assets,
        )

    # ------------------------------------------------------------------ ingest

    def _ingest(
        self, sources: list[Any], warnings: list[TransWarning]
    ) -> tuple[list[EvidenceSpan], dict[str, str]]:
        spans: list[EvidenceSpan] = []
        source_hashes: dict[str, str] = {}
        for source in sources:
            extractor = next((e for e in self.extractors if e.can_handle(source)), None)
            if extractor is None:
                warnings.append(
                    TransWarning(
                        code="unsupported_source",
                        message=(
                            f"No extractor supports this source: {source.path or source.kind}"
                        ),
                        locator=str(source.path) if source.path else None,
                    )
                )
                continue
            try:
                extracted = extractor.extract(source)
            except InputError:
                # Actionable configuration errors (e.g. missing PDF extra)
                # must reach the user, not be buried in a warning.
                raise
            except Exception as exc:  # noqa: BLE001 - untrusted input boundary
                warnings.append(
                    TransWarning(
                        code="source_extract_failed",
                        message=f"Could not read {source.path or source.kind}: {exc}",
                        locator=str(source.path) if source.path else None,
                    )
                )
                continue
            if not extracted:
                warnings.append(
                    TransWarning(
                        code="source_empty",
                        message=f"No text could be extracted from {source.path or source.kind}.",
                        locator=str(source.path) if source.path else None,
                    )
                )
                continue
            spans.extend(extracted)
            key = str(source.path) if source.path else str(source.kind)
            source_hashes[key] = extracted[0].doc_sha
        return spans, source_hashes

    # ---------------------------------------------------------------------- AI

    async def _complete_json(
        self,
        *,
        prompt: str,
        system_instruction: str,
        usage: Usage,
        warnings: list[TransWarning],
        stage: str,
        temperature: float = 0.2,
        response_json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call the provider in JSON mode, with one parse retry and one retry
        for transient provider errors."""
        last_error: Exception | None = None
        current_prompt = prompt
        for attempt in range(2):
            try:
                response = await self.ai.complete(
                    CompletionRequest(
                        prompt=current_prompt,
                        system_instruction=system_instruction,
                        temperature=temperature,
                        json_mode=True,
                        response_json_schema=response_json_schema,
                    )
                )
            except ProviderError as exc:
                last_error = exc
                if attempt == 0 and exc.retryable:
                    warnings.append(
                        TransWarning(
                            code="provider_retry",
                            message=f"{stage}: retrying after provider error: {exc.message}",
                        )
                    )
                    continue
                raise
            usage.input_tokens += response.input_tokens
            usage.output_tokens += response.output_tokens
            usage.cost_usd += self.ai.estimate_cost(
                response.input_tokens, response.output_tokens
            )
            try:
                return json.loads(self._clean_json(response.text))
            except (json.JSONDecodeError, TypeError) as exc:
                last_error = exc
                current_prompt = (
                    prompt
                    + "\n\nYour previous reply was not valid JSON. "
                    "Return ONLY the JSON object, with no prose or code fences."
                )
        raise ValueError(f"{stage}: provider did not return valid JSON ({last_error})")

    # ----------------------------------------------------------------- outline

    async def _generate_outline(
        self,
        req: GenerateRequest,
        spans: list[EvidenceSpan],
        warnings: list[TransWarning],
        usage: Usage,
    ) -> tuple[str, list[OutlineNode]]:
        slide_spans = [s for s in spans if s.kind == SourceKind.SLIDES]
        if not slide_spans:
            slide_spans = [s for s in spans if s.kind == SourceKind.NOTES] or spans

        evidence_text = "\n\n".join(
            f"[{s.eid}] {s.text[:400]}" for s in slide_spans[:MAX_OUTLINE_SPANS]
        )
        prompt = f"""TASK: GENERATE_OUTLINE
Subject: {req.subject.name} {req.subject.code}
Initial Title: {req.title}

----- EVIDENCE START -----
{evidence_text}
----- EVIDENCE END -----

Return JSON with:
"title": "Clear concise lecture title",
"outline": [
  {{"id": "sec_1", "numeral": "I", "title": "Main Topic 1", "level": 1}},
  {{"id": "sec_2", "numeral": "II", "title": "Main Topic 2", "level": 1}}
]
"""
        try:
            data = await self._complete_json(
                prompt=prompt,
                system_instruction=OUTLINE_SYSTEM,
                usage=usage,
                warnings=warnings,
                stage="outline",
                response_json_schema=OUTLINE_JSON_SCHEMA,
            )
            title = str(data.get("title") or req.title).strip()
            nodes = self._parse_outline(data.get("outline", []))
            if nodes:
                return title, nodes
        except (ProviderError, ValueError) as exc:
            warnings.append(
                TransWarning(
                    code="outline_llm_failed",
                    message=f"LLM outline formulation failed: {exc}",
                )
            )

        return req.title, self._fallback_outline(slide_spans)

    def _parse_outline(self, raw_nodes: Any) -> list[OutlineNode]:
        nodes: list[OutlineNode] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(raw_nodes or [], start=1):
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip().rstrip(":").strip()
            if len(title) < 3 or _ADMINISTRATIVE.match(title):
                continue
            node_id = str(raw.get("id") or f"sec_{index}").strip() or f"sec_{index}"
            node_id = re.sub(r"[^A-Za-z0-9_\-]", "_", node_id)
            while node_id in seen_ids:
                node_id = f"{node_id}_{index}"
            seen_ids.add(node_id)
            try:
                level = int(raw.get("level") or 1)
            except (TypeError, ValueError):
                level = 1
            nodes.append(
                OutlineNode(
                    id=node_id,
                    numeral=str(raw.get("numeral") or index),
                    title=title[:120],
                    level=max(1, min(level, 4)),
                    parent_id=raw.get("parent_id"),
                )
            )
        return nodes

    def _fallback_outline(self, spans: list[EvidenceSpan]) -> list[OutlineNode]:
        numerals = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
        nodes: list[OutlineNode] = []
        for span in spans[:10]:
            first_line = span.text.splitlines()[0].strip() if span.text else ""
            title = re.sub(r"^[0-9.\-\s]+", "", first_line).strip().rstrip(":").strip()
            if len(title) < 4 or title.startswith("[") or _ADMINISTRATIVE.match(title):
                continue
            nodes.append(
                OutlineNode(
                    id=f"sec_{len(nodes) + 1}",
                    numeral=numerals[len(nodes) % len(numerals)],
                    title=title[:60],
                    level=1,
                )
            )
        if not nodes:
            nodes = [
                OutlineNode(id="sec_1", numeral="I", title="Overview & Fundamentals", level=1),
                OutlineNode(id="sec_2", numeral="II", title="Mechanisms & Implementation", level=1),
                OutlineNode(id="sec_3", numeral="III", title="Analysis & Summary", level=1),
            ]
        return nodes

    # ---------------------------------------------------------------- sections

    async def _generate_sections(
        self,
        req: GenerateRequest,
        outline: list[OutlineNode],
        spans: list[EvidenceSpan],
        evidence_map: dict[str, EvidenceSpan],
        warnings: list[TransWarning],
        usage: Usage,
    ) -> tuple[list[Section], dict[str, int]]:
        slide_spans = [s for s in spans if s.kind == SourceKind.SLIDES]
        transcript_spans = [s for s in spans if s.kind == SourceKind.TRANSCRIPT]
        note_spans = [
            s for s in spans if s.kind in (SourceKind.NOTES, SourceKind.TEXTBOOK)
        ]
        figure_spans = [s for s in spans if s.kind == SourceKind.FIGURE]
        all_slides_text = " ".join(s.text for s in slide_spans)
        total = len(outline)

        slide_buckets = _distribute(slide_spans, total)
        transcript_buckets = _distribute(transcript_spans, total)
        note_buckets = _distribute(note_spans, total)

        max_concurrency = getattr(req.options, "max_concurrency", 3) or 3
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _draft_section(index: int, node: OutlineNode):
            async with semaphore:
                bucket = slide_buckets[index] if index < len(slide_buckets) else []
                matched = [s for s in bucket if node.title.lower() in s.text.lower()]
                # Keep the whole bucket: title-matched spans are promoted, not a
                # replacement for the rest of the section's material.
                seen_eids = {s.eid for s in matched}
                section_slides = matched + [s for s in bucket if s.eid not in seen_eids]
                section_transcript = (
                    transcript_buckets[index] if index < len(transcript_buckets) else []
                )
                section_notes = note_buckets[index] if index < len(note_buckets) else []

                # Find any figures associated with the slides in this section
                slide_nos = {
                    s.locator.get("slide_no")
                    for s in section_slides
                    if s.locator.get("slide_no") is not None
                }
                section_figures = [
                    f for f in figure_spans if f.locator.get("slide_no") in slide_nos
                ]
                section_spans = (
                    section_slides + section_transcript + section_notes + section_figures
                )

                if not section_spans:
                    warnings.append(
                        TransWarning(
                            code="section_without_evidence",
                            message=f"No source material maps to section '{node.title}'.",
                            locator=node.id,
                        )
                    )

                evidence_context = "\n".join(
                    f"[{s.eid}] ({s.kind}) {s.text}" for s in section_spans
                )
                prompt = f"""TASK: DRAFT_SECTION
Outline Node [{node.id}]: {node.numeral}. {node.title}

----- EVIDENCE START -----
{evidence_context[:MAX_SECTION_CHARS]}
----- EVIDENCE END -----

Return JSON with "claims" and "blocks".
- claims: atomic statements (<= {MAX_CLAIM_WORDS} words), each citing exact evidence IDs.
- blocks: bullets whose items include "claim_ids" referencing those claim IDs, or a
  table with "headers" and "rows".

{{
  "claims": [
    {{"cid": "{node.id}_C01", "text": "Exact factual statement from the evidence.",
      "evidence": ["{section_spans[0].eid if section_spans else 'E1'}"],
      "support": "explicit", "speech_act": "assertion", "numbers": []}}
  ],
  "blocks": [
    {{"kind": "bullets", "payload": {{"items": [
      {{"level": 1, "text": "Short bullet restating the claim", "claim_ids": ["{node.id}_C01"]}}
    ]}}}}
  ]
}}
"""
                verified_claims: list[VerifiedClaim] = []
                raw_blocks: list[Block] = []
                sec_dropped = 0
                sec_dropped_examples: list[str] = []

                try:
                    data = await self._complete_json(
                        prompt=prompt,
                        system_instruction=DRAFT_SECTION_SYSTEM,
                        usage=usage,
                        warnings=warnings,
                        stage=f"section:{node.id}",
                        response_json_schema=SECTION_JSON_SCHEMA,
                    )
                    raw_claims = data.get("claims") or []
                    if not isinstance(raw_claims, list):
                        raw_claims = []
                    for claim_index, raw in enumerate(raw_claims, start=1):
                        if not isinstance(raw, dict):
                            continue
                        text = re.sub(r"\s+", " ", str(raw.get("text") or "")).strip()
                        if not text:
                            continue
                        evidence_ids = [
                            str(eid) for eid in (raw.get("evidence") or []) if str(eid).strip()
                        ]
                        if not evidence_ids:
                            sec_dropped += 1
                            continue
                        base_cid = self._build_cid(node.id, raw.get("cid"), claim_index)
                        parts = split_claim_text(text, MAX_CLAIM_WORDS)
                        for part_index, part in enumerate(parts):
                            cid = base_cid
                            if len(parts) > 1:
                                cid = f"{base_cid}_{chr(ord('a') + min(part_index, 25))}"
                            try:
                                card = ClaimCard(
                                    cid=cid,
                                    section_id=node.id,
                                    text=part,
                                    evidence=evidence_ids,
                                    support=raw.get("support", "explicit"),
                                    speech_act=raw.get("speech_act", "assertion"),
                                    numbers=extract_numerals(part),
                                    section_hint=node.title,
                                )
                            except ValueError:
                                sec_dropped += 1
                                continue
                            verified = verify_claim(
                                card, evidence_map, slide_text=all_slides_text
                            )
                            if verified.status == "dropped":
                                sec_dropped += 1
                                if len(sec_dropped_examples) < 3:
                                    sec_dropped_examples.append(part[:70])
                                continue
                            verified_claims.append(verified)

                    for raw in data.get("blocks") or []:
                        if not isinstance(raw, dict):
                            continue
                        kind = raw.get("kind")
                        if kind not in {
                            "heading",
                            "bullets",
                            "table",
                            "banner",
                            "callout",
                            "qa_item",
                            "figure",
                        }:
                            continue
                        payload = raw.get("payload") or {}
                        if not isinstance(payload, dict):
                            payload = {}
                        raw_blocks.append(Block(kind=kind, payload=payload))

                except (ProviderError, ValueError) as exc:
                    warnings.append(
                        TransWarning(
                            code="section_llm_failed",
                            message=f"LLM drafting failed for section {node.id}: {exc}",
                            locator=node.id,
                        )
                    )

                # Attach reference figures from slides if not already placed
                existing_img_paths = {
                    b.payload.get("image_path")
                    for b in raw_blocks
                    if b.kind == "figure" and b.payload.get("image_path")
                }
                for fig in section_figures:
                    if fig.media_path and fig.media_path not in existing_img_paths:
                        raw_blocks.append(
                            Block(
                                kind="figure",
                                payload={
                                    "image_path": fig.media_path,
                                    "caption": fig.text.lstrip("[").rstrip("]"),
                                    "source_slide": fig.locator.get("slide_no"),
                                    "eid": fig.eid,
                                },
                            )
                        )
                        existing_img_paths.add(fig.media_path)

                return (
                    index,
                    node,
                    section_slides,
                    verified_claims,
                    raw_blocks,
                    sec_dropped,
                    sec_dropped_examples,
                )

        tasks = [_draft_section(index, node) for index, node in enumerate(outline)]
        drafted_results = await asyncio.gather(*tasks)
        drafted_results.sort(key=lambda item: item[0])

        sections: list[Section] = []
        seen_claims: set[str] = set()
        used_cids: set[str] = set()
        dropped = 0
        dropped_examples: list[str] = []
        duplicates = 0

        for (
            _idx,
            node,
            section_slides,
            raw_verified_claims,
            raw_blocks,
            sec_dropped,
            sec_dropped_examples,
        ) in drafted_results:
            dropped += sec_dropped
            dropped_examples.extend(sec_dropped_examples)

            section_claims: list[VerifiedClaim] = []
            for claim in raw_verified_claims:
                fingerprint = normalize_text(claim.card.text)
                if fingerprint in seen_claims:
                    duplicates += 1
                    continue
                seen_claims.add(fingerprint)
                claim.card.cid = self._unique_cid(claim.card.cid, used_cids)
                section_claims.append(claim)

            blocks = self._annotate_blocks(raw_blocks, section_claims)

            if not blocks and section_slides:
                bullets: list[dict[str, Any]] = []
                for span in section_slides:
                    for line in span.text.splitlines()[:6]:
                        clean = line.strip()
                        if len(clean) > 5 and not clean.startswith("["):
                            bullets.append({"level": 1, "text": clean})
                if bullets:
                    blocks = [Block(kind="bullets", payload={"items": bullets})]

            if not blocks:
                # Never fabricate study content. Say what actually happened.
                blocks = [
                    Block(
                        kind="bullets",
                        payload={
                            "items": [
                                {
                                    "level": 1,
                                    "text": (
                                        "No source material was mapped to this section. "
                                        "Add slides or a transcript that covers this topic."
                                    ),
                                }
                            ]
                        },
                    )
                ]

            sections.append(
                Section(
                    id=node.id,
                    title=node.title,
                    blocks=blocks,
                    claims=section_claims,
                )
            )

        if dropped:
            examples = f" Examples: {' | '.join(dropped_examples)}" if dropped_examples else ""
            warnings.append(
                TransWarning(
                    code="claims_dropped",
                    message=(
                        f"{dropped} claim(s) failed verification and were dropped."
                        f"{examples}"
                    ),
                )
            )
        if duplicates:
            warnings.append(
                TransWarning(
                    code="claims_deduplicated",
                    message=f"{duplicates} duplicate claim(s) were removed.",
                )
            )

        stats = {
            "claims_verified": sum(len(s.claims) for s in sections),
            "claims_dropped": dropped,
            "claims_deduplicated": duplicates,
            "evidence_spans": len(spans),
        }
        return sections, stats

    @staticmethod
    def _coerce_level(value: Any) -> int:
        try:
            level = int(value)
        except (TypeError, ValueError):
            return 1
        return max(1, min(level, 3))

    @staticmethod
    def _build_cid(section_id: str, given: Any, index: int) -> str:
        base = re.sub(r"[^A-Za-z0-9_\-]", "", str(given or "")).strip("_")
        if not base:
            base = f"C{index:02d}"
        if not base.startswith(f"{section_id}_"):
            base = f"{section_id}_{base}"
        return base

    @staticmethod
    def _unique_cid(cid: str, used: set[str]) -> str:
        candidate = cid
        suffix = 2
        while candidate in used:
            candidate = f"{cid}_{suffix}"
            suffix += 1
        used.add(candidate)
        return candidate

    def _annotate_blocks(
        self, blocks: list[Block], claims: list[VerifiedClaim]
    ) -> list[Block]:
        """Attach verified badges to block items and drop model-supplied tags."""
        matcher = ClaimMatcher((c.card.cid, c.card.text) for c in claims)
        tag_by_cid = {c.card.cid: c.tag for c in claims if c.tag}
        valid_cids = set(tag_by_cid) | {c.card.cid for c in claims}
        annotated: list[Block] = []
        for block in blocks:
            payload = dict(block.payload)
            if block.kind == "bullets":
                items = payload.get("items")
                if not isinstance(items, list):
                    continue
                new_items = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    text = str(item.get("text") or "").strip()
                    if not text:
                        continue
                    raw_ids = item.get("claim_ids")
                    cids = [str(cid) for cid in raw_ids] if isinstance(raw_ids, list) else []
                    cids = [cid for cid in cids if cid in valid_cids]
                    cid = cids[0] if cids else matcher.match(text)
                    new_items.append(
                        {
                            "level": self._coerce_level(item.get("level")),
                            "text": text,
                            "claim_ids": [cid] if cid else [],
                            "tag": tag_by_cid.get(cid) if cid else None,
                        }
                    )
                if not new_items:
                    continue
                payload["items"] = new_items
            annotated.append(Block(kind=block.kind, width=block.width, payload=payload))
        return annotated

    # ------------------------------------------------------------------ review

    async def _generate_review_questions(
        self,
        req: GenerateRequest,
        title: str,
        sections: list[Section],
        warnings: list[TransWarning],
        usage: Usage,
    ) -> ReviewSet:
        summary_bullets: list[str] = []
        for section in sections:
            for block in section.blocks:
                if block.kind != "bullets":
                    continue
                for item in block.payload.get("items", [])[:2]:
                    summary_bullets.append(f"- {item.get('text', '')}")

        summary_text = "\n".join(summary_bullets[:15])
        prompt = f"""TASK: REVIEW_QUESTIONS
Lecture: {title} ({req.subject.name})
Summary Facts:
{summary_text}

Generate 3-5 high-yield multiple choice questions based STRICTLY on the facts above:
{{
  "questions": [
    {{
      "id": 1,
      "stem": "Specific question testing a key concept from this lecture?",
      "options": {{"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"}},
      "correct_answer": "A",
      "explanation": "Clear explanation of why A is correct."
    }}
  ]
}}
"""
        try:
            data = await self._complete_json(
                prompt=prompt,
                system_instruction=REVIEW_QUESTIONS_SYSTEM,
                usage=usage,
                warnings=warnings,
                stage="review_questions",
                response_json_schema=REVIEW_QUESTIONS_JSON_SCHEMA,
            )
        except (ProviderError, ValueError) as exc:
            warnings.append(
                TransWarning(
                    code="review_questions_failed",
                    message=f"Review question generation failed: {exc}",
                )
            )
            return ReviewSet(questions=[])

        questions: list[ReviewQuestion] = []
        for raw in data.get("questions") or []:
            if not isinstance(raw, dict):
                continue
            try:
                question = ReviewQuestion(**raw)
            except (TypeError, ValueError):
                continue
            if not question.stem.strip() or len(question.options) < 2:
                continue
            if question.correct_answer not in question.options:
                warnings.append(
                    TransWarning(
                        code="review_answer_invalid",
                        message=(
                            f"Dropped question '{question.stem[:40]}...': "
                            "correct answer is not one of the options."
                        ),
                    )
                )
                continue
            questions.append(question)
        for new_id, question in enumerate(questions, start=1):
            question.id = new_id
        return ReviewSet(questions=questions)

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _clean_json(text: str) -> str:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return text[start : end + 1]
        return text.strip()
