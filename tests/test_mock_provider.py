from __future__ import annotations

import asyncio
import json

from pyron.ai.base import CompletionRequest
from pyron.ai.mock_provider import MockProvider


def complete(prompt: str) -> dict:
    response = asyncio.run(
        MockProvider().complete(CompletionRequest(prompt=prompt, json_mode=True))
    )
    return json.loads(response.text)


OUTLINE_PROMPT = """TASK: GENERATE_OUTLINE
Subject: Distributed Systems [CS401]
Initial Title: Distributed Systems & Consensus Architecture

----- EVIDENCE START -----
[E_abc_S1] CS 401: Distributed Systems & Consensus Architecture
[E_abc_S2] Department of Computer Science & Engineering
[E_abc_S3] The CAP Theorem & PACELC Model
Consistency, availability and partition tolerance trade-offs.
[E_abc_S4] Replication Strategies: Synchronous vs. Asynchronous
[E_abc_S5] Distributed Consensus & The Raft Algorithm
----- EVIDENCE END -----

Return JSON with "title" and "outline".
"""


def test_outline_skips_administrative_metadata():
    data = complete(OUTLINE_PROMPT)
    titles = [node["title"] for node in data["outline"]]
    assert data["title"] == "Distributed Systems & Consensus Architecture"
    assert titles[:3] == [
        "The CAP Theorem & PACELC Model",
        "Replication Strategies: Synchronous vs. Asynchronous",
        "Distributed Consensus & The Raft Algorithm",
    ]
    assert not any("Department" in title or "CS 401" in title for title in titles)


SECTION_PROMPT = """TASK: DRAFT_SECTION
Outline Node [sec_1]: I. The CAP Theorem & PACELC Model

----- EVIDENCE START -----
[E_abc_S3] (slides) The CAP Theorem & PACELC Model
Consistency: Every read receives the most recent write.
Availability: Every non-failing node returns a response.
[E_abc_T1] (transcript) In production, network partitions are inevitable, so you never choose CA.
[E_abc_N1] (notes) Daniel Abadi proposed PACELC in 2012 to extend CAP.
----- EVIDENCE END -----

Return JSON with "claims" and "blocks".
- claims: atomic statements (<= 40 words), each citing exact evidence IDs.
{"claims": [{"text": "Short bullet restating the claim", "claim_ids": ["sec_1_C01"]}]}
"""


def test_section_claims_ignore_instruction_text():
    data = complete(SECTION_PROMPT)
    texts = [claim["text"] for claim in data["claims"]]
    assert texts
    assert all("atomic statements" not in text for text in texts)
    assert all("claim_ids" not in text for text in texts)
    assert any("network partitions are inevitable" in text for text in texts)
    assert any("Daniel Abadi" in text for text in texts)


def test_section_cids_are_namespaced_and_unique():
    data = complete(SECTION_PROMPT)
    cids = [claim["cid"] for claim in data["claims"]]
    assert len(cids) == len(set(cids))
    assert all(cid.startswith("sec_1_") for cid in cids)
    for block in data["blocks"]:
        for index, item in enumerate(block["payload"]["items"]):
            assert item["claim_ids"] == [cids[index]]


def test_section_does_not_repeat_the_section_title_as_a_bullet():
    data = complete(SECTION_PROMPT)
    texts = [claim["text"] for claim in data["claims"]]
    assert not any(text.strip().lower().startswith("the cap theorem") for text in texts)


def test_review_questions_are_not_fabricated():
    data = complete("TASK: REVIEW_QUESTIONS\nLecture: X\nSummary Facts:\n- a fact\n")
    assert data == {"questions": []}


def test_mock_never_invents_filler_content():
    prompt = """TASK: DRAFT_SECTION
Outline Node [sec_1]: I. Topic One

----- EVIDENCE START -----
[E_abc_S1] (slides) Hi
----- EVIDENCE END -----
"""
    data = complete(prompt)
    assert data == {"claims": [], "blocks": []}
