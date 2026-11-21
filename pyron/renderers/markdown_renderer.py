from __future__ import annotations

from pathlib import Path

from pyron.api.contracts import TransDocument

TAG_PREFIXES = {
    "SPOKEN": "**[SPOKEN]** ",
    "TEXTBOOK": "**[REFERENCE]** ",
    "REMEMBER": "**[REMEMBER]** ",
    "MNEMONIC": "**[MNEMONIC]** ",
    "ALERT": "**[ALERT]** ",
    "QUESTION": "**[QUESTION]** ",
}


def _cell(value: object) -> str:
    """Escape a value so it cannot break a Markdown table."""
    return str(value).replace("|", r"\|").replace("\n", "<br>").strip()


class MarkdownRenderer:
    def __init__(self, doc_model: TransDocument):
        self.doc = doc_model

    def render(self, output_path: Path | str | None = None) -> str:
        lines: list[str] = []
        subject = self.doc.subject

        header = f"# {subject.code} {subject.name.upper()}".strip()
        if subject.session_type:
            header += f" [{subject.session_type}]"
        if subject.batch:
            header += f" — {subject.batch}"
        lines.append(header)

        term_parts = [
            part
            for part in [subject.term, f"Week {subject.week}" if subject.week else "", subject.date_str or ""]
            if part
        ]
        if term_parts:
            lines.append(f"**{' / '.join(term_parts)}**  ")
        if subject.lecturer:
            lecturer = f"**Instructor:** {subject.lecturer}"
            if subject.credentials:
                lecturer += f", {subject.credentials}"
            lines.append(lecturer)
        lines.append("\n---\n")

        lines.append(f"## {self.doc.title}\n")

        if self.doc.outline:
            lines.append("### TOPIC OUTLINE\n")
            for node in self.doc.outline:
                indent = "  " * (node.level - 1)
                lines.append(f"{indent}- **{node.numeral}.** {node.title}")
            lines.append("\n---\n")

        for section in self.doc.sections:
            lines.append(f"### {section.title.upper()}\n")
            if section.blocks:
                for block in section.blocks:
                    self._render_block(block, lines)
            else:
                for claim in section.claims:
                    if claim.status == "dropped":
                        continue
                    prefix = TAG_PREFIXES.get(claim.tag or "", "")
                    lines.append(f"- {prefix}{claim.card.text}")
                lines.append("")
            lines.append("\n---\n")

        if self.doc.review and self.doc.review.questions:
            lines.append("### REVIEW QUESTIONS\n")
            for question in self.doc.review.questions:
                lines.append(f"**{question.id}. {question.stem}**\n")
                for key in sorted(question.options):
                    lines.append(f"- **{key}.** {question.options[key]}")
                if question.explanation:
                    lines.append(f"\n*Rationale: {question.explanation}*\n")
                lines.append("")

            lines.append("#### ANSWER KEY\n")
            lines.append("| Item | Correct Answer |")
            lines.append("| :--- | :--- |")
            for question in self.doc.review.questions:
                lines.append(f"| {question.id} | **{question.correct_answer}** |")
            lines.append("\n---\n")

        content = "\n".join(lines)
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return content

    def _render_block(self, block, lines: list[str]) -> None:
        if block.kind == "heading":
            lines.append(f"#### {block.payload.get('text', '')}\n")

        elif block.kind == "bullets":
            items = block.payload.get("items", [])
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    level = max(1, min(int(item.get("level") or 1), 3))
                except (TypeError, ValueError):
                    level = 1
                prefix = TAG_PREFIXES.get(item.get("tag") or "", "")
                lines.append(f"{'  ' * (level - 1)}- {prefix}{item.get('text', '')}")
            lines.append("")

        elif block.kind == "table":
            headers = block.payload.get("headers") or []
            rows = block.payload.get("rows") or []
            if not headers and not rows:
                return
            columns = len(headers) if headers else max(len(row) for row in rows)
            if headers:
                lines.append("| " + " | ".join(_cell(h) for h in headers) + " |")
                lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for row in rows:
                padded = list(row) + [""] * (columns - len(row))
                lines.append("| " + " | ".join(_cell(value) for value in padded) + " |")
            lines.append("")

        elif block.kind == "banner":
            text = str(block.payload.get("text", "")).strip()
            if text:
                lines.append(f"> **{text.upper()}**\n")

        elif block.kind == "callout":
            text = str(block.payload.get("text", "")).strip()
            if text:
                lines.append(f"> {text}\n")

        elif block.kind == "qa_item":
            question = str(block.payload.get("question", "")).strip()
            answer = str(block.payload.get("answer", "")).strip()
            if question:
                lines.append(f"**Q:** {question}\n")
            if answer:
                lines.append(f"**A:** {answer}\n")

        elif block.kind == "figure":
            img_path = str(block.payload.get("image_path", "")).strip()
            caption = str(block.payload.get("caption", "")).strip()
            alt = str(block.payload.get("alt_text", "")).strip() or caption or "Figure"
            if img_path:
                lines.append(f"![{alt}]({img_path})\n")
                if caption:
                    lines.append(f"*{caption}*\n")
