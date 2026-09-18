"""Slides: one card per session, arranged in a grid.

The card is the report's only xOpat affordance. It shows a thumbnail, labels
the session, links the whole card to the viewer with the session in the
fragment, and folds the session JSON itself into a `<details>` so a reader can
see -- and paste back -- exactly what the link carries.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Union

from fasthtml.common import (
    FT,
    A,
    Details,
    Div,
    Figcaption,
    Figure,
    Iframe,
    Img,
    P,
    Pre,
    Span,
    Summary,
)

from ..core import BaseComponent, ComponentRegistry
from ..session import XopatSession, as_session
from ..xopat import XopatEndpoint

__all__ = ["SlideCard", "SlideGrid"]


class SlideCard(BaseComponent):
    """A thumbnail, a label and a viewer link for one session.

    Args:
        session: A :class:`XopatSession`, or anything ``as_session`` accepts --
            a path, a viewer link, a pasted config dict.
        endpoint: Deployment to link against, overriding the session's own.
        thumbnails: Set false for deployments without a thumbnail service.
        thumbnail_size: Thumbnail edge length in pixels.
        show_session: Fold the session JSON into the card.
        note: One line under the label, for whatever the reader needs to know.
        name: Label override; defaults to the session's own.
    """

    component_type = "slide-card"

    def __init__(
        self,
        session: Union[XopatSession, Mapping[str, Any], str, Any],
        *,
        endpoint: Optional[XopatEndpoint] = None,
        thumbnails: bool = True,
        thumbnail_size: int = 512,
        show_session: bool = True,
        note: str = "",
        name: Optional[str] = None,
        embed: bool = False,
        embed_height: str = "70vh",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.session = as_session(session, endpoint=endpoint, name=name)
        self.endpoint = endpoint
        self.thumbnails = thumbnails
        self.thumbnail_size = thumbnail_size
        self.show_session = show_session
        self.note = note
        self.embed = embed
        self.embed_height = embed_height

    @property
    def url(self) -> str:
        return self.session.url(self.endpoint)

    def css(self) -> str:
        return """
