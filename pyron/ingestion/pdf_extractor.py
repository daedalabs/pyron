from __future__ import annotations

import hashlib
import re
from typing import Any

from pyron.api.contracts import EvidenceSpan, Source, SourceKind
from pyron.api.errors import InputError
from pyron.ingestion.base import BaseExtractor

try:  # PyMuPDF is optional (AGPL); see pyproject extras.
    import pymupdf
except ImportError:  # pragma: no cover - depends on install extras
    pymupdf = None


# Header/footer signatures that are safe to drop. These require a distinctive
# shape (colon, page number, batch banner) so ordinary content lines that merely
# start with "Week 3" or "Finals" are preserved.
_HEADER_PATTERNS = (
    re.compile(
        r"^(?:trans\s*head|trans\s*group\s*leader|group\s*leader|members?|"
        r"lecturers?|instructors?|editors?)\s*:",
        re.IGNORECASE,
    ),
    re.compile(r"^page\s+\d+\s*(?:of\s*\d+)?$", re.IGNORECASE),
    re.compile(r"^\d+\s*(?:of\s*\d+)?$"),
    re.compile(r"^\[[A-Z]{2,6}\]\s.*\bBatch\s+\d+", re.IGNORECASE),
)
_TERM_ONLY = re.compile(
    r"^(?:prelims?|midterms?|finals?)(?:\s*(?:/|-|–|—)\s*(?:trans(?:es)?|week\s*\d+))?\s*$",
    re.IGNORECASE,
)
_WEEK_ONLY = re.compile(r"^week\s*\d+\s*(?:of\s*\d+)?\s*$", re.IGNORECASE)
_BATCH_HEADER = re.compile(r"^[A-Z][A-Z\s&/]{3,}\s+Batch\s+\d+", re.IGNORECASE)
_BULLET = re.compile(
    r"^\s*(?:[•▪●○◦‣·■□▸▶]|[-–—]\s|\d{1,2}[.)]\s|\(\w\)\s|[IVXLC]{1,5}\.\s|[a-zA-Z]\.\s)\s*"
)
_MOJIBAKE_HINT = re.compile(r"(?:â€|ðŸ|Ã[\x80-\xBF])")
_INVISIBLE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]")
_LEADING_MARKER = re.compile(r"^\s*(?:[•▪●○◦‣·■□▸▶]\s*|[-–—]\s+)")


def _repair_bytes(data: bytes) -> str:
    """Decode mixed valid/truncated UTF-8, preserving what can be decoded."""
    output: list[str] = []
    while data:
        try:
            output.append(data.decode("utf-8"))
            break
        except UnicodeDecodeError as error:
            if error.start > 0:
                output.append(data[: error.start].decode("utf-8"))
            end = error.end if error.end > error.start else error.start + 1
            output.append(data[error.start : end].decode("cp1252", errors="ignore"))
            data = data[end:]
    return "".join(output)


