"""The report shell.

A report is a title and an ordered list of components, rendered once into a
self-contained HTML file. There is no server and no JavaScript: the viewer does
the interactive work behind a link, and everything that collapses in the report
collapses with ``<details>``.

Writing your own component is a subclass with two methods::

    class Finding(BaseComponent):
        component_type = "finding"

        def __init__(self, text, **kw):
            super().__init__(**kw)
            self.text = text

        def css(self):
            return ".rf-finding { color: var(--rf-muted); }"

        def render(self):
            return P(self.text, cls="rf-finding", id=self.id)

add it to a report with ``report.add(Finding(text="..."))``. Give your classes a
prefix of their own: ``rf-card``, ``rf-note``, ``rf-meta`` and friends are already
styled by the components here, and every stylesheet in the report lands on the
same page.

A report carries two pieces of text of its own -- ``subtitle``, and ``preamble``,
a paragraph above every block. Those are the whole of the prose a page gets;
everything else on it is a component. That is deliberate: a report the agent
writes in March and a report it writes in September have to look like the same
report, and the only way to guarantee that is for the page to be made of parts
rather than of markup.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Union

from fasthtml.common import (
    FT,
    Body,
    Details,
    Div,
    H1,
    H2,
    Head,
    Html,
    Main,
    Meta,
    P,
    Style,
    Summary,
    Title,
    to_xml,
)

__all__ = ["BaseComponent", "ComponentRegistry", "Report", "Section"]


class BaseComponent:
    """Anything a report can render.

    Subclasses implement :meth:`render` and may contribute scoped CSS through
    :meth:`css`. The report collects the CSS and inlines it once, so a component
    never has to care whether it appears once or twenty times.
    """

    component_type: str = "base"

    def __init__(self, id: Optional[str] = None) -> None:
        self.id = id or f"{self.component_type}_{uuid.uuid4().hex[:8]}"
        #: A report re-numbers the ids it generated itself; one you passed is
        #: yours. See `Report.number_blocks`.
        self.id_generated = id is None

    def css(self) -> str:
        """CSS this component needs, without a ``<style>`` tag."""
        return ""

    def children(self) -> Sequence["BaseComponent"]:
        """Components rendered inside this one.

        The report walks them when it collects CSS, so a container that composes
        other components does not have to re-declare their styles -- a grid of
        cards brings the card stylesheet with it.
        """
        return ()

    def render(self) -> FT:
        raise NotImplementedError(f"{type(self).__name__} must implement render()")

    def to_html(self) -> str:
        return to_xml(self.render())


class ComponentRegistry:
    """Name -> class map, so components can be created from a config file."""

    _components: dict = {}

    @classmethod
    def register(cls, name: str, component_class: type) -> None:
        if not issubclass(component_class, BaseComponent):
            raise TypeError(f"{component_class} must inherit from BaseComponent")
        cls._components[name] = component_class

    @classmethod
    def get(cls, name: str) -> type:
        if name not in cls._components:
            raise KeyError(
                f"Component '{name}' not found. Registered: {list(cls._components)}"
            )
        return cls._components[name]

    @classmethod
    def list(cls) -> List[str]:
        return list(cls._components)

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseComponent:
        return cls.get(name)(**kwargs)


BASE_CSS = """
:root {
  --rf-bg: #ffffff;
  --rf-fg: #16191d;
  --rf-muted: #5b6572;
  --rf-line: #dfe3e8;
  --rf-card: #fbfcfd;
  --rf-accent: #0b6bcb;
  --rf-error: #b3261e;
  --rf-radius: 10px;
}
@media (prefers-color-scheme: dark) {
  .rf-root:not(.rf-light) {
    --rf-bg: #171a1e;
    --rf-fg: #e6e9ed;
    --rf-muted: #9aa4b0;
    --rf-line: #2c3238;
    --rf-card: #1d2126;
    --rf-accent: #6cb2f7;
    --rf-error: #ff8077;
  }
}
.rf-root.rf-dark {
  --rf-bg: #171a1e;
  --rf-fg: #e6e9ed;
  --rf-muted: #9aa4b0;
  --rf-line: #2c3238;
  --rf-card: #1d2126;
  --rf-accent: #6cb2f7;
  --rf-error: #ff8077;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--rf-bg);
  color: var(--rf-fg);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
.rf-main { max-width: 1400px; margin: 0 auto; padding: 32px 24px 64px; }
.rf-title { font-size: 1.9rem; margin: 0 0 4px; letter-spacing: -0.01em; }
.rf-subtitle { color: var(--rf-muted); margin: 0 0 28px; }
/* The one paragraph a page holds of its own. Max-width, not the grid's full
   width: a line of prose across 1400px is unreadable, and this is the only text
   a reader has to read rather than look at. */
.rf-preamble { margin: -12px 0 28px; max-width: 72ch; color: var(--rf-fg); }
.rf-subtitle + .rf-preamble { margin-top: -12px; }
.rf-block { margin: 0 0 28px; }
.rf-card {
  border: 1px solid var(--rf-line);
  border-radius: var(--rf-radius);
  background: var(--rf-card);
  padding: 16px;
}
a { color: var(--rf-accent); }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--rf-line); }
th { font-weight: 600; color: var(--rf-muted); font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.03em; }
tbody tr:last-child td { border-bottom: none; }
.rf-error {
  border: 1px solid var(--rf-error);
  color: var(--rf-error);
  border-radius: var(--rf-radius);
  padding: 10px 14px;
}
"""


@dataclass
class Report:
    """An ordered list of components plus the page they render into.

    >>> report = Report(title="QC", preamble="Overlays are the model's.")
    >>> report.write("report_output.html")          # doctest: +SKIP
    """

    title: str = "Untitled Report"
    subtitle: str = ""
    #: A paragraph above every block: what to look at, what the overlays are.
    #: The only prose the page holds besides the title and the subtitle.
    preamble: str = ""
    blocks: List[BaseComponent] = field(default_factory=list)
    #: "light", "dark" or "auto" (follow the reader's system setting).
    theme: str = "auto"
    #: Extra stylesheet appended after every block's CSS.
    css: str = ""

    def __post_init__(self) -> None:
        self.blocks = [
            block if isinstance(block, BaseComponent) else _wrap(block)
            for block in self.blocks
        ]

    def add(self, block: Union[BaseComponent, FT]) -> "Report":
        """Append a component (or a raw FastHTML tree) and return the report."""
        self.blocks.append(block if isinstance(block, BaseComponent) else _wrap(block))
        return self

    def extend(self, blocks: Iterable[Union[BaseComponent, FT]]) -> "Report":
        for block in blocks:
            self.add(block)
        return self

    def number_blocks(self) -> "Report":
        """Give every block an id derived from its position, not from chance.

        `BaseComponent` falls back to a random id, which is right for a
        component on its own and wrong for a file: two runs of the same manifest
        have to write the same bytes. A component the caller named keeps its
        name.
        """
        counters: dict = {}

        def walk(block: BaseComponent) -> None:
            if block.id_generated:
                counters[block.component_type] = counters.get(block.component_type, 0) + 1
                block.id = f"{block.component_type}_{counters[block.component_type]:02d}"
            for child in block.children():
                walk(child)

        for block in self.blocks:
            walk(block)
        return self

    def render_body(self) -> FT:
        """The page body as a FastHTML tree, for embedding elsewhere."""
        self.number_blocks()
        head = [H1(self.title, cls="rf-title")]
        if self.subtitle:
            head.append(P(self.subtitle, cls="rf-subtitle"))
        if self.preamble:
            head.append(P(self.preamble, cls="rf-preamble"))
        rendered = []
        for block in self.blocks:
            try:
                out = block.render()
            except Exception as exc:  # a broken block must not lose the report
                out = Div(
                    f"{type(block).__name__} failed to render: {exc}",
                    cls="rf-error",
                )
            rendered.append(_block_div(out, block.id))
        return Main(*head, *rendered, cls="rf-main")

    def collect_css(self) -> str:
        sheets = [BASE_CSS]
        seen = set()
        for block in self.blocks:
            for sheet in _stylesheets(block):
                # Twelve cards share one stylesheet; the page gets it once.
                if sheet not in seen:
                    seen.add(sheet)
                    sheets.append(sheet)
        if self.theme != "auto":
            sheets.append(f".rf-root {{ color-scheme: {self.theme}; }}")
        if self.css:
            sheets.append(self.css)
        return "\n".join(sheet for sheet in sheets if sheet.strip())

    def to_html(self) -> str:
        theme_class = "rf-root" + ("" if self.theme == "auto" else f" rf-{self.theme}")
        page = Html(
            Head(
                Title(self.title),
                Meta(charset="utf-8"),
                Meta(name="viewport", content="width=device-width, initial-scale=1"),
                Style(self.collect_css()),
            ),
            Body(Div(self.render_body(), cls=theme_class)),
            lang="en",
        )
        return to_xml(page)

    def write(self, path) -> Path:
        """Write the report to ``path`` and return the resolved path."""
        target = Path(path)
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_html(), encoding="utf-8")
        return target


class _RawBlock(BaseComponent):
    """A bare FastHTML tree treated as a report block."""

    component_type = "raw"

    def __init__(self, node: FT, **kwargs) -> None:
        super().__init__(**kwargs)
        self.node = node

    def render(self) -> FT:
        return self.node


def _wrap(node: Union[FT, BaseComponent]) -> BaseComponent:
    if isinstance(node, BaseComponent):
        return node
    return _RawBlock(node)


def _block_div(rendered: FT, block_id: str) -> FT:
    """The `rf-block` wrapper around one rendered block.

    It carries the block's id *only* when the rendered block did not. Every frozen
    component puts `id=self.id` on its own root, so stamping it on the wrapper too
    put the same id on two nested elements -- invalid HTML, and an anchor whose
    target is whichever of the two a browser feels like. The wrapper is still
    stamped for a block that renders no id, which covers the error card a failed
    block degrades to and any component outside the frozen set, so no block loses
    its anchor and no page gains a duplicate.
    """
    if _carries_id(rendered, block_id):
        return Div(rendered, cls="rf-block")
    return Div(rendered, cls="rf-block", id=block_id)


def _carries_id(node: Any, wanted: str) -> bool:
    """Whether a rendered tree already puts `wanted` on one of its elements.

    Whole subtree, not just the root: a component that carries its id on a nested
    element has the same anchor either way, and testing only the root would put the
    id back on the wrapper and recreate the duplicate this avoids.

    Defensive about node types because `render()` may return anything FastHTML
    accepts — `NotStr` raw HTML has no `attrs` to read, and a block that fails to
    render returns a plain error `Div`.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        attrs = getattr(current, "attrs", None)
        if isinstance(attrs, dict) and attrs.get("id") == wanted:
            return True
        children = getattr(current, "children", None)
        if children:
            stack.extend(children)
    return False


