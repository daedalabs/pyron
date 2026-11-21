from __future__ import annotations

from pathlib import Path
from typing import Literal

from pyron.ai.base import BaseAIProvider
from pyron.ai.mock_provider import MockProvider
from pyron.api.contracts import (
    GenerateRequest,
    TransBundle,
    TransDocument,
)
from pyron.api.errors import InputError
from pyron.renderers.docx_renderer import DocxRenderer
from pyron.renderers.markdown_renderer import MarkdownRenderer
from pyron.storage.bundle import BundleStorage
from pyron.synthesis.pipeline import SynthesisPipeline
from pyron.theming.theme_manager import ThemeManager


class TransEngine:
    def __init__(
        self,
        ai_provider: BaseAIProvider | None = None,
        theme_manager: ThemeManager | None = None,
    ):
        self.ai = ai_provider
        self.themes = theme_manager or ThemeManager()
        self.pipeline = SynthesisPipeline(ai_provider) if ai_provider else None

    @classmethod
    def create(
        cls,
        ai_provider: BaseAIProvider | None = None,
        themes_path: Path | str | None = None,
    ) -> TransEngine:
        """Create an engine. Without a provider the offline mock is used."""
        return cls(
            ai_provider=ai_provider or MockProvider(),
            theme_manager=ThemeManager(themes_path),
        )

    async def generate(self, req: GenerateRequest) -> TransDocument:
        """Run the multi-stage synthesis pipeline and return the canonical document."""
        if self.pipeline is None:
            raise InputError(
                "This engine was created without an AI provider; generation is unavailable. "
                "Use TransEngine.create() to build a generating engine."
            )
        return await self.pipeline.run(req)

    def render(
        self,
        doc: TransDocument,
        fmt: Literal["docx", "md"] = "docx",
        output_path: Path | str = "trans.docx",
        theme_override: str | None = None,
    ) -> Path:
        """Render an existing document. Never calls the AI provider."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        if fmt == "docx":
            theme = self.themes.get_theme_for_spec(
                doc.subject, theme_override=theme_override
            )
            DocxRenderer(doc, theme=theme).render(out)
            return out
        if fmt == "md":
            MarkdownRenderer(doc).render(out)
            return out
        raise ValueError(f"Unsupported format: {fmt}")

    def save_bundle(
        self,
        doc: TransDocument,
        output_dir: Path | str,
        render_docx: bool = True,
        render_md: bool = True,
        theme_override: str | None = None,
    ) -> TransBundle:
        """Save the complete headless bundle (json, evidence, claims, docx, md)."""
        theme = self.themes.get_theme_for_spec(
            doc.subject, theme_override=theme_override
        )
        return BundleStorage.save_bundle(
            doc=doc,
            theme=theme,
            output_dir=output_dir,
            render_docx=render_docx,
            render_md=render_md,
        )
