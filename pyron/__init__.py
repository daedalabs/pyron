"""
Pyron: Headless provenance-first academic compiler engine.
A modular, evidence-grounded engine for compiling high-density study guides from lecture slides, transcripts, and reading notes.
"""

from pyron.api.contracts import (
    GenerateRequest,
    Source,
    SourceKind,
    SubjectSpec,
    TransBundle,
    TransDocument,
)
from pyron.engine import TransEngine

# Aliases for unified Pyron branding
PyronEngine = TransEngine
StudyBundle = TransBundle
StudyDocument = TransDocument

__version__ = "0.1.0"
__all__ = [
    "GenerateRequest",
    "PyronEngine",
    "Source",
    "SourceKind",
    "StudyBundle",
    "StudyDocument",
    "SubjectSpec",
    "TransBundle",
    "TransDocument",
    "TransEngine",
]
