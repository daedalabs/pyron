from __future__ import annotations

from pyron.api.contracts import ClaimCard, EvidenceSpan, SourceKind
from pyron.synthesis.tag_policy import (
    extract_numerals,
    numeral_supported,
    verify_claim,
)


def span(eid: str, kind: SourceKind, text: str) -> EvidenceSpan:
    return EvidenceSpan(eid=eid, kind=kind, doc_sha="sha", text=text)


def card(text: str, eid: str = "E1", **kwargs) -> ClaimCard:
    return ClaimCard(cid="C1", text=text, evidence=[eid], **kwargs)


class TestNumeralProvenance:
    def test_rejects_substring_match(self):
        evidence = {"E1": span("E1", SourceKind.SLIDES, "Mortality is 30 percent in 2023.")}
        verdict = verify_claim(card("Mortality is 3 percent."), evidence)
        assert verdict.status == "dropped"

    def test_accepts_unit_suffix(self):
        assert numeral_supported("150", "Timeout is 150ms-300ms.")
        assert numeral_supported("300", "Timeout is 150ms-300ms.")

    def test_rejects_decimal_prefix(self):
        assert not numeral_supported("3", "Dose is 3.5 mg.")
        assert numeral_supported("3.5", "Dose is 3.5 mg.")

    def test_accepts_spelled_out_numbers(self):
        evidence = {"E1": span("E1", SourceKind.NOTES, "Three replication modes exist.")}
        verdict = verify_claim(card("3 replication modes exist."), evidence)
        assert verdict.status == "verified"

    def test_extract_numerals_includes_words(self):
        assert "3" in extract_numerals("Three modes, 2 replicas, 150ms.")
        assert "150" in extract_numerals("Three modes, 2 replicas, 150ms.")

    def test_drops_when_evidence_missing(self):
        assert verify_claim(card("Any statement here.", eid="E404"), {}).status == "dropped"


class TestTagProvenance:
    def test_slide_only_never_earns_a_badge(self):
        text = "Remember the difference between processes and threads."
        evidence = {"E1": span("E1", SourceKind.SLIDES, text)}
        assert verify_claim(card(text), evidence).tag is None

    def test_slide_only_question_word_is_not_a_question_badge(self):
        text = "This is a classic interview question."
        evidence = {"E1": span("E1", SourceKind.SLIDES, text)}
        assert verify_claim(card(text), evidence).tag is None

    def test_transcript_earns_spoken(self):
        text = "Raft uses randomized election timeouts to avoid split votes."
        evidence = {"E1": span("E1", SourceKind.TRANSCRIPT, text)}
        assert verify_claim(card(text), evidence, slide_text="Slides about threads.").tag == "SPOKEN"

    def test_reading_the_slide_aloud_earns_nothing(self):
        text = "Raft uses randomized election timeouts to avoid split votes."
        evidence = {"E1": span("E1", SourceKind.TRANSCRIPT, text)}
        assert verify_claim(card(text), evidence, slide_text=text).tag is None

    def test_notes_earn_reference(self):
        text = "PACELC extends CAP with latency trade-offs."
        evidence = {"E1": span("E1", SourceKind.NOTES, text)}
        assert verify_claim(card(text), evidence).tag == "TEXTBOOK"

    def test_question_requires_spoken_evidence(self):
        text = "How does Raft prevent split-brain elections?"
        evidence = {"E1": span("E1", SourceKind.TRANSCRIPT, text)}
        assert verify_claim(card(text), evidence).tag == "QUESTION"

    def test_generic_emphasis_is_not_an_exam_hint(self):
        text = "This is important and crucial for the design."
        evidence = {"E1": span("E1", SourceKind.TRANSCRIPT, text)}
        assert verify_claim(card(text), evidence).tag == "SPOKEN"

    def test_explicit_exam_hint_earns_remember(self):
        text = "Exam hint: remember randomized timeouts."
        evidence = {"E1": span("E1", SourceKind.TRANSCRIPT, text)}
        assert verify_claim(card(text), evidence).tag == "REMEMBER"

    def test_mnemonic_and_alert(self):
        mnemonic = "Mnemonic: SEC means Sequential, Eventual, Causal."
        alert = "Warning: this can cause data loss."
        assert verify_claim(card(mnemonic), {"E1": span("E1", SourceKind.TRANSCRIPT, mnemonic)}).tag == "MNEMONIC"
        assert verify_claim(card(alert), {"E1": span("E1", SourceKind.TRANSCRIPT, alert)}).tag == "ALERT"

    def test_empty_claim_is_dropped(self):
        evidence = {"E1": span("E1", SourceKind.NOTES, "Some evidence.")}
        assert verify_claim(card("   "), evidence).status == "dropped"
