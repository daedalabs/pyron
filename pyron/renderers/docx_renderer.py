from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path
from typing import Any

import docx
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches, Pt, RGBColor

from pyron.api.contracts import Block, BlockWidth, TransDocument, VerifiedClaim
from pyron.theming.theme_manager import Theme


def _hex_to_rgb_color(hex_str: str) -> RGBColor:
    value = hex_str.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _set_cell_background(cell, hex_color: str) -> None:
    clean_hex = hex_color.lstrip("#").upper()
    cell._tc.get_or_add_tcPr().append(
        parse_xml(f'<w:shd {nsdecls("w")} w:fill="{clean_hex}"/>')
    )


def _set_cell_margins(cell, top=60, bottom=60, left=80, right=80) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_pr.append(
        parse_xml(
            f'<w:tcMar {nsdecls("w")}>'
            f'<w:top w:w="{top}" w:type="dxa"/>'
            f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
            f'<w:left w:w="{left}" w:type="dxa"/>'
            f'<w:right w:w="{right}" w:type="dxa"/>'
            f"</w:tcMar>"
        )
    )


def _set_table_borders(table, color="CCCCCC", size="4") -> None:
    table._tbl.tblPr.append(
        parse_xml(
            f'<w:tblBorders {nsdecls("w")}>'
            f'<w:top w:val="single" w:sz="{size}" w:space="0" w:color="{color}"/>'
            f'<w:bottom w:val="single" w:sz="{size}" w:space="0" w:color="{color}"/>'
            f'<w:left w:val="none"/>'
            f'<w:right w:val="none"/>'
            f'<w:insideH w:val="single" w:sz="{size}" w:space="0" w:color="{color}"/>'
            f'<w:insideV w:val="none"/>'
            f"</w:tblBorders>"
        )
    )


def _no_split_rows(table) -> None:
    for row in table.rows:
        tr_pr = row._tr.get_or_add_trPr()
        if tr_pr.find(qn("w:cantSplit")) is None:
            tr_pr.append(OxmlElement("w:cantSplit"))


# Semantic badge metadata: (Badge Text, RGB Color)
BADGE_CHIPS: dict[str, tuple[str, RGBColor]] = {
    "SPOKEN": ("🗣 SPOKEN: ", RGBColor(30, 64, 175)),      # Royal Blue
    "REMEMBER": ("❗ REMEMBER: ", RGBColor(180, 83, 9)),   # Warm Amber
    "ALERT": ("⚠️ ALERT: ", RGBColor(220, 38, 38)),        # Crimson Red
    "MNEMONIC": ("💡 MNEMONIC: ", RGBColor(124, 58, 237)), # Violet
    "TEXTBOOK": ("📖 REF: ", RGBColor(22, 101, 52)),       # Forest Green
    "REFERENCE": ("📖 REF: ", RGBColor(22, 101, 52)),      # Forest Green
    "QUESTION": ("❓ QUESTION: ", RGBColor(13, 148, 136)),  # Teal
}

# Regex to strip raw leading tags so we don't repeat "[SPOKEN] 🗣 SPOKEN:"
STRIP_LEADING_TAGS_RE = re.compile(
    r"^(?:\[(?:SPOKEN|REMEMBER|ALERT|MNEMONIC|REFERENCE|TEXTBOOK|QUESTION)\]|🗣|❗|⚠️|💡|📖|❓)\s*",
    re.IGNORECASE,
)


