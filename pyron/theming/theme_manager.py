from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel

from pyron.api.contracts import SubjectSpec


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join([c*2 for c in h])
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


def derive_tint(primary_hex: str, white_factor: float = 0.90) -> str:
    """Creates a soft pastel tint (e.g. 90% white blend) for callout backgrounds and table shading."""
    r, g, b = _hex_to_rgb(primary_hex)
    tr = int(r * (1 - white_factor) + 255 * white_factor)
    tg = int(g * (1 - white_factor) + 255 * white_factor)
    tb = int(b * (1 - white_factor) + 255 * white_factor)
    return _rgb_to_hex(tr, tg, tb)


def derive_on_primary(primary_hex: str) -> str:
    """Returns #ffffff or #0f172a based on relative luminance."""
    r, g, b = _hex_to_rgb(primary_hex)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b)
    return "#0f172a" if luminance > 165 else "#ffffff"


def derive_secondary(primary_hex: str) -> str:
    """Returns a lighter secondary accent shade."""
    r, g, b = _hex_to_rgb(primary_hex)
    sr = int(r * 0.75 + 255 * 0.25)
    sg = int(g * 0.75 + 255 * 0.25)
    sb = int(b * 0.75 + 255 * 0.25)
    return _rgb_to_hex(sr, sg, sb)


class Theme(BaseModel):
    id: str
    name: str
    primary: str = "#1e40af"
    secondary: str = "#3b82f6"
    tint: str = "#eff6ff"
    on_primary: str = "#ffffff"
    text_dark: str = "#0f172a"
    font_heading: str = "Montserrat, Arial, sans-serif"
    font_body: str = "Arial, Roboto, sans-serif"

    @property
    def primary_rgb_tuple(self) -> tuple[int, int, int]:
        return _hex_to_rgb(self.primary)

    @property
    def tint_rgb_tuple(self) -> tuple[int, int, int]:
        return _hex_to_rgb(self.tint)


class ThemeManager:
    def __init__(self, themes_path: Path | str | None = None):
        if themes_path is None:
            themes_path = Path(__file__).parent / "themes.yaml"
        self.themes_path = Path(themes_path)
        self.themes: dict[str, Theme] = {}
        self.default_theme_id: str = "classic_blue"
        self._load()

    def _load(self) -> None:
        if not self.themes_path.exists():
            return
        data = yaml.safe_load(self.themes_path.read_text(encoding="utf-8")) or {}
        self.default_theme_id = data.get("default_theme", "classic_blue")
        for tid, tdata in data.get("themes", {}).items():
            self.themes[tid] = Theme(**tdata)

    def from_primary_color(
        self,
        primary_hex: str,
        name: str = "Custom Palette",
        tint_hex: str | None = None,
    ) -> Theme:
        """Derives a complete cohesive palette from any single hex color."""
        clean_primary = primary_hex.strip()
        if not clean_primary.startswith("#"):
            clean_primary = f"#{clean_primary}"

        tint = tint_hex or derive_tint(clean_primary)
        secondary = derive_secondary(clean_primary)
        on_primary = derive_on_primary(clean_primary)

        return Theme(
            id=f"custom_{hashlib.md5(clean_primary.encode()).hexdigest()[:6]}",
            name=name,
            primary=clean_primary,
            secondary=secondary,
            tint=tint,
            on_primary=on_primary,
            text_dark="#0f172a",
        )

    def get_theme_for_spec(self, subject: SubjectSpec | None = None, theme_override: str | None = None) -> Theme:
        """
        Subject-agnostic theme resolution:
        1. Explicit override (hex code or theme ID)
        2. Subject-specified primary_color (hex)
        3. Named style match from themes.yaml
        4. Default clean universal theme (classic_blue)
        """
        target = theme_override or (subject.primary_color if subject else None)
        if target:
            clean = target.strip()
            # If it's a hex code (e.g. #1e40af or 1e40af)
            if clean.startswith("#") or (len(clean) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in clean)):
                return self.from_primary_color(clean)
            # If it's a named theme in themes.yaml
            if clean.lower() in self.themes:
                return self.themes[clean.lower()]

        return self.themes.get(self.default_theme_id, Theme(id="default", name="Default"))
