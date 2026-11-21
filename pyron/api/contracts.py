from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceKind(StrEnum):
    SLIDES = "slides"
    TRANSCRIPT = "transcript"
    TEXTBOOK = "textbook"
    NOTES = "notes"
    FIGURE = "figure"


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: SourceKind
    path: Path | None = None
    data: bytes | None = None
    media_type: str | None = None
    meta: dict[str, str] = Field(default_factory=dict)


class SubjectSpec(BaseModel):
    code: str = ""                                    # Optional course/topic code (e.g. "[CS101]", "[LAW201]")
    name: str = "General Study Guide"                # Subject / Course / Topic title
    session_type: str = ""                            # Optional: "Lecture", "Lab", "Seminar", etc.
    batch: str = ""                                   # Optional: "Batch 2026", "Cohort A", etc.
    term: str = ""                                    # Optional term/week info
    week: int | None = None
    date_str: str | None = None
    lecturer: str = ""                                # Instructor / Speaker / Author
    credentials: str = ""
    team: list[str] = Field(default_factory=list)     # Optional contributors
    editors: list[str] = Field(default_factory=list)  # Optional editors
    primary_color: str | None = None                  # Universal headless color (e.g. "#1e40af", "#8B0000")
    tint_color: str | None = None                     # Optional custom tint (or auto-derived)


class EvidenceSpan(BaseModel):
    eid: str                                          # e.g. "E0001"
    kind: SourceKind
    doc_sha: str
    char_span: tuple[int, int] = (0, 0)
    locator: dict[str, Any] = Field(default_factory=dict) # {"slide_no": 1} or {"t_start_ms": 1200}
    text: str                                         # Verbatim exact text
    confidence: float = 1.0
    media_path: str | None = None                     # Relative path in bundle e.g. "images/slide_01_img_01.png"
    media_bytes: bytes | None = Field(default=None, exclude=True) # Raw image bytes (excluded from JSON serialization)


class ClaimCard(BaseModel):
    cid: str                                          # e.g. "sec_1_C01" (globally unique)
    section_id: str = ""                              # e.g. "sec_1"
    text: str                                         # Atomic claim (<= 40 words)
    evidence: list[str] = Field(min_length=1)         # eids that support this claim
    support: Literal["explicit", "inferred"] = "explicit"
    speech_act: Literal[
        "assertion", "definition", "mnemonic", "exam_hint", "question", "warning"
    ] = "assertion"
    numbers: list[str] = Field(default_factory=list)  # Extracted verbatim numbers
    section_hint: str = ""


class VerifiedClaim(BaseModel):
    card: ClaimCard
    status: Literal["verified", "downgraded", "dropped", "needs_review"] = "verified"
    tag: str | None = None                            # "SPOKEN", "TEXTBOOK", "REMEMBER", "MNEMONIC", "ALERT", "QUESTION"


class OutlineNode(BaseModel):
    id: str
    numeral: str                                      # e.g. "I", "a", "1"
    title: str
    level: int = 1
    parent_id: str | None = None
    children: list[OutlineNode] = Field(default_factory=list)


class ReviewQuestion(BaseModel):
    id: int
    stem: str                                         # Question prompt
    options: dict[str, str] = Field(default_factory=dict) # {"A": "Serine", "B": "Cysteine", ...}
    correct_answer: str                               # e.g. "A"
    explanation: str = ""


class ReviewSet(BaseModel):
    questions: list[ReviewQuestion] = Field(default_factory=list)


class BlockWidth(StrEnum):
    COLUMN = "column"                                 # Standard 2-column width
    PAGE = "page"                                     # Full-width spanner (wide table, banner)


class BlockBreak(StrEnum):
    AUTO = "auto"
    AVOID = "avoid"


class Block(BaseModel):
    kind: Literal["banner", "heading", "bullets", "table", "callout", "qa_item", "figure"]
    width: BlockWidth = BlockWidth.COLUMN
    break_inside: BlockBreak = BlockBreak.AVOID
    keep_with_next: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class Section(BaseModel):
    id: str
    title: str
    blocks: list[Block] = Field(default_factory=list)
    claims: list[VerifiedClaim] = Field(default_factory=list)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class TransWarning(BaseModel):
    code: str
    message: str
    locator: str | None = None


class RunManifest(BaseModel):
    run_id: str
    timestamp: str
    model: str
    source_hashes: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class TransDocument(BaseModel):
    schema_version: str = "1.0"
    doc_id: str
    title: str
    subject: SubjectSpec
    outline: list[OutlineNode] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    review: ReviewSet | None = None
    usage: Usage = Field(default_factory=Usage)
    warnings: list[TransWarning] = Field(default_factory=list)
    manifest: RunManifest | None = None
    assets: dict[str, bytes] = Field(default_factory=dict, exclude=True)

    def save_json(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load_json(cls, path: Path | str) -> TransDocument:
        p = Path(path)
        return cls.model_validate_json(p.read_text(encoding="utf-8"))


class GenerateOptions(BaseModel):
    quality: Literal["draft", "standard", "max"] = "standard"
    max_cost_usd: float | None = None
    include_review_questions: bool = True
    include_textbook_expansion: bool = True
    allow_verbal_tags: bool = True
    theme: str | None = None
    max_concurrency: int = 3


class GenerateRequest(BaseModel):
    subject: SubjectSpec
    title: str = "Untitled Lecture"
    sources: list[Source] = Field(min_length=1)
    options: GenerateOptions = Field(default_factory=GenerateOptions)
    idempotency_key: str | None = None


class TransBundle(BaseModel):
    bundle_dir: Path
    doc_json: Path
    evidence_jsonl: Path | None = None
    doc_docx: Path | None = None
    doc_md: Path | None = None
    claims_jsonl: Path | None = None
    questions_json: Path | None = None
    trans_pdf: Path | None = None
    manifest_json: Path | None = None
    images_dir: Path | None = None
