from __future__ import annotations

import asyncio
import io
import zipfile

from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from pyron.api.contracts import (
    Block,
    BlockWidth,
    GenerateOptions,
    GenerateRequest,
    Section,
    Source,
    SourceKind,
    SubjectSpec,
    TransDocument,
)
from pyron.engine import TransEngine
from pyron.ingestion.pdf_extractor import PDFExtractor
from pyron.ingestion.pptx_extractor import PPTXExtractor
from tests.helpers import FakeProvider


def _create_sample_png() -> bytes:
    img = Image.new("RGB", (100, 100), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_sample_pptx_with_image() -> bytes:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # Add title text box
    tx_box = slide.shapes.add_textbox(Inches(1), Inches(0.5), Inches(5), Inches(1))
    tx_box.text_frame.text = "Distributed Consensus Architecture"

    # Add picture
    img_data = _create_sample_png()
    slide.shapes.add_picture(
        io.BytesIO(img_data), Inches(1), Inches(1.5), Inches(3), Inches(2)
    )

    out_buf = io.BytesIO()
    prs.save(out_buf)
    return out_buf.getvalue()


def test_pptx_extractor_extracts_embedded_images():
    pptx_bytes = _create_sample_pptx_with_image()
    source = Source(kind=SourceKind.SLIDES, data=pptx_bytes)
    spans = PPTXExtractor().extract(source)

    # Should have 1 slide text span and 1 figure span
    assert len(spans) == 2
    slide_span = next(s for s in spans if s.kind == SourceKind.SLIDES)
    figure_span = next(s for s in spans if s.kind == SourceKind.FIGURE)

    assert "Distributed Consensus" in slide_span.text
    assert figure_span.media_path == "images/slide_01_fig_01.png"
    assert figure_span.media_bytes is not None
    assert len(figure_span.media_bytes) > 100
    assert "Distributed Consensus" in figure_span.text


def test_pdf_extractor_extracts_embedded_images():
    pymupdf = __import__("pymupdf")
    img_data = _create_sample_png()

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "PDF Slide Heading", fontsize=12)
    rect = pymupdf.Rect(50, 100, 200, 250)
    page.insert_image(rect, stream=img_data)
    pdf_bytes = doc.tobytes()
    doc.close()

    source = Source(kind=SourceKind.SLIDES, data=pdf_bytes)
    spans = PDFExtractor().extract(source)

    figure_spans = [s for s in spans if s.kind == SourceKind.FIGURE]
    assert len(figure_spans) == 1
    assert figure_spans[0].media_path == "images/slide_01_fig_01.png"
    assert figure_spans[0].media_bytes is not None
    assert len(figure_spans[0].media_bytes) > 100


def test_pipeline_places_reference_images_and_stores_assets():
    pptx_bytes = _create_sample_pptx_with_image()
    req = GenerateRequest(
        subject=SubjectSpec(code="[CS401]", name="Distributed Systems"),
        title="Consensus Protocol",
        sources=[Source(kind=SourceKind.SLIDES, data=pptx_bytes)],
        options=GenerateOptions(max_concurrency=2),
    )

    provider = FakeProvider(
        outline={
            "title": "Consensus Protocol",
            "outline": [
                {"id": "sec_1", "numeral": "I", "title": "Consensus Architecture", "level": 1}
            ],
        },
        sections=lambda sid, ids, prompt: {
            "claims": [
                {
                    "cid": "C1",
                    "text": "Distributed consensus is achieved.",
                    "evidence": [ids[0]],
                }
            ],
            "blocks": [
                {
                    "kind": "bullets",
                    "payload": {
                        "items": [
                            {
                                "level": 1,
                                "text": "Distributed consensus is achieved.",
                                "claim_ids": ["C1"],
                            }
                        ]
                    },
                }
            ],
        },
    )

    engine = TransEngine.create(ai_provider=provider)
    doc = asyncio.run(engine.generate(req))

    # Verify document assets contain the image bytes
    assert doc.assets
    assert any(k.startswith("images/slide_01_fig_") for k in doc.assets)

    # Verify the figure block was automatically placed into section 1
    sec1 = doc.sections[0]
    figure_blocks = [b for b in sec1.blocks if b.kind == "figure"]
    assert len(figure_blocks) == 1
    assert figure_blocks[0].payload["image_path"].startswith("images/slide_01_fig_")
    assert "Consensus" in figure_blocks[0].payload["caption"]


def test_render_and_save_bundle_with_images(tmp_path):
    img_data = _create_sample_png()
    doc = TransDocument(
        doc_id="img_test_1",
        title="Image Render Test",
        subject=SubjectSpec(name="Computer Science"),
        assets={"images/test_diagram.png": img_data},
        sections=[
            Section(
                id="sec_1",
                title="Architecture",
                blocks=[
                    Block(
                        kind="figure",
                        width=BlockWidth.COLUMN,
                        payload={
                            "image_path": "images/test_diagram.png",
                            "caption": "Figure 1: High Level System Architecture",
                        },
                    )
                ],
            )
        ],
    )

    engine = TransEngine()
    bundle = engine.save_bundle(doc, output_dir=tmp_path / "bundle")

    # 1. Image saved to bundle/images/
    saved_img = tmp_path / "bundle" / "images" / "test_diagram.png"
    assert saved_img.exists()
    assert saved_img.read_bytes() == img_data

    # 2. Markdown has proper image tag and caption
    md_content = bundle.doc_md.read_text(encoding="utf-8")
    assert "![Figure 1: High Level System Architecture](images/test_diagram.png)" in md_content
    assert "*Figure 1: High Level System Architecture*" in md_content

    # 3. Word document has the image embedded inside its zip package
    docx_zip = zipfile.ZipFile(bundle.doc_docx)
    media_files = [f for f in docx_zip.namelist() if f.startswith("word/media/")]
    assert len(media_files) >= 1


def test_structured_json_schema_passed_to_provider():
    provider = FakeProvider()
    req = GenerateRequest(
        subject=SubjectSpec(code="[TEST1]", name="Testing"),
        title="Schema Test",
        sources=[
            Source(
                kind=SourceKind.NOTES,
                data=b"Fact 1: Threads share memory.\nFact 2: Processes isolate memory.",
            )
        ],
    )
    engine = TransEngine.create(ai_provider=provider)
    asyncio.run(engine.generate(req))

    # All calls to the provider must have response_json_schema populated
    assert provider.calls
    for call in provider.calls:
        assert call.response_json_schema is not None
        assert "properties" in call.response_json_schema
        assert "type" in call.response_json_schema
