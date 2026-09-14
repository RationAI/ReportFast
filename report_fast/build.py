"""The one-call report.

Everything below is a convenience over the primitives -- ``XopatSession`` for
one session, ``SessionTemplate``/``sessions_from_folder`` for many, the
components in :mod:`report_fast.components`` for what goes on the page. Reach
for :func:`build_report` when the report is "these slides, these masks, some
numbers"; write the ~10 lines yourself when it is anything else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence, Union

from .components.chart import Chart
from .components.metrics import MetricTable
from .components.prose import Prose
from .components.slide_grid import SlideCard, SlideGrid
from .config import SessionPreset
from .core import BaseComponent, Report
from .masks import Background, Mask, sessions_from_masks
from .mlflow import Mlflow
from .session import (
    SLIDE_PATTERNS,
    SessionTemplate,
    Slide,
    XopatSession,
    as_session,
    sessions_from_folder,
    sessions_from_paths,
)
from .xopat import XopatEndpoint

__all__ = ["build_report", "sessions_for"]


def sessions_for(
    slides: Union[None, Slide, Iterable[Slide]] = None,
    *,
    directory: Union[str, Path, None] = None,
    patterns: Sequence[str] = SLIDE_PATTERNS,
    recursive: bool = True,
    masks: Union[None, Sequence[Any], Callable[[Slide], Sequence[Any]]] = None,
    layers: Sequence[Any] = (),
    layers_for: Optional[Callable[[Slide], Sequence[Any]]] = None,
    names: Optional[Callable[[Slide], str]] = None,
    template: Optional[SessionTemplate] = None,
    params: Optional[Mapping[str, Any]] = None,
    endpoint: Optional[XopatEndpoint] = None,
    preset: Union[None, SessionPreset, Mapping[str, Any], str, Path] = None,
) -> List[XopatSession]:
    """Sessions for a folder, a list of paths, or a list of ready-made sessions.

    `masks` is the shorthand for the common case: a sequence of mask files over
    every slide, or a function from slide to that slide's masks. Named masks
    with their own source (`Mask` over `Drive`/`MlflowRun`) go through
    :func:`~report_fast.masks.sessions_from_masks` instead — see `background=`
    on :func:`build_report`.
    """
    offered = list(layers) + ([] if callable(masks) else list(masks or ()))
    if any(isinstance(layer, Mask) for layer in offered):
        raise ValueError(
            "Mask(...) carries its own source; pass it to"
            " build_report(background=..., masks=[...])."
        )
    per_slide = masks if callable(masks) else None
    shared = list(layers) + (list(masks or ()) if not callable(masks) else [])
    every = layers_for if per_slide is None else _both(layers_for, per_slide)

    if directory is not None:
        return sessions_from_folder(
            directory,
            patterns=patterns,
            recursive=recursive,
            layers=shared,
            layers_for=every,
            names=names,
            template=template,
            params=params,
            endpoint=endpoint,
            preset=preset,
        )
    if slides is None:
        raise ValueError("Pass slides= or directory=.")
    paths = [slides] if isinstance(slides, (str, Path)) else list(slides)
    if all(
        isinstance(item, (XopatSession, SessionTemplate, Mapping)) for item in paths
    ):
        # Already sessions (or a pasted config / a bound template): nothing to
        # scan, nothing to name -- the label came with them.
        return [
            as_session(item, params=params, endpoint=endpoint, preset=preset)
            for item in paths
        ]
    return sessions_from_paths(
        paths,
        layers=shared,
        layers_for=every,
        names=names,
        template=template,
        params=params,
        endpoint=endpoint,
        preset=preset,
    )


def _both(*functions):
    chosen = [function for function in functions if function is not None]
    if not chosen:
        return None
    return lambda slide: [
        layer for function in chosen for layer in (function(slide) or ())
    ]


def build_report(
    slides: Union[None, Slide, Iterable[Slide]] = None,
    *,
    directory: Union[str, Path, None] = None,
    patterns: Sequence[str] = SLIDE_PATTERNS,
    recursive: bool = True,
    masks: Union[None, Sequence[Any], Callable[[Slide], Sequence[Any]]] = None,
    layers: Sequence[Any] = (),
    layers_for: Optional[Callable[[Slide], Sequence[Any]]] = None,
    names: Optional[Callable[[Slide], str]] = None,
    template: Optional[SessionTemplate] = None,
    params: Optional[Mapping[str, Any]] = None,
    endpoint: Optional[XopatEndpoint] = None,
    preset: Union[None, SessionPreset, Mapping[str, Any], str, Path] = None,
    background: Optional[Background] = None,
    only: Sequence[str] = (),
    min_layers: int = 0,
    flow: Optional[Mlflow] = None,
    title: str = "Report",
    subtitle: str = "",
    intro: Union[str, Sequence[str]] = "",
    blocks: Iterable[BaseComponent] = (),
    metrics: Any = None,
    charts: Union[None, Mapping[str, Any], Sequence[Any]] = None,
    grid: Optional[Mapping[str, Any]] = None,
    theme: str = "auto",
    out: Union[str, Path, None] = None,
) -> Report:
    """Slides, masks and numbers in; an HTML report out.

    Args:
        slides: Backgrounds -- paths, or sessions/configs/links already built.
        directory: Scan this folder instead of listing slides.
        masks: Mask files for every slide, or ``slide -> [mask, ...]``. With
            ``background=`` this is the list of :class:`~report_fast.masks.Mask`
            overlays instead, each carrying its own ``Drive``/``MlflowRun``.
        background: Where the backgrounds come from -- a ``Drive`` folder, a
            ``MlflowRun``'s artifacts, a folder path, or a list of slides. Takes
            over session building from ``slides=``/``directory=``.
        only: Case ids to report, in this order; empty means all of them.
        min_layers: Drop cases with fewer overlays than this.
        flow: MLflow client for ``MlflowRun`` sources; built from the
            environment when one is needed and none was passed.
        layers: Layers as layer dicts (type, params, per-layer options).
        layers_for: ``slide -> [layer, ...]`` for per-slide overlays.
        template: Build each session by binding this pasted session.
        intro: Paragraph or list of paragraphs under the title.
        blocks: Extra components, appended after the generated ones.
        metrics: Anything ``MetricTable`` accepts.
        charts: Path/bytes, or ``{caption: image}``.
        grid: ``SlideGrid`` overrides (``min_width``, ``collapsible``...);
            pass ``False`` for one card per row instead of a grid.
        out: Also write the report here.

    Returns:
        The ``Report``; call ``.to_html()`` or ``.write(path)`` on it.
    """
    report = Report(title=title, subtitle=subtitle, theme=theme)
    paragraphs = [intro] if isinstance(intro, str) and intro else list(intro or ())
    if paragraphs:
        report.add(Prose(paragraphs=paragraphs))

    if background is not None:
        if slides is not None or directory is not None:
            raise ValueError(
                "background= replaces slides= and directory=; pass one or the other."
            )
        made = sessions_from_masks(
            background,
            list(masks or ()),
            only=only,
            min_layers=min_layers,
            endpoint=endpoint,
            flow=flow,
            params=dict(params or {}),
        )
    else:
        made = sessions_for(
            slides,
            directory=directory,
            patterns=patterns,
            recursive=recursive,
            masks=masks,
            layers=layers,
            layers_for=layers_for,
            names=names,
            template=template,
            params=params,
            endpoint=endpoint,
            preset=preset,
        )
    if made:
        if grid is False:
            for session in made:
                report.add(SlideCard(session, endpoint=endpoint))
        else:
            report.add(SlideGrid(made, endpoint=endpoint, **(dict(grid or {}))))

    if metrics is not None:
        report.add(MetricTable(metrics))
    for caption, image in _charts(charts):
        report.add(Chart(image, caption=caption, alt=caption))
    report.extend(blocks)

    if out is not None:
        report.write(out)
    return report


def _charts(charts: Union[None, Mapping[str, Any], Sequence[Any]]):
    if not charts:
        return []
    if isinstance(charts, Mapping):
        return list(charts.items())
    if isinstance(charts, (str, bytes, Path)):
        return [("", charts)]
    return [("", chart) for chart in charts]
