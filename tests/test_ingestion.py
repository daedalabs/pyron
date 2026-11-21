from __future__ import annotations

import pytest

from pyron.api.contracts import Source, SourceKind
from pyron.ingestion.notes_extractor import NotesExtractor
from pyron.ingestion.pdf_extractor import PDFExtractor, _clean_text, _split_items
from pyron.ingestion.transcript_parser import TranscriptParser


def test_pdf_mojibake_is_repaired():
    assert _clean_text("personâ€™s reputation") == "person’s reputation"
    assert "“" in _clean_text("â€œTo do good.")
    assert _clean_text("ðŸ—£ spoken marker") == "spoken marker"
    assert _clean_text("Normal caf\u00e9 text") == "Normal café text"


def test_pdf_wrapped_bullets_are_reflowed():
    items = _split_items(
        "• First bullet that wraps\nonto a second line.\n• Second bullet."
    )
    assert items == ["First bullet that wraps onto a second line.", "Second bullet."]


def test_distribute_covers_every_item_once():
    from pyron.synthesis.pipeline import _distribute

    items = list(range(10))
    buckets = _distribute(items, 3)
    flat = [item for bucket in buckets for item in bucket]
    assert sorted(flat) == items
    assert len(flat) == len(set(flat))


def test_notes_rejects_binary_documents(tmp_path):
    path = tmp_path / "slides.docx"
    path.write_bytes(b"PK\x03\x04not really a docx")
    assert NotesExtractor().can_handle(Source(kind=SourceKind.NOTES, path=path)) is False


def test_notes_accept_markdown_and_report_real_offsets(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")
    spans = NotesExtractor().extract(Source(kind=SourceKind.NOTES, path=path))
    assert [span.text for span in spans] == ["First paragraph.", "Second paragraph."]
    source = path.read_text(encoding="utf-8")
    for span in spans:
        start, end = span.char_span
        assert source[start:end] == span.text


def test_transcript_parses_cues_and_times(tmp_path):
    path = tmp_path / "lecture.vtt"
    path.write_text(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:04.000\nHello there class.\n\n"
        "00:00:05.000 --> 00:00:09.000\nSecond cue text about threads.\n",
        encoding="utf-8",
    )
    spans = TranscriptParser().extract(Source(kind=SourceKind.TRANSCRIPT, path=path))
    assert spans
    assert spans[0].locator["t_start"] == "00:00:01.000"
    assert "Hello there class." in spans[0].text


def test_transcript_accepts_plain_text_transcripts(tmp_path):
    path = tmp_path / "lecture.txt"
    path.write_text("Speaker one talks about threads.", encoding="utf-8")
    assert TranscriptParser().can_handle(Source(kind=SourceKind.TRANSCRIPT, path=path))


def test_pdf_keeps_content_lines_and_drops_only_headers(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "deck.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((60, 80), "Lecturer: Dr. Ada", fontsize=11)
    page.insert_text((60, 110), "Week 3 - Synchronization primitives", fontsize=11)
    page.insert_text((60, 140), "Mutex ownership is crucial.", fontsize=11)
    document.save(path)
    document.close()

    spans = PDFExtractor().extract(Source(kind=SourceKind.SLIDES, path=path))
    text = "\n".join(span.text for span in spans)
    assert "Lecturer:" not in text
    assert "Week 3 - Synchronization primitives" in text
    assert "Mutex ownership is crucial." in text


def test_pdf_two_column_reading_order(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "twocol.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((60, 90), "LEFT TOPIC HEADING", fontsize=11)
    page.insert_text((60, 120), "left second line of content", fontsize=11)
    page.insert_text((330, 90), "RIGHT TOPIC HEADING", fontsize=11)
    page.insert_text((330, 120), "right second line of content", fontsize=11)
    document.save(path)
    document.close()

    spans = PDFExtractor().extract(Source(kind=SourceKind.SLIDES, path=path))
    text = "\n".join(span.text for span in spans)
    assert text.index("LEFT TOPIC HEADING") < text.index("RIGHT TOPIC HEADING")
    assert text.index("left second line") < text.index("RIGHT TOPIC HEADING")