def _fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was decoded as a single-byte codepage.

    Repairs each contiguous run that is representable in cp1252, so a string
    that also contains genuine Unicode characters is still repaired where
    possible. Truncated trailing sequences are dropped rather than poisoning
    the whole string.
    """
    if not _MOJIBAKE_HINT.search(text):
        return text

    output: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if not run:
            return
        chunk = "".join(run)
        try:
            output.append(_repair_bytes(chunk.encode("cp1252")))
        except UnicodeEncodeError:
            output.append(chunk)
        run.clear()

    for char in text:
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            flush()
            output.append(char)
        else:
            run.append(char)
    flush()
    return "".join(output)


_LEFTOVER_MOJIBAKE = re.compile(r"(?:â€[^\s]{0,2}|ðŸ[^\s]{0,3})")


def _clean_text(text: str) -> str:
    text = _fix_mojibake(text)
    text = _LEFTOVER_MOJIBAKE.sub("", text)
    text = _EMOJI.sub("", text)
    text = _INVISIBLE.sub("", text).replace("\xa0", " ")
    text = _LEADING_MARKER.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_page_header(line: str) -> bool:
    if any(pattern.match(line) for pattern in _HEADER_PATTERNS):
        return True
    if _BATCH_HEADER.match(line):
        return True
    return len(line) <= 60 and bool(_TERM_ONLY.match(line) or _WEEK_ONLY.match(line))


def _ordered_blocks(page: Any) -> list[str]:
    """Return page text blocks in human reading order (left column first)."""
    raw_blocks = page.get_text("blocks")
    blocks = [b for b in raw_blocks if len(b) >= 7 and b[6] == 0 and str(b[4]).strip()]
    if not blocks:
        return []

    page_width = page.rect.width or 1.0
    wide = [b for b in blocks if (b[2] - b[0]) > page_width * 0.65]
    midpoint = page_width / 2
    left = [b for b in blocks if b[2] <= midpoint * 1.02]
    right = [b for b in blocks if b[0] >= midpoint * 0.95]

    if len(left) >= 2 and len(right) >= 2 and len(wide) <= max(1, len(blocks) // 6):
        ordered = (
            sorted(wide, key=lambda b: (b[1], b[0]))
            + sorted(left, key=lambda b: (b[1], b[0]))
            + sorted(right, key=lambda b: (b[1], b[0]))
        )
    else:
        ordered = sorted(blocks, key=lambda b: (round(b[1], 1), b[0]))
    return [str(block[4]).strip() for block in ordered]


def _split_items(block_text: str) -> list[str]:
    """Turn one hard-wrapped PDF block into paragraph/bullet-sized items."""
    lines = [line.strip() for line in block_text.splitlines() if line.strip()]
    if not lines:
        return []

    items: list[str] = []
    buffer: list[str] = []
    for index, line in enumerate(lines):
        next_is_bullet = index + 1 < len(lines) and bool(_BULLET.match(lines[index + 1]))
        starts_item = bool(_BULLET.match(line)) or (
            len(line) < 60 and next_is_bullet and not line.endswith((",", ";", "."))
        )
        if starts_item:
            if buffer:
                items.append(" ".join(buffer))
            buffer = [_BULLET.sub("", line).strip()]
        else:
            buffer.append(line)
    if buffer:
        items.append(" ".join(buffer))

    cleaned = []
    for item in items:
        item = _clean_text(item)
        if len(item) > 2:
            cleaned.append(item)
    return cleaned


def _page_items(page: Any) -> list[str]:
    items: list[str] = []
    for block in _ordered_blocks(page):
        kept_lines = [
            line.strip()
            for line in block.splitlines()
            if line.strip() and not _is_page_header(line.strip())
        ]
        if not kept_lines:
            continue
        items.extend(_split_items("\n".join(kept_lines)))
    return items


class PDFExtractor(BaseExtractor):
    def can_handle(self, source: Source) -> bool:
        if source.kind in (SourceKind.SLIDES, SourceKind.TEXTBOOK, SourceKind.NOTES):
            if source.path and source.path.suffix.lower() == ".pdf":
                return True
            if source.data and source.data.startswith(b"%PDF"):
                return True
            if source.media_type == "application/pdf":
                return True
        return False

    def extract(self, source: Source) -> list[EvidenceSpan]:
        if pymupdf is None:
            raise InputError(
                "PDF support requires PyMuPDF. Install it with "
                '`pip install "pyron[pdf]"`, or pass slides as .pptx.'
            )

        if source.path and source.path.exists():
            raw = source.path.read_bytes()
            document = pymupdf.open(stream=raw, filetype="pdf")
        elif source.data:
            raw = source.data
            document = pymupdf.open(stream=raw, filetype="pdf")
        else:
            return []

        doc_sha = hashlib.sha256(raw).hexdigest()
        spans: list[EvidenceSpan] = []
        try:
            for page_index in range(len(document)):
                page = document[page_index]
                items = _page_items(page)
                slide_num = page_index + 1
                for item_index, text in enumerate(items, start=1):
                    if len(text) < 4:
                        continue
                    spans.append(
                        EvidenceSpan(
                            eid=f"E_{doc_sha[:6]}_P{slide_num}_{item_index}",
                            kind=source.kind,
                            doc_sha=doc_sha,
                            char_span=(0, len(text)),
                            locator={
                                "slide_no" if source.kind == SourceKind.SLIDES else "page": slide_num,
                                "item": item_index,
                            },
                            text=text,
                            confidence=1.0,
                        )
                    )

                # Extract embedded images from the page
                page_imgs = page.get_images(full=True)
                page_title = items[0] if items else f"Slide {slide_num}"
                for img_idx, img_info in enumerate(page_imgs, start=1):
                    xref = img_info[0]
                    try:
                        base_img = document.extract_image(xref)
                        img_bytes = base_img.get("image", b"")
                        if len(img_bytes) >= 150:
                            ext = (base_img.get("ext", "png") or "png").lower()
                            if ext == "jpeg":
                                ext = "jpg"
                            img_filename = f"slide_{slide_num:02d}_fig_{img_idx:02d}.{ext}"
                            fig_eid = f"E_{doc_sha[:6]}_F{slide_num}_{img_idx}"
                            caption = f"Slide {slide_num}: {page_title}"
                            spans.append(
                                EvidenceSpan(
                                    eid=fig_eid,
                                    kind=SourceKind.FIGURE,
                                    doc_sha=doc_sha,
                                    char_span=(0, 0),
                                    locator={
                                        "slide_no" if source.kind == SourceKind.SLIDES else "page": slide_num,
                                        "image_index": img_idx,
                                        "filename": img_filename,
                                    },
                                    text=f"[Figure from {caption}]",
                                    confidence=1.0,
                                    media_path=f"images/{img_filename}",
                                    media_bytes=img_bytes,
                                )
                            )
                    except Exception:
                        pass
        finally:
            document.close()
        return spans
