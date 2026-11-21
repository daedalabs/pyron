from __future__ import annotations

import asyncio
import json

import docx

from pyron.engine import TransEngine
from tests.helpers import FakeProvider, make_request


def build_doc():
    texts = {
        "sec_1": "Threads have cheaper context switches.",
        "sec_2": "Processes have isolated memory maps.",
    }

    def payload(section_id, ids, prompt):
        text = texts.get(section_id, "Threads have cheaper context switches.")
        return {
            "claims": [
                {
                    "cid": "C1",
                    "text": text,
                    "evidence": [ids[0]],
                    "support": "explicit",
                    "speech_act": "assertion",
                    "numbers": [],
                }
            ],
            "blocks": [
                {
                    "kind": "bullets",
                    "payload": {
                        "items": [
                            {
                                "level": 1,
                                "text": text,
                                "claim_ids": ["C1"],
                            }
                        ]
                    },
                }
            ],
        }

    provider = FakeProvider(sections=payload)
    engine = TransEngine.create(ai_provider=provider)
    return asyncio.run(engine.generate(make_request()))


def test_bundle_is_self_contained(tmp_path):
    doc = build_doc()
    engine = TransEngine.create(ai_provider=FakeProvider())
    bundle = engine.save_bundle(doc, output_dir=tmp_path / "bundle")

    for path in (
        bundle.doc_json,
        bundle.evidence_jsonl,
        bundle.claims_jsonl,
        bundle.questions_json,
        bundle.manifest_json,
        bundle.doc_md,
        bundle.doc_docx,
    ):
        assert path is not None and path.exists(), path

    payload = json.loads(bundle.doc_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["evidence"]

    claims = [
        json.loads(line)
        for line in bundle.claims_jsonl.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert claims
    evidence_ids = {
        json.loads(line)["eid"]
        for line in bundle.evidence_jsonl.read_text(encoding="utf-8").splitlines()
        if line
    }
    for claim in claims:
        assert claim["card"]["section_id"]
        assert set(claim["card"]["evidence"]) <= evidence_ids

    questions = json.loads(bundle.questions_json.read_text(encoding="utf-8"))
    assert questions == {"questions": []}

    assert json.loads(bundle.manifest_json.read_text(encoding="utf-8"))["run_id"]
    docx.Document(str(bundle.doc_docx))


def test_doc_md_has_no_duplicate_content(tmp_path):
    doc = build_doc()
    engine = TransEngine.create(ai_provider=FakeProvider())
    bundle = engine.save_bundle(doc, output_dir=tmp_path / "bundle")
    markdown = bundle.doc_md.read_text(encoding="utf-8")
    assert markdown.count("Threads have cheaper context switches.") == 1
    assert markdown.count("Processes have isolated memory maps.") == 1


def test_render_from_doc_json_needs_no_provider(tmp_path):
    doc = build_doc()
    doc_json = tmp_path / "doc.json"
    doc.save_json(doc_json)
    loaded = type(doc).load_json(doc_json)
    engine = TransEngine()
    output = engine.render(loaded, fmt="md", output_path=tmp_path / "out.md")
    assert output.exists()