def _stylesheets(component: BaseComponent) -> Iterable[str]:
    """Every stylesheet in a component's tree, outermost first."""
    queue = [component]
    while queue:
        node = queue.pop(0)
        sheet = node.css().strip()
        if sheet:
            yield sheet
        queue.extend(node.children())


class Section(BaseComponent):
    """A titled group of blocks; `<details>` when it collapses."""

    component_type = "section"

    def __init__(
        self,
        title: str,
        blocks: Iterable[Union[BaseComponent, FT]] = (),
        *,
        subtitle: str = "",
        collapsible: bool = False,
        open: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.subtitle = subtitle
        self.collapsible = collapsible
        self.open = open
        self.blocks: List[BaseComponent] = [
            block if isinstance(block, BaseComponent) else _wrap(block)
            for block in blocks
        ]

    def add(self, block: Union[BaseComponent, FT]) -> "Section":
        self.blocks.append(block if isinstance(block, BaseComponent) else _wrap(block))
        return self

    def children(self) -> Sequence[BaseComponent]:
        return self.blocks

    def css(self) -> str:
        return """
.rf-section > summary {
  cursor: pointer;
  font-weight: 600;
  font-size: 1.15rem;
  margin-bottom: 12px;
}
.rf-section:not(.rf-collapsible) > h2 { margin: 0 0 4px; font-size: 1.15rem; }
.rf-section .rf-section-body > .rf-block:last-child { margin-bottom: 0; }
"""

    def render(self) -> FT:
        body = Div(
            *[_block_div(block.render(), block.id) for block in self.blocks],
            cls="rf-section-body",
        )
        if self.collapsible:
            head = Summary(self.title)
            rest = [body]
            attrs = {"open": ""} if self.open else {}
        else:
            head = H2(self.title, cls="rf-section-title")
            rest = (
                [P(self.subtitle, cls="rf-subtitle"), body] if self.subtitle else [body]
            )
            attrs = {}
        tag = Details if self.collapsible else Div
        return tag(head, *rest, cls="rf-section", id=self.id, **attrs)
