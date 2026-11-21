"""Deterministic claim verification and badge ("tag") resolution.

The model is never trusted to choose a badge. A badge is a pure function of
(a) which sources support the claim, and (b) the model's reported speech act
when that act is supported by spoken evidence.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pyron.api.contracts import (
    ClaimCard,
    EvidenceSpan,
    SourceKind,
    VerifiedClaim,
)

_ORDINAL_SUFFIX = re.compile(r"(?:st|nd|rd|th)$", re.IGNORECASE)
_NUMERAL = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?:st|nd|rd|th)?(?!\d)")

# Word -> digit so "three modes" and "3 modes" verify against each other.
_NUMBER_WORDS: dict[str, str] = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000",
}
_DIGIT_WORDS: dict[str, tuple[str, ...]] = {}
for _word, _digit in _NUMBER_WORDS.items():
    _DIGIT_WORDS.setdefault(_digit, ())
    _DIGIT_WORDS[_digit] += (_word,)


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", text.lower()))


def lexical_overlap(text_a: str, text_b: str) -> float:
    """Fraction of text_a's tokens that also occur in text_b."""
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a.intersection(tokens_b)) / len(tokens_a)


def extract_numerals(text: str) -> list[str]:
    """Extract discrete numeric values, including spelled-out numbers."""
    lowered = text.lower()
    found = _NUMERAL.findall(lowered)
    for word, digit in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            found.append(digit)
    # Preserve order while removing duplicates.
    return list(dict.fromkeys(found))


def numeral_supported(numeral: str, evidence_text: str) -> bool:
    """True when ``numeral`` appears in ``evidence_text`` as a discrete token.

    Accepts the digit form and spelled-out equivalents so a faithful paraphrase
    ("3 modes" vs "three modes") is not dropped. Lookarounds keep "3" from
    matching "30", "2023" or "3.5" while still matching units ("150ms").
    """
    clean = _ORDINAL_SUFFIX.sub("", numeral)
    if not clean:
        return True
    pattern = rf"(?<![\d.]){re.escape(clean)}(?:st|nd|rd|th)?(?!\d)(?!\.\d)"
    if re.search(pattern, evidence_text, re.IGNORECASE):
        return True
    for word in _DIGIT_WORDS.get(clean, ()):
        if re.search(rf"\b{word}\b", evidence_text, re.IGNORECASE):
            return True
    return False


def verify_numeral_provenance(claim_text: str, evidence_text: str) -> bool:
    return all(numeral_supported(num, evidence_text) for num in extract_numerals(claim_text))


def _is_question(text: str, speech_act: str) -> bool:
    # A trailing question mark is evidence. The bare word "question" is not:
    # "this is a classic interview question" is a statement about a question.
    return speech_act == "question" or text.strip().endswith("?")


def _is_mnemonic(text: str, speech_act: str) -> bool:
    if speech_act == "mnemonic":
        return True
    return bool(re.search(r"\b(?:mnemonic|acronym)\b", text, re.IGNORECASE))


def _is_exam_hint(text: str, speech_act: str) -> bool:
    if speech_act == "exam_hint":
        return True
    # Deliberately narrow: only explicit exam-directed phrasing counts. Words
    # such as "remember", "important" or "crucial" appear in ordinary prose.
    return bool(
        re.search(
            r"\b(?:exam\s+(?:hint|note|favorite)|board\s+favorite|"
            r"favorite\s+of\s+the\s+board|"
            r"will\s+come\s+out|comes?\s+out\s+in\s+the\s+exam|"
            r"remember\s+this\s+for\s+the\s+exam|high[- ]yield)\b",
            text,
            re.IGNORECASE,
        )
    )


def _is_alert(text: str, speech_act: str) -> bool:
    if speech_act == "warning":
        return True
    return bool(
        re.search(r"\b(?:warning|contraindication|danger|caution|hazard)\b", text, re.IGNORECASE)
    )


def resolve_tag(
    claim: ClaimCard,
    spans: Sequence[EvidenceSpan],
    slide_text: str = "",
) -> str | None:
    """Resolve the badge from evidence provenance, never from the model alone."""
    kinds = {s.kind for s in spans}
    has_transcript = SourceKind.TRANSCRIPT in kinds
    has_reference = bool(kinds & {SourceKind.TEXTBOOK, SourceKind.NOTES})

    # Speech-act badges describe something the lecturer *said*. They are only
    # meaningful when the supporting evidence is spoken (or written notes),
    # never for plain slide content.
    if has_transcript or has_reference:
        if _is_mnemonic(claim.text, claim.speech_act):
            return "MNEMONIC"
        if _is_exam_hint(claim.text, claim.speech_act):
            return "REMEMBER"
        if _is_alert(claim.text, claim.speech_act):
            return "ALERT"
        if _is_question(claim.text, claim.speech_act):
            return "QUESTION"

    if has_transcript:
        # Anti "doc said" guard: reading the slide aloud does not earn a badge.
        if slide_text and lexical_overlap(claim.text, slide_text) >= 0.70:
            return None
        return "SPOKEN"

    if has_reference:
        return "TEXTBOOK"

    # Slide-only content is a standard bullet.
    return None


def verify_claim(
    claim: ClaimCard,
    evidence_map: dict[str, EvidenceSpan],
    slide_text: str = "",
) -> VerifiedClaim:
    """Verification gates.

    G1 - every cited span must exist.
    G2 - every number in the claim must occur in the cited evidence.
    G3 - the badge is derived from evidence provenance.
    """
    cited_spans = [evidence_map[eid] for eid in claim.evidence if eid in evidence_map]
    if not cited_spans or not claim.text.strip():
        return VerifiedClaim(card=claim, status="dropped", tag=None)

    combined_evidence_text = " ".join(s.text for s in cited_spans)
    for num in extract_numerals(claim.text):
        if not numeral_supported(num, combined_evidence_text):
            return VerifiedClaim(card=claim, status="dropped", tag=None)

    tag = resolve_tag(claim, cited_spans, slide_text=slide_text)
    return VerifiedClaim(card=claim, status="verified", tag=tag)