.rf-slide-card { margin: 0; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.rf-slide-card .rf-thumbs { display: flex; gap: 6px; flex-wrap: wrap; min-width: 0; }
.rf-slide-card .rf-thumb {
  flex: 1 1 110px; aspect-ratio: 1 / 1; overflow: hidden;
  border: 1px solid var(--rf-line); border-radius: 8px; background: var(--rf-card);
  display: flex; align-items: center; justify-content: center; min-width: 0; max-width: 100%;
}
.rf-slide-card .rf-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.rf-slide-card .rf-thumb.rf-no-img { color: var(--rf-muted); font-size: 0.8rem; }
.rf-slide-card figcaption { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.rf-slide-card .rf-name { font-weight: 600; overflow-wrap: anywhere; }
.rf-slide-card .rf-meta, .rf-slide-card .rf-note { color: var(--rf-muted); font-size: 0.85rem; }
.rf-slide-card details { font-size: 0.8rem; min-width: 0; }
.rf-slide-card summary { cursor: pointer; color: var(--rf-muted); }
.rf-slide-card pre {
  max-height: 240px; overflow: auto; background: var(--rf-bg);
  white-space: pre-wrap; word-break: break-word;
  border: 1px solid var(--rf-line); border-radius: 8px; padding: 8px; margin: 8px 0 0;
}
"""

    def render(self) -> FT:
        session = self.session
        name = session.names[0] if session.names else "session"
        media = (
            self._embed() if self.embed else Div(*self._thumbnails(), cls="rf-thumbs")
        )
        return Figure(
            media,
            Figcaption(
                Span(name, cls="rf-name"),
                Span(self._meta(), cls="rf-meta"),
                *[P(self.note, cls="rf-note")] if self.note else [],
                *self._session_details(),
                A("Open in xOpat", href=self.url, target="_blank", rel="noopener"),
            ),
            cls="rf-card rf-slide-card",
            id=self.id,
        )

    def _embed(self) -> FT:
        """The viewer itself in a frame.

        Worth the bytes only when the reader cannot be expected to follow a
        link: a kiosk page, a slide deck. Otherwise the thumbnail is smaller,
        faster, and does not boot a second WebGL context per card.
        """
        return Iframe(
            src=self.url,
            title=self.session.names[0] if self.session.names else "xOpat",
            loading="lazy",
            style=f"width: 100%; height: {self.embed_height}; border: 1px solid var(--rf-line); border-radius: 8px;",
        )

    def _thumbnails(self) -> List[FT]:
        session = self.session
        if not self.thumbnails or not session.data or not session.background:
            return [Div("no preview", cls="rf-thumb rf-no-img")]
        cells = []
        for index in range(len(session.background)):
            if not session.has_thumbnail(index, self.endpoint):
                continue
            cells.append(
                A(
                    Img(
                        src=session.thumbnail(
                            self.endpoint, self.thumbnail_size, index
                        ),
                        alt=session.names[index] if index < len(session.names) else "",
                        loading="lazy",
                    ),
                    href=self.url_opening(index),
                    target="_blank",
                    rel="noopener",
                    cls="rf-thumb",
                    title=session.names[index] if index < len(session.names) else "",
                )
            )
        return cells or [Div("no preview", cls="rf-thumb rf-no-img")]

    def url_opening(self, index: int) -> str:
        """The session's link with background `index` in the open viewport.

        A single-background session needs no variant -- and emitting one would
        add bytes, and a param, to every link in an ordinary report.
        """
        if len(self.session.background) < 2:
            return self.url
        variant = self.session.copy()
        active = variant.params.get("activeBackgroundIndex")
        variant.params["activeBackgroundIndex"] = (
            [index] if isinstance(active, list) else index
        )
        return variant.url(self.endpoint)

    def _meta(self) -> str:
        session = self.session
        parts = []
        if len(session.background) > 1:
            parts.append(f"{len(session.background)} backgrounds")
        layers = sum(
            len(visualization.get("shaders") or {})
            for visualization in session.visualizations
            if isinstance(visualization, dict)
        )
        if layers:
            parts.append(f"{layers} layers")
        # A count, not the keys. This line used to render the literal string
        # "slide-info" on every card of a real report: a plugin id from the session
        # schema, which tells a reader nothing about the slide and exposes a name
        # from the wire format where a person expects prose. The number stays
        # because plugins are loaded on boot and can change what a card looks like,
        # which is worth knowing when one renders oddly. The names themselves are
        # in the session JSON and no label table for them lives here: a hand-typed
        # plugin-id-to-label mapping would be a second source of truth about a
        # deployment this library does not run, which is the thing the derived
        # schema used to be and why it was deleted.
        if session.plugins:
            parts.append(f"{len(session.plugins)} plugins")
        return " · ".join(parts)

    def _session_details(self) -> List[FT]:
        if not self.show_session:
            return []
        pretty = json.dumps(self.session.to_config(), indent=2, ensure_ascii=False)
        return [Details(Summary("session"), Pre(pretty))]


class SlideGrid(BaseComponent):
    """Sessions as a responsive grid; collapses with `<details>`, no JavaScript.

    Args:
        sessions: Sessions, or anything ``as_session`` accepts per entry.
        title: Heading above the grid.
        collapsible: Wrap the grid in a `<details>` (open by default).
        min_width: Narrowest card the grid will lay out.
        card: Defaults for every card (``thumbnails``, ``show_session``...).
    """

    component_type = "slide-grid"

    def __init__(
        self,
        sessions: Iterable[Any] = (),
        *,
        title: str = "",
        endpoint: Optional[XopatEndpoint] = None,
        collapsible: bool = False,
        open: bool = True,
        min_width: int = 260,
        card: Optional[Mapping[str, Any]] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        defaults = {"endpoint": endpoint}
        defaults.update(card or {})
        self.title = title
        self.endpoint = endpoint
        self.collapsible = collapsible
        self.open = open
        self.min_width = min_width
        self.card_defaults = defaults
        self.cards: List[SlideCard] = []
        #: Position of every card whose id this grid derived, rather than being
        #: handed one. The grid's own id is not final until the report numbers
        #: it, so a derived card id has to be re-derived at render -- baking the
        #: grid's id in at construction would put a random hex into the page and
        #: break "same manifest, same bytes".
        self._derived: set = set()
        for index, item in enumerate(sessions):
            self._card(item, index)

    def _card(self, session: Any, index: int, **card_kwargs) -> SlideCard:
        defaults = dict(self.card_defaults)
        defaults.update(card_kwargs)
        named = defaults.pop("id", None)
        card = SlideCard(session, id=named or f"{self.id}_card{index}", **defaults)
        if named is None:
            self._derived.add(index)
        self.cards.insert(index, card)
        return card

    def add(self, session: Any, **card_kwargs) -> "SlideGrid":
        """Append one session to the grid (fluent)."""
        self._card(session, len(self.cards), **card_kwargs)
        return self

    def _retitle(self) -> None:
        """Re-derive the card ids this grid named, from its id as it is now."""
        for index in self._derived:
            self.cards[index].id = f"{self.id}_card{index}"

    @property
    def sessions(self) -> List[XopatSession]:
        return [card.session for card in self.cards]

    def children(self) -> Sequence[BaseComponent]:
        return self.cards

    def css(self) -> str:
        return f"""
.rf-slide-grid {{
  display: grid; gap: 16px;
  grid-template-columns: repeat(auto-fill, minmax({self.min_width}px, 1fr));
}}
/* Grid items default to min-width: auto, so one wide child -- a session's
   JSON, a long DataID -- widens the track and pushes the page past the
   viewport. Cards stay inside their column and scroll instead. */
.rf-slide-grid > * {{ min-width: 0; }}
.rf-slide-grid-wrap > summary {{
  cursor: pointer; font-weight: 600; font-size: 1.15rem; margin-bottom: 12px;
}}
.rf-slide-grid-wrap h2 {{ margin: 0 0 12px; font-size: 1.15rem; }}
"""

    def render(self) -> FT:
        self._retitle()
        grid = Div(*[card.render() for card in self.cards], cls="rf-slide-grid")
        blocks: Sequence[FT]
        if self.title and self.collapsible:
            blocks = [Summary(self.title), grid]
        elif self.title:
            blocks = [_heading(self.title), grid]
        else:
            blocks = [grid]
        tag = Details if self.collapsible else Div
        attrs = {"open": ""} if self.collapsible and self.open else {}
        return tag(*blocks, cls="rf-slide-grid-wrap", id=self.id, **attrs)


def _heading(text: str) -> FT:
    from fasthtml.common import H2

    return H2(text)


ComponentRegistry.register("slide-card", SlideCard)
ComponentRegistry.register("slide-grid", SlideGrid)
