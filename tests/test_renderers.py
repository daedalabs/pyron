from __future__ import annotations

import re
import zipfile

import docx

from pyron.api.contracts import (
    Block,
    BlockWidth,
    Section,
    SubjectSpec,
    TransDocument,
)
from pyron.renderers.docx_renderer import DocxRenderer
from pyron.renderers.markdown_renderer import MarkdownRenderer
from pyron.theming.theme_manager import ThemeManager


def make_doc() -> TransDocument:
    return TransDocument(
        doc_id="doc_1",
        title="Render Test",
        subject=SubjectSpec(code="[T101]", name="Testing"),
        sections=[
            Section(
                id="sec_1",
                title="Section One",
                blocks=[
                    Block(
                        kind="table",
                        width=BlockWidth.PAGE,
                        payload={
                            "headers": ["A|B", "C\nD"],
                            "rows": [["x|y", "z"]],
                        },
                    ),
                    Block(
                        kind="bullets",
                        payload={
                            "items": [
                                {
                                    "level": 1,
                                    "text": "Bullet text",
                                    "tag": "ALERT",
                                    "claim_ids": ["sec_1_C01"],
                                }
                            ]
                        },
                    ),
                    Block(kind="callout", payload={"text": "Callout note"}),
                    Block(kind="qa_item", payload={"question": "Q?", "answer": "A."}),
                ],
            )
        ],
    )


def test_markdown_escapes_tables_and_renders_badges():
    markdown = MarkdownRenderer(make_doc()).render()
    assert r"A\|B" in markdown
    assert "C<br>D" in markdown
    assert r"x\|y" in markdown
    assert "**[ALERT]** Bullet text" in markdown
    assert "> Callout note" in markdown
    assert "**Q:** Q?" in markdown


def test_docx_full_width_table_uses_section_breaks(tmp_path):
    output = tmp_path / "doc.docx"
    theme = ThemeManager().get_theme_for_spec()
    DocxRenderer(make_doc(), theme=theme).render(output)

    xml = zipfile.ZipFile(output).read("word/document.xml").decode("utf-8")
    column_counts = re.findall(r'<w:cols[^>]*w:num="(\d)"', xml)
    assert column_counts[0] == "2"
    assert "1" in column_counts
    assert column_counts[-1] == "2"

    document = docx.Document(str(output))
    assert len(document.tables) >= 2


def test_docx_handles_missing_review_and_empty_sections(tmp_path):
    doc = TransDocument(
        doc_id="doc_2", title="Minimal", subject=SubjectSpec(name="Minimal")
    )
    output = tmp_path / "minimal.docx"
    DocxRenderer(doc, theme=ThemeManager().get_theme_for_spec()).render(output)
    assert output.exists() and output.stat().st_size > 0


def test_docx_academic_chrome_and_badges(tmp_path):
    doc = TransDocument(
        doc_id="doc_3",
        title="Principles of Bioethics",
        subject=SubjectSpec(
            code="[BIOET]",
            name="Bioethics",
            batch="Batch 2030",
            term="Prelim / Week 4",
            lecturer="Dr. Milagros F. Neri",
        ),
        sections=[
            Section(
                id="sec_1",
                title="Principle of Beneficence",
                blocks=[
                    Block(
                        kind="bullets",
                        payload={
                            "items": [
                                {
                                    "level": 1,
                                    "text": "Beneficence: To do good or provide benefit.",
                                    "tag": "SPOKEN",
                                },
                                {
                                    "level": 2,
                                    "text": "Exam favorite: Remember proportionality.",
                                    "tag": "REMEMBER",
                                },
                            ]
                        },
                    )
                ],
            )
        ],
    )
    output = tmp_path / "bioethics.docx"
    theme = ThemeManager().get_theme_for_spec(doc.subject)
    DocxRenderer(doc, theme=theme).render(output)

    d = docx.Document(str(output))
    # Section has 2 columns throughout lecture body
    cols = d.sections[0]._sectPr.xpath("./w:cols")
    assert cols[0].get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}num") == "2"

    # Minimal header with academic metadata (no bulky tables)
    header_texts = [p.text for p in d.sections[0].header.paragraphs if p.text.strip()]
    assert any("BIOET" in t for t in header_texts)
    assert any("Batch 2030" in t for t in header_texts)
    assert any("Dr. Milagros F. Neri" in t for t in header_texts)
    assert len(d.sections[0].header.tables) == 0

    # Minimal footer with right-aligned page number
    footer_texts = [p.text for p in d.sections[0].footer.paragraphs if p.text.strip()]
    assert any("Page" in t for t in footer_texts)
    assert len(d.sections[0].footer.tables) == 0

    # Badges and key-terms formatted
    texts = [p.text for p in d.paragraphs if p.text.strip()]
    assert any("🗣 SPOKEN:" in t for t in texts)
    assert any("❗ REMEMBER:" in t for t in texts)
    assert any("Beneficence:" in t for t in texts)
