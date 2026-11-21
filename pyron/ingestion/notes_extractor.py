from __future__ import annotations

import hashlib

from pyron.api.contracts import EvidenceSpan, Source, SourceKind
from pyron.ingestion.base import BaseExtractor

SUPPORTED_SUFFIXES = {".txt", ".text", ".md", ".markdown"}


def _decode(raw: bytes) -> str:
    text = raw.decode("utf-8-sig", errors="replace")
    # Normalise newlines so CRLF files split into paragraphs correctly. Offsets
    # remain valid because the span text is measured in the normalised string.
    return text.replace("\r\n", "\n").replace("\r", "\n")


class NotesExtractor(BaseExtractor):
    """Ingests plain-text notes, readings, and markdown into evidence spans.

    Only real text files are accepted. Without this whitelist a binary file
    (e.g. a .docx) would be read as text and silently turned into garbage
    evidence.
    """

    def can_handle(self, source: Source) -> bool:
        if source.path is not None:
            return source.path.suffix.lower() in SUPPORTED_SUFFIXES
        return source.data is not None and source.kind in (
            SourceKind.NOTES,
            SourceKind.TEXTBOOK,
        )

    def extract(self, source: Source) -> list[EvidenceSpan]:
        spans: list[EvidenceSpan] = []
        if source.path and source.path.exists():
            raw = source.path.read_bytes()
            text_content = _decode(raw)
            doc_sha = hashlib.sha256(raw).hexdigest()
        elif source.data:
            raw = source.data
            text_content = _decode(raw)
            doc_sha = hashlib.sha256(raw).hexdigest()
        else:
            return spans

        kind = (
            source.kind
            if source.kind in (SourceKind.NOTES, SourceKind.TEXTBOOK)
            else SourceKind.NOTES
        )

        cursor = 0
        paragraphs = [p.strip() for p in text_content.split("\n\n") if p.strip()]
        for index, paragraph in enumerate(paragraphs, start=1):
            start = text_content.find(paragraph, cursor)
            if start == -1:
                start = cursor
            end = start + len(paragraph)
            cursor = end
            spans.append(
                EvidenceSpan(
                    eid=f"E_{doc_sha[:6]}_N{index:03d}",
                    kind=kind,
                    doc_sha=doc_sha,
                    char_span=(start, end),
                    locator={"paragraph": index},
                    text=paragraph,
                    confidence=1.0,
                )
            )
        return spans
