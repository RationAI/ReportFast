"""An image in the report, embedded so the file stays a single artifact."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Union

from fasthtml.common import FT, Figcaption, Figure, Img, P

from ..core import BaseComponent, ComponentRegistry

__all__ = ["Chart"]


class Chart(BaseComponent):
    """A PNG figure, inlined as a data URL by default.

    A report that links to `chart.png` is two files that get separated; a report
    that embeds it is one file that can be mailed. Pass ``external=True`` when
    the image is big enough that inlining is the wrong trade.

    Args:
        image: Path to an image file, raw bytes, or a data/http URL.
        alt: Text for readers who cannot see it.
        caption: Line under the image.
        width: Display width in CSS units.
        external: Reference the path as-is instead of embedding it.
    """

    component_type = "chart"

    def __init__(
        self,
        image: Union[str, bytes, Path] = "",
        *,
        alt: str = "",
        caption: str = "",
        width: str = "100%",
        external: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.src = self._src(image, external)
        self.alt = alt
        self.caption = caption
        self.width = width

    @staticmethod
    def _src(image: Union[str, bytes, Path], external: bool) -> str:
        if isinstance(image, bytes):
            return "data:image/png;base64," + base64.b64encode(image).decode("ascii")
        text = str(image)
        if text.startswith(("http://", "https://", "data:")):
            return text
        path = Path(text)
        if external or not path.exists():
            # Either asked for a reference, or it is one already (a root-relative
            # URL served next to the report).
            return text
        mime = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @classmethod
    def from_matplotlib(
        cls, figure, *, alt: str = "", caption: str = "", dpi: int = 110, **kwargs
    ):
        """Embed a matplotlib figure without the caller touching a buffer."""
        import io

        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight")
        return cls(buffer.getvalue(), alt=alt, caption=caption, **kwargs)

    def css(self) -> str:
        return (
            ".rf-chart { margin: 0; } "
            ".rf-chart img { display: block; border: 1px solid var(--rf-line);"
            " border-radius: var(--rf-radius); background: #fff; } "
            ".rf-chart figcaption { color: var(--rf-muted); font-size: 0.85rem; margin-top: 6px; }"
        )

    def render(self) -> FT:
        if not self.src:
            return P("no image", cls="rf-muted")
        return Figure(
            Img(src=self.src, alt=self.alt, style=f"max-width: {self.width};"),
            *[Figcaption(self.caption)] if self.caption else [],
            cls="rf-chart",
            id=self.id,
        )


ComponentRegistry.register("chart", Chart)
