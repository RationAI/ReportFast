"""Text blocks: paragraphs, headings, and anything you already have as HTML."""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Union

from fasthtml.common import FT, A, Div, H2, H3, Li, NotStr, P, Ul

from ..core import BaseComponent, ComponentRegistry

__all__ = ["Prose", "Heading", "Bullets", "RawHtml", "LinkList"]


class Heading(BaseComponent):
    """A section heading, one level under the report title."""

    component_type = "heading"

    def __init__(self, text: str, level: int = 2, **kwargs) -> None:
        super().__init__(**kwargs)
        self.text = text
        self.level = level

    def css(self) -> str:
        return ".rf-heading { margin: 0 0 6px; font-size: 1.15rem; }"

    def render(self) -> FT:
        tag = {1: H2, 2: H2, 3: H3}.get(self.level, H2)
        return tag(self.text, cls="rf-heading", id=self.id)


class Prose(BaseComponent):
    """Paragraphs of text. Blank lines split paragraphs; nothing else is parsed.

    Pass ``text`` for one paragraph or ``paragraphs`` for several.
    """

    component_type = "prose"

    def __init__(
        self,
        text: str = "",
        paragraphs: Optional[Iterable[str]] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.paragraphs: List[str] = [
            chunk.strip()
            for chunk in (text.split("\n\n") if text else [])
            if chunk.strip()
        ]
        if paragraphs:
            self.paragraphs.extend(str(chunk).strip() for chunk in paragraphs)

    def css(self) -> str:
        return ".rf-prose p { margin: 0 0 10px; max-width: 75ch; } .rf-prose p:last-child { margin-bottom: 0; }"

    def render(self) -> FT:
        return Div(*[P(text) for text in self.paragraphs], cls="rf-prose", id=self.id)


class Bullets(BaseComponent):
    """An unordered list of short facts."""

    component_type = "bullets"

    def __init__(self, items: Sequence[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.items = list(items)

    def render(self) -> FT:
        return Ul(*[Li(item) for item in self.items], cls="rf-bullets", id=self.id)


class LinkList(BaseComponent):
    """Named external links -- other reports, a dashboard, a paper."""

    component_type = "link-list"

    def __init__(self, links: Union[dict, Iterable[tuple]], **kwargs) -> None:
        super().__init__(**kwargs)
        self.links = list(links.items()) if isinstance(links, dict) else list(links)

    def css(self) -> str:
        return ".rf-links { display: flex; gap: 18px; flex-wrap: wrap; }"

    def render(self) -> FT:
        return Div(
            *[
                A(label, href=url, target="_blank", rel="noopener")
                for label, url in self.links
            ],
            cls="rf-links",
            id=self.id,
        )


class RawHtml(BaseComponent):
    """HTML you wrote elsewhere, dropped into the report unescaped.

    The escape hatch for a charting library, another tool's embed, or markup an
    agent produced: the report does not inspect or reformat it.
    """

    component_type = "raw-html"

    def __init__(self, html: str = "", node: Optional[FT] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.html = html
        self.node = node

    def render(self) -> FT:
        if self.node is not None:
            return self.node
        return Div(NotStr(self.html), id=self.id)


ComponentRegistry.register("prose", Prose)
ComponentRegistry.register("heading", Heading)
ComponentRegistry.register("raw-html", RawHtml)
