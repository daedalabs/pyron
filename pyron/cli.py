from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from pyron import __version__
from pyron.ai.gemini_provider import GeminiProvider
from pyron.ai.mock_provider import MockProvider
from pyron.ai.openai_provider import OpenAIProvider
from pyron.api.contracts import (
    GenerateRequest,
    Source,
    SourceKind,
    SubjectSpec,
    TransDocument,
)
from pyron.api.errors import InputError, ProviderError
from pyron.config import load_env_file
from pyron.engine import TransEngine

# Extension -> default source kind. `.txt` that looks like a transcript is
# upgraded to TRANSCRIPT in `_classify`.
SUPPORTED_INPUTS: dict[str, SourceKind] = {
    ".pptx": SourceKind.SLIDES,
    ".pdf": SourceKind.SLIDES,
    ".vtt": SourceKind.TRANSCRIPT,
    ".srt": SourceKind.TRANSCRIPT,
    ".txt": SourceKind.NOTES,
    ".text": SourceKind.NOTES,
    ".md": SourceKind.NOTES,
    ".markdown": SourceKind.NOTES,
}


def _classify(path: Path) -> SourceKind:
    suffix = path.suffix.lower()
    if suffix == ".ppt":
        raise InputError(
            f"Legacy binary .ppt is not supported: {path}. "
            "Save it as .pptx and retry."
        )
    if suffix not in SUPPORTED_INPUTS:
        supported = ", ".join(sorted(SUPPORTED_INPUTS))
        raise InputError(
            f"Unsupported input '{path.name}' ({suffix or 'no extension'}). "
            f"Supported extensions: {supported}"
        )
    kind = SUPPORTED_INPUTS[suffix]
    stem = path.stem.lower()
    if suffix == ".pdf" and any(
        marker in stem for marker in ("reading", "textbook", "notes", "supplement")
    ):
        return SourceKind.TEXTBOOK
    if kind == SourceKind.NOTES and "transcript" in stem:
        kind = SourceKind.TRANSCRIPT
    return kind


def _select_provider(args: argparse.Namespace):
    chosen = args.ai
    if chosen == "auto":
        preference = os.environ.get("TRANS_DEFAULT_AI", "").lower()
        if preference in ("gemini", "openai", "mock"):
            chosen = preference
        elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            chosen = "gemini"
        elif os.environ.get("OPENAI_API_KEY"):
            chosen = "openai"
        else:
            chosen = "mock"

    if chosen == "gemini":
        return GeminiProvider(api_key=args.api_key)
    if chosen == "openai":
        return OpenAIProvider(api_key=args.api_key)
    return MockProvider()


def _print_warnings(doc: TransDocument) -> None:
    if not doc.warnings:
        return
    print(f"Warnings ({len(doc.warnings)}):")
    for warning in doc.warnings:
        location = f" [{warning.locator}]" if warning.locator else ""
        print(f"  - {warning.code}{location}: {warning.message}")


def _run_build(args: argparse.Namespace) -> int:
    provider = _select_provider(args)
    engine = TransEngine.create(ai_provider=provider)

    sources: list[Source] = []
    for raw in args.sources:
        path = Path(raw)
        if not path.exists():
            raise InputError(f"Input file not found: {path}")
        if not path.is_file():
            raise InputError(f"Input is not a file: {path}")
        sources.append(Source(kind=_classify(path), path=path))

    code = args.code.strip()
    subject = SubjectSpec(
        code=f"[{code.strip('[]')}]" if code.strip("[]") else "",
        name=args.subject,
        batch=args.batch,
        term=args.term,
        lecturer=args.lecturer,
        primary_color=args.color,
    )
    request = GenerateRequest(subject=subject, title=args.title, sources=sources)

    async def build() -> TransDocument:
        code_display = f" {subject.code}" if subject.code else ""
        print(f"Synthesizing study guide for {subject.name}{code_display}...")
        return await engine.generate(request)

    doc = asyncio.run(build())
    bundle = engine.save_bundle(doc, output_dir=args.out)
    claims = sum(len(section.claims) for section in doc.sections)
    cost = (
        "no API cost (mock provider)"
        if doc.usage.cost_usd == 0 and getattr(provider, "name", "") == "mock"
        else f"~${doc.usage.cost_usd:.6f}"
    )
    print(
        f"Done: {len(doc.sections)} sections, {claims} verified claims, "
        f"{len(doc.evidence)} evidence spans, "
        f"{doc.usage.input_tokens} in / {doc.usage.output_tokens} out tokens ({cost})."
    )
    _print_warnings(doc)
    print("Bundle:")
    for label, path in (
        ("Headless JSON", bundle.doc_json),
        ("Evidence", bundle.evidence_jsonl),
        ("Editable DOCX", bundle.doc_docx),
        ("Markdown", bundle.doc_md),
        ("Claim cards", bundle.claims_jsonl),
        ("Questions", bundle.questions_json),
    ):
        if path is not None:
            print(f"  {label:14} {path}")
    return 0


def _run_render(args: argparse.Namespace) -> int:
    doc_path = Path(args.doc_json)
    if not doc_path.is_file():
        raise InputError(f"Document not found: {doc_path}")
    doc = TransDocument.load_json(doc_path)
    default_out = "trans_output.docx" if args.fmt == "docx" else "trans_output.md"
    output = args.out or default_out
    engine = TransEngine()  # rendering never needs an AI provider
    rendered = engine.render(
        doc, fmt=args.fmt, output_path=output, theme_override=args.color
    )
    print(f"Re-rendered: {rendered}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(
        prog="pyron", description="Universal academic study guide generator"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__} - Powered by Pyron / DaedaLabs (Deogenes Maranan)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_p = subparsers.add_parser("build", help="Build a study guide from input files")
    build_p.add_argument(
        "sources",
        nargs="+",
        help="Slides (.pptx/.pdf), transcript (.vtt/.srt), or notes (.txt/.md)",
    )
    build_p.add_argument("--subject", default="General Study Guide", help="Subject or course name")
    build_p.add_argument("--code", default="", help="Course code prefix, e.g. CS101")
    build_p.add_argument("--term", default="", help="Term, week, or module info")
    build_p.add_argument("--batch", default="", help="Batch, class, or organization label")
    build_p.add_argument("--lecturer", default="", help="Instructor or speaker name")
    build_p.add_argument("--title", default="Lecture Study Guide", help="Lecture title")
    build_p.add_argument(
        "--color",
        "--theme",
        dest="color",
        default=None,
        help="Theme name (classic_blue, slate_neutral, forest_green, crimson, ...) or hex color",
    )
    build_p.add_argument("--out", default="out_bundle", help="Output directory for the bundle")
    build_p.add_argument(
        "--ai",
        default="auto",
        choices=["auto", "gemini", "openai", "mock"],
        help="AI provider (auto-detects from environment)",
    )
    build_p.add_argument("--api-key", default=None, help="API key for the chosen provider")

    render_p = subparsers.add_parser("render", help="Re-render a doc.json without calling AI")
    render_p.add_argument("doc_json", help="Path to doc.json")
    render_p.add_argument("--format", dest="fmt", default="docx", choices=["docx", "md"])
    render_p.add_argument(
        "--color", "--theme", dest="color", default=None, help="Theme name or hex override"
    )
    render_p.add_argument(
        "--out", default=None, help="Output file (defaults to trans_output.docx/.md)"
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            return _run_render(args)
        return _run_build(args)
    except InputError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    except ProviderError as exc:
        print(f"provider error: {exc.message}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
