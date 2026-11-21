from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pyron.api.contracts import TransBundle, TransDocument
from pyron.renderers.docx_renderer import DocxRenderer
from pyron.renderers.markdown_renderer import MarkdownRenderer
from pyron.theming.theme_manager import Theme


def _atomic_write_text(path: Path, content: str) -> None:
    """Write via a temp file so a crash cannot leave a truncated bundle file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


class BundleStorage:
    @staticmethod
    def save_bundle(
        doc: TransDocument,
        theme: Theme,
        output_dir: Path | str,
        render_docx: bool = True,
        render_md: bool = True,
    ) -> TransBundle:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1. Canonical headless document.
        doc_json_path = out_dir / "doc.json"
        _atomic_write_text(doc_json_path, doc.model_dump_json(indent=2))

        # 2. Claim cards, one JSON object per line, each carrying its section.
        claims_jsonl_path = out_dir / "claims.jsonl"
        claim_lines: list[str] = []
        for section in doc.sections:
            for claim in section.claims:
                payload = claim.model_dump()
                payload["card"]["section_id"] = payload["card"].get("section_id") or section.id
                claim_lines.append(json.dumps(payload, ensure_ascii=False))
        _atomic_write_text(claims_jsonl_path, "\n".join(claim_lines) + ("\n" if claim_lines else ""))

        # 3. Full provenance graph.
        evidence_jsonl_path = out_dir / "evidence.jsonl"
        evidence_lines = [span.model_dump_json() for span in doc.evidence]
        _atomic_write_text(
            evidence_jsonl_path,
            "\n".join(evidence_lines) + ("\n" if evidence_lines else ""),
        )

        # 4. Review questions keep one stable shape even when empty.
        questions_json_path = out_dir / "questions.json"
        review = doc.review.model_dump() if doc.review else {"questions": []}
        _atomic_write_text(questions_json_path, json.dumps(review, indent=2, ensure_ascii=False))

        # 5. Run manifest.
        manifest_json_path = out_dir / "manifest.json"
        if doc.manifest:
            _atomic_write_text(manifest_json_path, doc.manifest.model_dump_json(indent=2))

        # 6. Markdown.
        doc_md_path: Path | None = None
        if render_md:
            doc_md_path = out_dir / "doc.md"
            content = MarkdownRenderer(doc).render()
            _atomic_write_text(doc_md_path, content)

        # 7. Word document.
        doc_docx_path: Path | None = None
        if render_docx:
            doc_docx_path = out_dir / "doc.docx"
            DocxRenderer(doc, theme=theme).render(doc_docx_path)

        # 8. Assets (images extracted from references).
        images_dir: Path | None = None
        if hasattr(doc, "assets") and doc.assets:
            images_dir = out_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            for rel_path, data in doc.assets.items():
                asset_path = out_dir / rel_path
                asset_path.parent.mkdir(parents=True, exist_ok=True)
                asset_path.write_bytes(data)

        return TransBundle(
            bundle_dir=out_dir,
            doc_json=doc_json_path,
            evidence_jsonl=evidence_jsonl_path,
            doc_docx=doc_docx_path,
            doc_md=doc_md_path,
            claims_jsonl=claims_jsonl_path,
            questions_json=questions_json_path,
            manifest_json=manifest_json_path,
            images_dir=images_dir,
        )
