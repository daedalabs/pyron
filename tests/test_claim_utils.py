from __future__ import annotations

from pyron.synthesis.claim_utils import (
    ClaimMatcher,
    normalize_text,
    split_claim_text,
    word_count,
)


def test_normalize_text_ignores_case_and_punctuation():
    assert normalize_text("  Threads SHARE page-tables! ") == "threads share page-tables"


def test_split_short_claim_is_unchanged():
    assert split_claim_text("One short statement.", 40) == ["One short statement."]


def test_split_long_claim_keeps_every_sentence():
    text = " ".join(["alpha beta gamma delta epsilon."] * 10)
    chunks = split_claim_text(text, 40)
    assert len(chunks) > 1
    assert all(word_count(chunk) <= 40 for chunk in chunks)
    assert sum(chunk.count("alpha") for chunk in chunks) == 10


def test_split_oversized_sentence_hard_wraps():
    text = " ".join(["word"] * 95) + "."
    chunks = split_claim_text(text, 40)
    assert all(word_count(chunk) <= 40 for chunk in chunks)
    assert sum(len(chunk.split()) for chunk in chunks) == 95


def test_matcher_prefers_exact_then_containment():
    matcher = ClaimMatcher(
        [
            ("sec_1_C01", "Threads share page tables, which is why context switches are cheap."),
            ("sec_1_C02", "Processes do not share virtual memory."),
        ]
    )
    assert matcher.match("Processes do not share virtual memory.") == "sec_1_C02"
    assert (
        matcher.match(
            "Threads share page tables, which is why context switches are cheap. "
            "This is why they win."
        )
        == "sec_1_C01"
    )
    assert matcher.match("Unrelated content.") is None
