from __future__ import annotations

import hashlib
import re
from typing import Any

from pyron.api.contracts import EvidenceSpan, Source, SourceKind
from pyron.ingestion.base import BaseExtractor

_TIMESTAMP = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)")
_CUE = re.compile(
    r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)"
)


class TranscriptParser(BaseExtractor):
    def can_handle(self, source: Source) -> bool:
        if source.kind == SourceKind.TRANSCRIPT:
            return True
        if source.path is not None:
            return source.path.suffix.lower() in (".vtt", ".srt")
        return False

    def extract(self, source: Source) -> list[EvidenceSpan]:
        if source.path and source.path.exists():
            raw = source.path.read_bytes()
            text_content = raw.decode("utf-8-sig", errors="replace")
            doc_sha = hashlib.sha256(raw).hexdigest()
        elif source.data:
            raw = source.data
            text_content = raw.decode("utf-8-sig", errors="replace")
            doc_sha = hashlib.sha256(raw).hexdigest()
        else:
            return []

        chunks = self._parse_cues(text_content)
        if not chunks:
            chunks = self._parse_paragraphs(text_content)

        grouped = self._group_chunks(chunks)
        spans: list[EvidenceSpan] = []
        for index, group in enumerate(grouped, start=1):
            spans.append(
                EvidenceSpan(
                    eid=f"E_{doc_sha[:6]}_T{index:04d}",
                    kind=SourceKind.TRANSCRIPT,
                    doc_sha=doc_sha,
                    char_span=(0, len(group["text"])),
                    locator={
                        "t_start": group["start"],
                        "t_end": group["end"],
                        "segment_idx": index,
                    },
                    text=group["text"],
                    confidence=1.0,
                )
            )
        return spans

    @staticmethod
    def _parse_cues(text: str) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        buffer: list[str] = []
        start = "00:00"
        end = "00:00"
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.upper().startswith("WEBVTT") or stripped.isdigit():
                continue
            cue = _CUE.search(stripped)
            if cue:
                if buffer:
                    chunks.append({"text": " ".join(buffer), "start": start, "end": end})
                    buffer = []
                start, end = cue.group(1), cue.group(2)
                continue
            if stripped.startswith("NOTE"):
                continue
            match = _TIMESTAMP.match(stripped)
            if match and stripped.startswith("["):
                if buffer:
                    chunks.append({"text": " ".join(buffer), "start": start, "end": end})
                    buffer = []
                start = end = match.group(1)
                continue
            buffer.append(stripped)
        if buffer:
            chunks.append({"text": " ".join(buffer), "start": start, "end": end})
        return chunks

    @staticmethod
    def _parse_paragraphs(text: str) -> list[dict[str, Any]]:
        chunks = []
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        for index, paragraph in enumerate(paragraphs, start=1):
            chunks.append({"text": paragraph, "start": f"P{index}", "end": f"P{index}"})
        return chunks

    @staticmethod
    def _group_chunks(
        chunks: list[dict[str, Any]], target_words: int = 90
    ) -> list[dict[str, Any]]:
        grouped: list[dict[str, Any]] = []
        buffer: list[str] = []
        start = ""
        end = ""
        words = 0
        for chunk in chunks:
            if not start:
                start = chunk["start"]
            end = chunk["end"]
            buffer.append(chunk["text"])
            words += len(chunk["text"].split())
            if words >= target_words:
                grouped.append({"text": " ".join(buffer), "start": start, "end": end})
                buffer, words, start, end = [], 0, "", ""
        if buffer:
            grouped.append({"text": " ".join(buffer), "start": start or "end", "end": end or "end"})
        return grouped
