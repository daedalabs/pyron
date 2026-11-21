from __future__ import annotations

import pathlib
import subprocess
import sys

from pyron.api.contracts import SourceKind, SubjectSpec, TransDocument
from pyron.cli import _classify, main

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyron", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_missing_input_fails_cleanly():
    result = run_cli("build", "does_not_exist.pptx")
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert "Traceback" not in result.stderr


def test_unsupported_extension_is_rejected(tmp_path):
    target = tmp_path / "slides.docx"
    target.write_bytes(b"PK\x03\x04")
    result = run_cli("build", str(target))
    assert result.returncode == 2
    assert "Unsupported input" in result.stderr


def test_legacy_ppt_is_rejected(tmp_path):
    target = tmp_path / "deck.ppt"
    target.write_bytes(b"\xd0\xcf\x11\xe0")
    result = run_cli("build", str(target))
    assert result.returncode == 2
    assert ".pptx" in result.stderr


def test_version_flag():
    result = run_cli("--version")
    assert result.returncode == 0
    assert "0.1.0" in result.stdout


def test_render_does_not_need_an_api_key(tmp_path):
    doc = TransDocument(doc_id="d", title="T", subject=SubjectSpec(name="S"))
    doc_json = tmp_path / "doc.json"
    doc.save_json(doc_json)
    output = tmp_path / "out.md"
    code = main(["render", str(doc_json), "--format", "md", "--out", str(output)])
    assert code == 0
    assert output.exists()


def test_render_missing_doc_json_is_clean(tmp_path):
    code = main(["render", str(tmp_path / "nope.json")])
    assert code == 2


def test_pdf_classification_prefers_textbook_for_readings(tmp_path):
    assert _classify(tmp_path / "lecture_slides.pdf") == SourceKind.SLIDES
    assert _classify(tmp_path / "supplementary_reading.pdf") == SourceKind.TEXTBOOK
    assert _classify(tmp_path / "lecture_transcript.txt") == SourceKind.TRANSCRIPT
    assert _classify(tmp_path / "notes.md") == SourceKind.NOTES
