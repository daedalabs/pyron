from __future__ import annotations

import asyncio

from pyron.engine import TransEngine
from tests.helpers import FakeProvider, make_request

LONG_TEXT = " ".join(
    ["Threads share page tables which keeps context switches cheap."] * 6
)


def section_payload(section_id: str, ids: list[str], prompt: str) -> dict:
    eid = ids[0]
    return {
        "claims": [
            {
                "cid": "C1",
                "text": "Threads share page tables.",
                "evidence": [eid],
                "support": "explicit",
                "speech_act": "assertion",
                "numbers": [],
            },
            {
                "cid": "C1",
                "text": LONG_TEXT,
                "evidence": [eid],
                "support": "explicit",
                "speech_act": "assertion",
                "numbers": [],
            },
            {
                "cid": "C3",
                "text": "Mortality is 3 percent.",
                "evidence": [eid],
                "support": "explicit",
                "speech_act": "assertion",
                "numbers": ["3"],
            },
        ],
        "blocks": [
            {
                "kind": "bullets",
                "payload": {
                    "items": [
                        {
                            "level": 1,
                            "text": "Threads share page tables.",
                            "claim_ids": ["C1"],
                            "tag": "REMEMBER",
                        }
                    ]
                },
            }
        ],
    }


def run(provider: FakeProvider, request=None):
    engine = TransEngine.create(ai_provider=provider)
    return asyncio.run(engine.generate(request or make_request(
        notes="Mortality is 30 percent in cohort 2023.\n\nThreads share page tables. "
        "Processes do not share virtual memory."
    )))


def test_claim_ids_are_globally_unique():
    doc = run(FakeProvider(sections=section_payload))
    cids = [claim.card.cid for section in doc.sections for claim in section.claims]
    assert cids
    assert len(cids) == len(set(cids))


def test_claims_are_atomic():
    doc = run(FakeProvider(sections=section_payload))
    assert all(len(claim.card.text.split()) <= 40 for s in doc.sections for claim in s.claims)


def test_duplicate_claims_removed_across_sections():
    doc = run(FakeProvider(sections=section_payload))
    texts = [claim.card.text for section in doc.sections for claim in section.claims]
    assert len(texts) == len(set(texts))
    assert any(w.code == "claims_deduplicated" for w in doc.warnings)


def test_failed_claims_are_dropped_and_reported():
    doc = run(FakeProvider(sections=section_payload))
    warnings = {warning.code: warning for warning in doc.warnings}
    assert "claims_dropped" in warnings
    assert all(c.card.text != "Mortality is 3 percent." for s in doc.sections for c in s.claims)


def test_llm_supplied_tags_are_ignored():
    doc = run(FakeProvider(sections=section_payload))
    items = [
        item
        for section in doc.sections
        for block in section.blocks
        if block.kind == "bullets"
        for item in block.payload.get("items", [])
    ]
    assert items
    assert all(item.get("tag") != "REMEMBER" for item in items)
    # Notes evidence yields a reference badge, derived from provenance.
    assert any(item.get("tag") == "TEXTBOOK" for item in items)


def test_blocks_without_claims_render_without_tags():
    def payload(section_id, ids, prompt):
        return {
            "claims": [],
            "blocks": [
                {
                    "kind": "bullets",
                    "payload": {"items": [{"level": 1, "text": "Untagged bullet."}]},
                }
            ],
        }

    doc = run(FakeProvider(sections=payload))
    item = doc.sections[0].blocks[0].payload["items"][0]
    assert item["tag"] is None
    assert item["claim_ids"] == []


def test_evidence_is_persisted_and_resolvable():
    doc = run(FakeProvider(sections=section_payload))
    evidence = {span.eid for span in doc.evidence}
    assert evidence
    for section in doc.sections:
        for claim in section.claims:
            assert set(claim.card.evidence) <= evidence


def test_usage_and_json_mode():
    provider = FakeProvider(sections=section_payload)
    doc = run(provider)
    assert doc.usage.input_tokens > 0
    assert doc.usage.output_tokens > 0
    assert provider.json_mode_used


def test_empty_review_questions_warns_instead_of_faking():
    doc = run(FakeProvider(sections=section_payload))
    assert doc.review is not None and doc.review.questions == []
    assert any(w.code == "review_questions_empty" for w in doc.warnings)


def test_invalid_review_answer_is_rejected():
    review = {
        "questions": [
            {
                "id": 1,
                "stem": "Valid question?",
                "options": {"A": "one", "B": "two"},
                "correct_answer": "C",
                "explanation": "n/a",
            },
            {
                "id": 2,
                "stem": "Another valid question?",
                "options": {"A": "one", "B": "two"},
                "correct_answer": "A",
                "explanation": "n/a",
            },
        ]
    }
    doc = run(FakeProvider(sections=section_payload, review=review))
    assert [q.id for q in doc.review.questions] == [1]
    assert any(w.code == "review_answer_invalid" for w in doc.warnings)


def test_sources_are_distributed_without_duplication():
    notes = "Paragraph one covers alpha content.\n\nParagraph two covers beta content."
    request = make_request(notes=notes)
    provider = FakeProvider(sections=lambda sid, ids, prompt: {
        "claims": [
            {"cid": "C1", "text": f"Claim for {sid}.", "evidence": [ids[0]]}
        ],
        "blocks": [],
    })
    doc = run(provider, request)
    used: list[str] = []
    for section in doc.sections:
        for claim in section.claims:
            used.extend(claim.card.evidence)
    assert len(used) == len(set(used))


def test_no_evidence_raises_input_error():
    from pyron.api.errors import InputError

    request = make_request(notes="   ")
    provider = FakeProvider(sections=section_payload)
    try:
        run(provider, request)
    except InputError:
        assert True
    else:  # pragma: no cover
        raise AssertionError("expected InputError for empty sources")