class DocxRenderer:
    def __init__(self, doc_model: TransDocument, theme: Theme):
        self.doc_model = doc_model
        self.theme = theme
        self.doc = docx.Document()
        self.primary_rgb = _hex_to_rgb_color(theme.primary)
        self.secondary_rgb = _hex_to_rgb_color(theme.secondary)
        self.on_primary_rgb = _hex_to_rgb_color(theme.on_primary)

    def render(self, output_path: Path | str) -> Path:
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Standard academic 2-column layout throughout the body
        self._setup_page_and_chrome(initial_columns=2)
        self._render_title_and_metadata()
        if self.doc_model.outline:
            self._render_topic_outline()
        self._render_sections()
        self._render_review_questions()

        self.doc.save(str(out_path))
        return out_path

    # ------------------------------------------------------------------ chrome

    def _setup_page_and_chrome(self, initial_columns: int = 2) -> None:
        section = self.doc.sections[0]
        # Precise A4 page dimensions matching reference PDFs
        section.page_width = Inches(8.27)
        section.page_height = Inches(11.69)
        section.top_margin = Inches(0.40)
        section.bottom_margin = Inches(0.40)
        section.left_margin = Inches(0.50)
        section.right_margin = Inches(0.354)
        section.header_distance = Inches(0.18)
        section.footer_distance = Inches(0.18)

        self._render_header(section)
        self._render_footer(section)
        self._set_columns(section, initial_columns)

    @staticmethod
    def _set_columns(section, count: int) -> None:
        columns = section._sectPr.xpath("./w:cols")
        if columns:
            columns[0].set(qn("w:num"), str(count))
            columns[0].set(qn("w:space"), "510")  # 25.5 pt gutter

    def _begin_full_width(self) -> None:
        section = self.doc.add_section(WD_SECTION.CONTINUOUS)
        section.top_margin = Inches(0.40)
        section.bottom_margin = Inches(0.40)
        section.left_margin = Inches(0.50)
        section.right_margin = Inches(0.354)
        self._set_columns(section, 1)

    def _end_full_width(self) -> None:
        section = self.doc.add_section(WD_SECTION.CONTINUOUS)
        section.top_margin = Inches(0.40)
        section.bottom_margin = Inches(0.40)
        section.left_margin = Inches(0.50)
        section.right_margin = Inches(0.354)
        self._set_columns(section, 2)

    def _render_header(self, section) -> None:
        header = section.header
        header.is_linked_to_previous = False

        # Check if an actual logo image exists in assets
        logo_bytes: bytes | None = None
        if hasattr(self.doc_model, "assets") and self.doc_model.assets:
            for k, b in self.doc_model.assets.items():
                if any(m in k.lower() for m in ("logo", "seal", "crest")):
                    logo_bytes = b
                    break

        subject = self.doc_model.subject
        subj_title = (
            f"{subject.code} {subject.name.upper()}".strip()
            if subject.code or subject.name
            else self.doc_model.title.upper()
        )
        if subject.session_type:
            subj_title += f" [{subject.session_type}]"

        batch_text = subject.batch or ""

        term_parts = [
            p
            for p in [
                subject.term,
                f"Week {subject.week}" if subject.week else "",
                subject.date_str or "",
            ]
            if p
        ]
        term_text = " / ".join(term_parts) if term_parts else ""

        lecturer_text = f"Lecturer: {subject.lecturer}" if subject.lecturer else ""
        if lecturer_text and subject.credentials:
            lecturer_text += f", {subject.credentials}"

        if logo_bytes:
            # Compact 2-column layout only when an actual logo image is provided
            table = header.add_table(rows=1, cols=2, width=Inches(7.414))
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            table.autofit = False
            _no_split_rows(table)
            table.columns[0].width = Inches(0.70)
            table.columns[1].width = Inches(6.714)

            tblPr = table._tbl.tblPr
            tblPr.append(parse_xml(f'<w:tblW {nsdecls("w")} w:w="10676" w:type="dxa"/>'))
            tblPr.append(parse_xml(f'<w:tblLayout {nsdecls("w")} w:type="fixed"/>'))

            c_logo = table.cell(0, 0)
            c_meta = table.cell(0, 1)
            c_logo._tc.get_or_add_tcPr().append(
                parse_xml(f'<w:tcW {nsdecls("w")} w:w="1008" w:type="dxa"/>')
            )
            c_meta._tc.get_or_add_tcPr().append(
                parse_xml(f'<w:tcW {nsdecls("w")} w:w="9668" w:type="dxa"/>')
            )
            _set_cell_margins(c_logo, top=0, bottom=0, left=0, right=20)
            _set_cell_margins(c_meta, top=0, bottom=0, left=20, right=0)

            p_logo = c_logo.paragraphs[0]
            p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_logo.paragraph_format.space_before = Pt(0)
            p_logo.paragraph_format.space_after = Pt(0)
            with contextlib.suppress(Exception):
                p_logo.add_run().add_picture(io.BytesIO(logo_bytes), width=Inches(0.60))

            self._populate_header_paragraphs(
                c_meta.paragraphs[0],
                add_paragraph_fn=c_meta.add_paragraph,
                subj_title=subj_title,
                batch_text=batch_text,
                term_text=term_text,
                lecturer_text=lecturer_text,
                right_pos="9668",
            )
        else:
            # Clean, minimal text header spanning full page width (no tables, no empty paragraphs)
            self._populate_header_paragraphs(
                header.paragraphs[0],
                add_paragraph_fn=header.add_paragraph,
                subj_title=subj_title,
                batch_text=batch_text,
                term_text=term_text,
                lecturer_text=lecturer_text,
                right_pos="10676",
            )

    def _populate_header_paragraphs(
        self,
        p1,
        add_paragraph_fn,
        subj_title: str,
        batch_text: str,
        term_text: str,
        lecturer_text: str,
        right_pos: str = "10676",
    ) -> None:
        # Line 1: Subject (Left) + Batch (Right) on the exact same single line
        p1.paragraph_format.space_before = Pt(0)
        p1.paragraph_format.space_after = Pt(0)
        p1.paragraph_format.line_spacing = 1.05

        pPr = p1._p.get_or_add_pPr()
        pPr.append(
            parse_xml(
                f'<w:tabs {nsdecls("w")}><w:tab w:val="right" w:pos="{right_pos}"/></w:tabs>'
            )
        )

        r1 = p1.add_run(subj_title)
        r1.font.bold = True
        r1.font.size = Pt(10.0)
        r1.font.name = "Arial"
        r1.font.color.rgb = self.primary_rgb

        if batch_text:
            p1.add_run("\t")
            r_batch = p1.add_run(batch_text)
            r_batch.font.bold = True
            r_batch.font.size = Pt(8.5)
            r_batch.font.name = "Arial"
            r_batch.font.color.rgb = self.secondary_rgb

        last_p = p1

        # Line 2: Term / Week / Date (if present)
        if term_text:
            p2 = add_paragraph_fn()
            p2.paragraph_format.space_before = Pt(0)
            p2.paragraph_format.space_after = Pt(0)
            p2.paragraph_format.line_spacing = 1.05
            r_term = p2.add_run(term_text)
            r_term.font.bold = True
            r_term.font.size = Pt(8.5)
            r_term.font.name = "Arial"
            r_term.font.color.rgb = self.secondary_rgb
            last_p = p2

        # Line 3: Lecturer (if present)
        if lecturer_text:
            p3 = add_paragraph_fn()
            p3.paragraph_format.space_before = Pt(0)
            p3.paragraph_format.space_after = Pt(0)
            p3.paragraph_format.line_spacing = 1.05
            r_lec = p3.add_run(lecturer_text)
            r_lec.font.size = Pt(8.0)
            r_lec.font.name = "Arial"
            r_lec.font.color.rgb = RGBColor(0x33, 0x41, 0x55)
            last_p = p3

        # Add crisp, slim bottom dividing rule directly onto the last header line
        clean_hex = self.theme.primary.lstrip("#").upper()
        last_p.paragraph_format.space_after = Pt(2)
        last_p._p.get_or_add_pPr().append(
            parse_xml(
                f'<w:pBdr {nsdecls("w")}>'
                f'  <w:bottom w:val="single" w:sz="6" w:space="2" w:color="{clean_hex}"/>'
                f"</w:pBdr>"
            )
        )

    def _render_footer(self, section) -> None:
        # Minimal right-aligned page number: "Page X of Y"
        footer = section.footer
        footer.is_linked_to_previous = False
        p = footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)

        r1 = p.add_run("Page ")
        r1.font.size = Pt(8.0)
        r1.font.name = "Arial"
        r1.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)

        p._p.append(parse_xml(f'<w:fldSimple {nsdecls("w")} w:instr="PAGE"/>'))

        r2 = p.add_run(" of ")
        r2.font.size = Pt(8.0)
        r2.font.name = "Arial"
        r2.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)

        p._p.append(parse_xml(f'<w:fldSimple {nsdecls("w")} w:instr="NUMPAGES"/>'))

    # ------------------------------------------------------------------- title

    def _render_title_and_metadata(self) -> None:
        # Title sits at the top of the Left Column, left-aligned, matching reference PDFs
        paragraph = self.doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(3)
        paragraph.paragraph_format.keep_with_next = True
        run = paragraph.add_run(self.doc_model.title)
        run.font.bold = True
        run.font.size = Pt(17)
        run.font.name = "Arial"
        run.font.color.rgb = self.primary_rgb

    def _add_banner_pill(
        self, text: str, full_width: bool = False, is_subbanner: bool = False
    ) -> None:
        table = self.doc.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False

        width_in = Inches(7.41) if full_width else Inches(3.53)
        width_dxa = "10676" if full_width else "5083"
        table.columns[0].width = width_in

        tblPr = table._tbl.tblPr
        tblPr.append(parse_xml(f'<w:tblW {nsdecls("w")} w:w="{width_dxa}" w:type="dxa"/>'))
        tblPr.append(parse_xml(f'<w:tblLayout {nsdecls("w")} w:type="fixed"/>'))

        cell = table.cell(0, 0)
        tcPr = cell._tc.get_or_add_tcPr()
        tcPr.append(parse_xml(f'<w:tcW {nsdecls("w")} w:w="{width_dxa}" w:type="dxa"/>'))

        bg_hex = self.theme.secondary if is_subbanner else self.theme.primary
        text_color = self.on_primary_rgb
        _set_cell_background(cell, bg_hex)
        _set_cell_margins(cell, top=35, bottom=35, left=60, right=60)
        _no_split_rows(table)

        paragraph = cell.paragraphs[0]
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.keep_with_next = True
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(text.upper())
        run.font.bold = True
        run.font.size = Pt(8.5)
        run.font.name = "Arial"
        run.font.color.rgb = text_color

        # Subtle breathing space after banner table
        spacer = self.doc.add_paragraph()
        spacer.paragraph_format.space_before = Pt(1)
        spacer.paragraph_format.space_after = Pt(2)
        spacer.paragraph_format.keep_with_next = True

    def _render_topic_outline(self) -> None:
        if not self.doc_model.outline:
            return

        # Topic outline banner is column-width, sitting right below the title in Column 1
        self._add_banner_pill("TOPIC OUTLINE", full_width=False)

        for node in self.doc_model.outline:
            p = self.doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.18)
            p.paragraph_format.first_line_indent = Inches(-0.18)
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(1.5)
            p.paragraph_format.line_spacing = 1.12
            p.paragraph_format.keep_with_next = True

            num_run = p.add_run(f"{node.numeral}. ")
            num_run.font.bold = True
            num_run.font.size = Pt(8.5)
            num_run.font.name = "Arial"
            num_run.font.color.rgb = self.primary_rgb

            title_run = p.add_run(node.title)
            title_run.font.size = Pt(8.5)
            title_run.font.name = "Arial"

            if node.children:
                for child in node.children:
                    cp = self.doc.add_paragraph()
                    cp.paragraph_format.left_indent = Inches(0.36)
                    cp.paragraph_format.first_line_indent = Inches(-0.18)
                    cp.paragraph_format.space_before = Pt(0)
                    cp.paragraph_format.space_after = Pt(1)
                    cp.paragraph_format.line_spacing = 1.1

                    cnum_run = cp.add_run(f"{child.numeral}. ")
                    cnum_run.font.bold = True
                    cnum_run.font.size = Pt(8.0)
                    cnum_run.font.name = "Arial"
                    cnum_run.font.color.rgb = self.secondary_rgb

                    ctitle_run = cp.add_run(child.title)
                    ctitle_run.font.size = Pt(8.0)
                    ctitle_run.font.name = "Arial"

        spacer = self.doc.add_paragraph()
        spacer.paragraph_format.space_before = Pt(1)
        spacer.paragraph_format.space_after = Pt(4)
        spacer.paragraph_format.keep_with_next = True

    # ---------------------------------------------------------------- sections

    def _render_sections(self) -> None:
        for section in self.doc_model.sections:
            self._add_banner_pill(section.title)

            if section.blocks:
                for block in section.blocks:
                    self._render_block(block)
            else:
                for claim in section.claims:
                    if claim.status != "dropped":
                        self._render_claim(claim)

    def _render_block(self, block: Block) -> None:
        if block.kind == "heading":
            paragraph = self.doc.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(5)
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.keep_with_next = True
            run = paragraph.add_run(str(block.payload.get("text", "")))
            run.font.bold = True
            run.font.size = Pt(9.5)
            run.font.name = "Arial"
            run.font.color.rgb = self.primary_rgb

        elif block.kind == "bullets":
            items = block.payload.get("items", [])
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                self._add_bullet_item(
                    str(item.get("text", "")),
                    level=self._coerce_level(item.get("level")),
                    tag=item.get("tag"),
                )

        elif block.kind == "table":
            self._render_table_block(
                block.payload, full_width=block.width == BlockWidth.PAGE
            )

        elif block.kind == "banner":
            text = str(block.payload.get("text", "")).strip()
            if text:
                self._add_banner_pill(text, is_subbanner=True)

        elif block.kind == "callout":
            text = str(block.payload.get("text", "")).strip()
            if text:
                paragraph = self.doc.add_paragraph()
                paragraph.paragraph_format.space_before = Pt(2)
                paragraph.paragraph_format.space_after = Pt(3)
                paragraph.paragraph_format.left_indent = Inches(0.15)
                paragraph.paragraph_format.right_indent = Inches(0.15)
                if self.theme.tint:
                    paragraph._p.get_or_add_pPr().append(
                        parse_xml(
                            f'<w:shd {nsdecls("w")} w:fill="'
                            f'{self.theme.tint.lstrip("#").upper()}"/>'
                        )
                    )
                run = paragraph.add_run(text)
                run.font.size = Pt(8.5)
                run.font.bold = True
                run.font.name = "Arial"
                run.font.color.rgb = self.primary_rgb

        elif block.kind == "qa_item":
            question = str(block.payload.get("question", "")).strip()
            answer = str(block.payload.get("answer", "")).strip()
            if question:
                paragraph = self.doc.add_paragraph()
                paragraph.paragraph_format.space_before = Pt(3)
                paragraph.paragraph_format.space_after = Pt(1)
                run = paragraph.add_run(question)
                run.font.bold = True
                run.font.size = Pt(8.5)
                run.font.name = "Arial"
            if answer:
                paragraph = self.doc.add_paragraph()
                paragraph.paragraph_format.left_indent = Inches(0.18)
                paragraph.paragraph_format.space_after = Pt(3)
                run = paragraph.add_run(answer)
                run.font.size = Pt(8.0)
                run.font.name = "Arial"

        elif block.kind == "figure":
            image_path = str(block.payload.get("image_path", "")).strip()
            caption = str(block.payload.get("caption", "")).strip()
            image_bytes: bytes | None = None
            if hasattr(self.doc_model, "assets") and image_path in self.doc_model.assets:
                image_bytes = self.doc_model.assets[image_path]
            elif image_path:
                file_candidate = Path(image_path)
                if file_candidate.exists():
                    with contextlib.suppress(Exception):
                        image_bytes = file_candidate.read_bytes()

            if image_bytes:
                paragraph = self.doc.add_paragraph()
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                paragraph.paragraph_format.space_before = Pt(4)
                paragraph.paragraph_format.space_after = Pt(1)
                paragraph.paragraph_format.keep_with_next = True
                with contextlib.suppress(Exception):
                    run = paragraph.add_run()
                    # Images fit inside column width (3.35 in) or full page width
                    width = Inches(6.8) if block.width == BlockWidth.PAGE else Inches(3.35)
                    run.add_picture(io.BytesIO(image_bytes), width=width)

                if caption:
                    cap_p = self.doc.add_paragraph()
                    cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    cap_p.paragraph_format.space_before = Pt(0)
                    cap_p.paragraph_format.space_after = Pt(5)
                    cap_run = cap_p.add_run(caption)
                    cap_run.font.size = Pt(7.5)
                    cap_run.font.italic = True
                    cap_run.font.name = "Arial"
                    cap_run.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)

    @staticmethod
    def _coerce_level(value: Any) -> int:
        try:
            level = int(value)
        except (TypeError, ValueError):
            return 1
        return max(1, min(level, 3))

    def _add_bullet_item(self, text: str, level: int = 1, tag: str | None = None) -> None:
        clean_text = text.strip()
        if not clean_text:
            return

        # Strip any leading bracketed tags or emoji badges that might be duplicated in raw text
        clean_text = STRIP_LEADING_TAGS_RE.sub("", clean_text).strip()

        paragraph = self.doc.add_paragraph()
        # Hanging indents matching reference PDF geometry
        indents = {1: Inches(0.18), 2: Inches(0.34), 3: Inches(0.50)}
        paragraph.paragraph_format.left_indent = indents.get(level, Inches(0.18))
        paragraph.paragraph_format.first_line_indent = Inches(-0.14)
        paragraph.paragraph_format.space_before = Pt(0.5)
        paragraph.paragraph_format.space_after = Pt(2.0)
        paragraph.paragraph_format.line_spacing = 1.15

        # Bullet glyphs: ● (level 1), ○ (level 2), ■ (level 3)
        glyphs = {1: "● ", 2: "○ ", 3: "■ "}
        glyph_run = paragraph.add_run(glyphs.get(level, "● "))
        glyph_run.font.size = Pt(7.0)
        glyph_run.font.name = "Arial"
        glyph_run.font.bold = True
        glyph_run.font.color.rgb = RGBColor(0x33, 0x41, 0x55)

        # Semantic badge prefix (e.g. 🗣 SPOKEN: , ❗ REMEMBER: )
        chip = BADGE_CHIPS.get((tag or "").upper())
        if chip:
            chip_label, chip_color = chip
            chip_run = paragraph.add_run(chip_label)
            chip_run.font.bold = True
            chip_run.font.size = Pt(8.0)
            chip_run.font.name = "Arial"
            chip_run.font.color.rgb = chip_color

        # Academic trans key-term formatting: bold leading term before colon
        # e.g., "Beneficence: To do good" -> Bold "Beneficence:", regular " To do good"
        colon_pos = clean_text.find(":")
        period_pos = clean_text.find(".")
        if 0 < colon_pos <= 35 and (period_pos == -1 or colon_pos < period_pos):
            key_part = clean_text[: colon_pos + 1]
            rest_part = clean_text[colon_pos + 1 :]

            run_key = paragraph.add_run(key_part)
            run_key.font.bold = True
            run_key.font.size = Pt(8.5)
            run_key.font.name = "Arial"
            run_key.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

            if rest_part:
                run_rest = paragraph.add_run(rest_part)
                run_rest.font.size = Pt(8.5)
                run_rest.font.name = "Arial"
                run_rest.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)
        else:
            run_t = paragraph.add_run(clean_text)
            run_t.font.size = Pt(8.5)
            run_t.font.name = "Arial"
            run_t.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    def _render_claim(self, claim: VerifiedClaim) -> None:
        self._add_bullet_item(claim.card.text, level=1, tag=claim.tag)

    def _render_table_block(self, payload: dict[str, Any], full_width: bool = False) -> None:
        headers = payload.get("headers") or []
        rows = payload.get("rows") or []
        if not headers and not rows:
            return

        if full_width:
            self._begin_full_width()
        try:
            num_cols = len(headers) if headers else (len(rows[0]) if rows else 1)
            num_cols = max(1, num_cols)
            table = self.doc.add_table(rows=len(rows) + (1 if headers else 0), cols=num_cols)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            table.autofit = False

            width_dxa = "10676" if full_width else "5083"
            tblPr = table._tbl.tblPr
            tblPr.append(parse_xml(f'<w:tblW {nsdecls("w")} w:w="{width_dxa}" w:type="dxa"/>'))
            tblPr.append(parse_xml(f'<w:tblLayout {nsdecls("w")} w:type="fixed"/>'))

            _set_table_borders(table, color="CCCCCC")
            _no_split_rows(table)

            row_offset = 0
            if headers:
                for column, header in enumerate(headers):
                    cell = table.cell(0, column)
                    _set_cell_background(cell, self.theme.primary)
                    _set_cell_margins(cell, top=50, bottom=50, left=60, right=60)
                    paragraph = cell.paragraphs[0]
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run = paragraph.add_run(str(header).upper())
                    run.font.bold = True
                    run.font.size = Pt(7.5)
                    run.font.name = "Arial"
                    run.font.color.rgb = self.on_primary_rgb
                row_offset = 1

            for row_index, row in enumerate(rows):
                for column, value in enumerate(row):
                    if column >= num_cols:
                        break
                    cell = table.cell(row_offset + row_index, column)
                    if row_index % 2 == 0 and self.theme.tint:
                        _set_cell_background(cell, self.theme.tint)
                    _set_cell_margins(cell, top=35, bottom=35, left=50, right=50)
                    run = cell.paragraphs[0].add_run(str(value))
                    run.font.size = Pt(7.5)
                    run.font.name = "Arial"

            spacer = self.doc.add_paragraph()
            spacer.paragraph_format.space_before = Pt(1)
            spacer.paragraph_format.space_after = Pt(4)
        finally:
            if full_width:
                self._end_full_width()

    # ------------------------------------------------------------------ review

    def _render_review_questions(self) -> None:
        if not self.doc_model.review or not self.doc_model.review.questions:
            return

        self._add_banner_pill("REVIEW QUESTIONS")
        for question in self.doc_model.review.questions:
            paragraph = self.doc.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(4)
            paragraph.paragraph_format.space_after = Pt(1)
            paragraph.paragraph_format.keep_with_next = True
            run = paragraph.add_run(f"{question.id}. {question.stem}")
            run.font.bold = True
            run.font.size = Pt(8.5)
            run.font.name = "Arial"

            for key in sorted(question.options):
                option = self.doc.add_paragraph()
                option.paragraph_format.space_before = Pt(0)
                option.paragraph_format.space_after = Pt(1)
                option.paragraph_format.left_indent = Inches(0.18)
                run = option.add_run(f"{key}. {question.options[key]}")
                run.font.size = Pt(8.0)
                run.font.name = "Arial"

            if question.explanation:
                explanation = self.doc.add_paragraph()
                explanation.paragraph_format.space_before = Pt(1)
                explanation.paragraph_format.space_after = Pt(3)
                explanation.paragraph_format.left_indent = Inches(0.18)
                run = explanation.add_run("Rationale: ")
                run.font.bold = True
                run.font.size = Pt(7.5)
                run.font.name = "Arial"
                run = explanation.add_run(question.explanation)
                run.font.size = Pt(7.5)
                run.font.italic = True
                run.font.name = "Arial"

        self._add_banner_pill("ANSWER KEY")
        grid = self.doc.add_table(rows=len(self.doc_model.review.questions) + 1, cols=2)
        grid.alignment = WD_TABLE_ALIGNMENT.CENTER
        grid.autofit = False
        tblPr = grid._tbl.tblPr
        tblPr.append(parse_xml(f'<w:tblW {nsdecls("w")} w:w="5083" w:type="dxa"/>'))
        tblPr.append(parse_xml(f'<w:tblLayout {nsdecls("w")} w:type="fixed"/>'))

        grid.columns[0].width = Inches(1.7)
        grid.columns[1].width = Inches(1.83)
        _set_table_borders(grid, color="CCCCCC")
        _no_split_rows(grid)

        for column, label in enumerate(("ITEM", "CORRECT ANSWER")):
            cell = grid.cell(0, column)
            _set_cell_background(cell, self.theme.primary)
            _set_cell_margins(cell, top=35, bottom=35, left=50, right=50)
            run = cell.paragraphs[0].add_run(label)
            run.font.bold = True
            run.font.size = Pt(7.5)
            run.font.name = "Arial"
            run.font.color.rgb = self.on_primary_rgb

        for index, question in enumerate(self.doc_model.review.questions, start=1):
            item_cell = grid.cell(index, 0)
            answer_cell = grid.cell(index, 1)
            _set_cell_margins(item_cell, top=30, bottom=30, left=50, right=50)
            _set_cell_margins(answer_cell, top=30, bottom=30, left=50, right=50)
            if index % 2 == 1 and self.theme.tint:
                _set_cell_background(item_cell, self.theme.tint)
                _set_cell_background(answer_cell, self.theme.tint)
            i_run = item_cell.paragraphs[0].add_run(str(question.id))
            i_run.font.size = Pt(7.5)
            i_run.font.name = "Arial"
            a_run = answer_cell.paragraphs[0].add_run(question.correct_answer)
            a_run.font.bold = True
            a_run.font.size = Pt(7.5)
            a_run.font.name = "Arial"
