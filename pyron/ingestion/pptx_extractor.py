from __future__ import annotations

import hashlib
from io import BytesIO

from pptx import Presentation

from pyron.api.contracts import EvidenceSpan, Source, SourceKind
from pyron.ingestion.base import BaseExtractor


def _shape_text(shape) -> list[str]:
    """Collect text from a shape, including tables (which have no text frame)."""
    parts: list[str] = []
    if getattr(shape, "has_text_frame", False):
        for paragraph in shape.text_frame.paragraphs:
            text = paragraph.text.strip()
            if text:
                parts.append(text)
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    if getattr(shape, "shape_type", None) is not None and getattr(shape, "shapes", None):
        # Grouped shapes: recurse so nested text is not lost.
        for child in shape.shapes:
            parts.extend(_shape_text(child))
    return parts


def _extract_shape_images(shape) -> list[tuple[bytes, str, str]]:
    """Extract embedded images from a shape or group of shapes.

    Returns list of (image_bytes, ext, shape_name).
    Filters out tiny 1x1 decorative spacer pixels (< 150 bytes).
    """
    results: list[tuple[bytes, str, str]] = []
    if hasattr(shape, "image"):
        try:
            image = shape.image
            blob = image.blob
            if len(blob) >= 150:
                ext = (getattr(image, "ext", "png") or "png").lower()
                if ext == "jpeg":
                    ext = "jpg"
                name = getattr(shape, "name", "picture") or "picture"
                results.append((blob, ext, name))
        except Exception:
            pass
    elif getattr(shape, "shape_type", None) is not None and getattr(shape, "shapes", None):
        for child in shape.shapes:
            results.extend(_extract_shape_images(child))
    return results


class PPTXExtractor(BaseExtractor):
    def can_handle(self, source: Source) -> bool:
        if source.path is not None:
            return source.path.suffix.lower() == ".pptx"
        if source.kind == SourceKind.SLIDES:
            if source.data and source.data.startswith(b"PK\x03\x04"):
                return True
            return source.media_type in (
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        return False

    def extract(self, source: Source) -> list[EvidenceSpan]:
        if source.path and source.path.exists():
            raw = source.path.read_bytes()
            presentation = Presentation(BytesIO(raw))
        elif source.data:
            raw = source.data
            presentation = Presentation(BytesIO(raw))
        else:
            return []

        doc_sha = hashlib.sha256(raw).hexdigest()
        spans: list[EvidenceSpan] = []
        for index, slide in enumerate(presentation.slides, start=1):
            parts: list[str] = []
            slide_images: list[tuple[bytes, str, str]] = []
            for shape in slide.shapes:
                parts.extend(_shape_text(shape))
                slide_images.extend(_extract_shape_images(shape))

            text = "\n".join(parts).strip()
            slide_title = parts[0] if parts else f"Slide {index}"
            if text:
                spans.append(
                    EvidenceSpan(
                        eid=f"E_{doc_sha[:6]}_S{index}",
                        kind=SourceKind.SLIDES,
                        doc_sha=doc_sha,
                        char_span=(0, len(text)),
                        locator={"slide_no": index},
                        text=text,
                        confidence=1.0,
                    )
                )

            # Figures / images embedded in the slide
            for img_idx, (blob, ext, _shape_name) in enumerate(slide_images, start=1):
                img_filename = f"slide_{index:02d}_fig_{img_idx:02d}.{ext}"
                fig_eid = f"E_{doc_sha[:6]}_F{index}_{img_idx}"
                caption = f"Slide {index}: {slide_title}"
                spans.append(
                    EvidenceSpan(
                        eid=fig_eid,
                        kind=SourceKind.FIGURE,
                        doc_sha=doc_sha,
                        char_span=(0, 0),
                        locator={
                            "slide_no": index,
                            "image_index": img_idx,
                            "filename": img_filename,
                        },
                        text=f"[Figure from {caption}]",
                        confidence=1.0,
                        media_path=f"images/{img_filename}",
                        media_bytes=blob,
                    )
                )

            # Speaker notes are authored reference material, not slide content.
            # Keeping them separate stops them becoming section titles and lets
            # them earn a reference badge instead of silently passing as slides.
            notes = ""
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                spans.append(
                    EvidenceSpan(
                        eid=f"E_{doc_sha[:6]}_N{index:03d}",
                        kind=SourceKind.NOTES,
                        doc_sha=doc_sha,
                        char_span=(0, len(notes)),
                        locator={"slide_no": index, "speaker_notes": True},
                        text=notes,
                        confidence=1.0,
                    )
                )
        return spans
