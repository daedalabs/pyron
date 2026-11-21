"""Small, deterministic text helpers used to keep claims auditable.

These helpers deliberately avoid any model calls: everything here is a pure
function so the verification and rendering layers stay reproducible.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_WHITESPACE = re.compile(r"\s+")
# Keep digits, percent signs and hyphens: they carry meaning for provenance.
_NON_WORD = re.compile(r"[^a-z0-9%.\- ]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def normalize_text(text: str) -> str:
    """Lower-case, strip punctuation and collapse whitespace.

    Used for de-duplication and for matching rendered bullets back to the
    verified claim that produced them.
    """
    cleaned = _NON_WORD.sub("", text.strip().lower())
    return _WHITESPACE.sub(" ", cleaned).strip()


def word_count(text: str) -> int:
    return len(text.split())


def split_claim_text(text: str, max_words: int = 40) -> list[str]:
    """Split an over-long claim into sentence-aligned, <= max_words pieces.

    Keeps the original wording intact (no summarisation) so every resulting
    claim is still a verbatim-grounded statement. A single oversized sentence
    is hard-wrapped on word boundaries as a last resort.
    """
    stripped = text.strip()
    if not stripped:
        return []
    if word_count(stripped) <= max_words:
        return [stripped]

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(stripped) if s.strip()]
    if len(sentences) <= 1:
        words = stripped.split()
        return [
            " ".join(words[i : i + max_words])
            for i in range(0, len(words), max_words)
        ]

    chunks: list[str] = []
    buffer: list[str] = []
    buffer_words = 0
    for sentence in sentences:
        sentence_words = word_count(sentence)
        if sentence_words > max_words:
            if buffer:
                chunks.append(" ".join(buffer))
                buffer, buffer_words = [], 0
            words = sentence.split()
            for i in range(0, len(words), max_words):
                chunks.append(" ".join(words[i : i + max_words]))
            continue
        if buffer and buffer_words + sentence_words > max_words:
            chunks.append(" ".join(buffer))
            buffer, buffer_words = [], 0
        buffer.append(sentence)
        buffer_words += sentence_words
    if buffer:
        chunks.append(" ".join(buffer))
    return chunks


class ClaimMatcher:
    """Matches a block item's text to the claim that supports it.

    The primary link is an explicit ``claim_ids`` reference from the model.
    Text matching is only a fallback for models that omit the reference, and is
    intentionally conservative (exact match, then containment of a reasonably
    long claim).
    """

    def __init__(self, claims: Iterable[tuple[str, str]]):
        # (cid, claim_text)
        self._by_norm: dict[str, str] = {}
        for cid, text in claims:
            key = normalize_text(text)
            if key and key not in self._by_norm:
                self._by_norm[key] = cid
        self._ordered = sorted(self._by_norm.items(), key=lambda kv: len(kv[0]), reverse=True)

    def match(self, text: str) -> str | None:
        norm = normalize_text(text)
        if not norm:
            return None
        if norm in self._by_norm:
            return self._by_norm[norm]
        # A split claim is usually a sentence inside the rendered bullet.
        for claim_norm, cid in self._ordered:
            if len(claim_norm) >= 25 and claim_norm in norm:
                return cid
        return None
