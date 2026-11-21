# Pyron (`daedalabs/pyron`)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: DaedaLabs Source-Available](https://img.shields.io/badge/license-DaedaLabs_Source--Available-purple.svg)](LICENSE)
[![Tests: 72 Passing](https://img.shields.io/badge/tests-72%20passing-brightgreen.svg)](tests/)
[![Ruff: Clean](https://img.shields.io/badge/code%20style-ruff-black.svg)](https://docs.astral.sh/ruff/)

**Pyron** is a headless, provenance-first compiler engine for high-density academic study guides (*"transes"*). It ingests lecture slide decks, verbatim transcripts, and supplementary readings, synthesizing them into a canonical study bundle where **every rendered claim is cryptographically traceable to its primary source evidence**.

> **Design Axiom:** *The model proposes; the engine verifies.*  
> If an AI provider hallucinated a numeral, invented a citation, or drifted from source material, Pyron's verification gates reject or flag the claim before it reaches the final document.

---

## Key Features

* **Strict Provenance & Verification Gates:**
  * **Gate 1 (Existence):** Every claim must reference valid evidence IDs (`eid`) cataloged in `evidence.jsonl`.
  * **Gate 2 (Numerals):** Every number, date, dosage, and percentage in a claim must appear verbatim in its cited source span.
  * **Gate 3 (Deterministic Semantic Badges):** Labels like `[SPOKEN]`, `[REFERENCE]`, `[REMEMBER]`, `[MNEMONIC]`, and `[QUESTION]` are computed directly by the engine's provenance ledger—never by LLM discretion.
* **Dual-Format Publication Engine:**
  * Compiles to clean, standardized Markdown (`doc.md`).
  * Emits professionally typeset Word documents (`doc.docx`) with a true 2-column A4 grid, minimal header/footer layout (`Page X of Y`), semantic badge chips, definition typography, and extracted reference figures.
* **Zero-Cost Instant Re-rendering:**
  * Once `doc.json` is generated, themes, palettes, and typography can be re-rendered in milliseconds without calling AI or paying token costs.
* **Extensible & Decoupled Architecture:**
  * Clean Python library API (`import pyron`) designed to power downstream modules (such as Anki flashcard generators, interactive active-recall bots, and web frontends).

---

## Installation

Install in editable mode for local development:

```bash
# Core library and CLI (MIT dependencies only)
pip install -e ".[dev]"

# Optional: Add PDF ingestion support (installs PyMuPDF under AGPL-3.0)
pip install -e ".[pdf]"
```

*Requirements:* Python 3.11 or newer.

---

## Usage

Pyron can be used either as an executable command-line tool or as a standalone Python library.

### 1. Command-Line Interface (CLI)

#### Compile a Study Guide
```bash
pyron build \
  demo_data/distributed_systems_slides.pptx \
  demo_data/distributed_systems_transcript.vtt \
  demo_data/supplementary_reading.txt \
  --subject "Distributed Systems" \
  --code CS401 \
  --lecturer "Prof. Raymond Vance, PhD" \
  --term "Fall 2026 / Week 7" \
  --color classic_blue \
  --out dist/distributed_systems
```

*Note on AI Providers:* If no API key is supplied, Pyron automatically falls back to an offline deterministic mock provider, ensuring offline pipelines, automated tests, and local builds succeed with zero configuration. To use live LLMs, set `GEMINI_API_KEY` or `OPENAI_API_KEY` in your `.env` file.

#### Instant Re-rendering
Change color palettes or document formats in milliseconds without re-running synthesis:
```bash
pyron render dist/distributed_systems/doc.json --color crimson --out dist/crimson.docx
```

---

### 2. Python Library API

Pyron is built to be embedded into backends, web applications, and educational microservices:

```python
import asyncio
from pathlib import Path
from pyron import PyronEngine, GenerateRequest, SubjectSpec, Source, SourceKind
from pyron.ai.gemini_provider import GeminiProvider

async def main():
    engine = PyronEngine(provider=GeminiProvider())

    request = GenerateRequest(
        subject=SubjectSpec(
            name="Clinical Bioethics",
            code="MED201",
            term="Semester 1 / Week 3",
            lecturer="Dr. Elena Santos, MD, MBE",
        ),
        sources=[
            Source(kind=SourceKind.SLIDES, path=Path("slides.pptx")),
            Source(kind=SourceKind.TRANSCRIPT, path=Path("transcript.vtt")),
        ],
    )

    doc = await engine.generate(request)
    bundle = engine.save_bundle(doc, output_dir=Path("./output_bundle"))
    print(f"Compiled bundle saved to {bundle.output_dir}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Canonical Bundle Artifacts

Pyron outputs an auditable, self-contained study bundle:

```
dist/lecture_bundle/
├── doc.json        # Canonical headless study document (schema_version, sections, claims, evidence)
├── evidence.jsonl  # Verbatim source spans with locator, hash, and source ID
├── claims.jsonl    # Atomized, verified claims linked to evidence spans
├── questions.json  # Comprehensive active-recall review bank
├── manifest.json   # Build run stats, model metadata, source hashes, and cost
├── doc.md          # Clean GFM Markdown output
├── doc.docx        # High-density 2-column Word document with styled callouts & figures
└── images/         # Extracted and referenced diagram assets
```

Every claim in `claims.jsonl` resolves offline against `evidence.jsonl` with zero network access and complete auditability.

---

## Verification & Testing

Pyron maintains comprehensive automated test coverage across verification gates, ingestion pipelines, DOCX rendering, and CLI contracts:

```bash
# Run test suite
pytest

# Run linter
ruff check .
```

---

## License & Attribution

This project is licensed under the **Pyron Source-Available Software License** (see [LICENSE](LICENSE)).  
Copyright &copy; 2026 Deogenes Maranan / DaedaLabs.

* **Product Integration:** You are free to view, modify, and embed Pyron into your applications, SaaS platforms, or commercial tools.
* **Mandatory Attribution:** Any product, service, or distribution incorporating Pyron must visibly display:  
  `Powered by Pyron / DaedaLabs (Deogenes Maranan)`  
  in user-facing documentation, "About" screens, CLI outputs, or credit notices.
* **Anti-Copycat Clause:** You may not redistribute, repackage, or resell Pyron as a standalone library or compiler without substantial, novel, and transformative functionality added.
* **Contributions:** Contributions are welcomed! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details on the Contributor License Agreement.
* **Third-Party Extras:** Optional extras (e.g., PyMuPDF via `[pdf]`) are governed by their respective licenses (AGPL-3.0 / Artifex).
