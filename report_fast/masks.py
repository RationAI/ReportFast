"""Masks, where they live, and how they are drawn.

The shape of a real overlay job: a background, a list of masks over it, and each
mask says where its files are and how the layer looks. That is the whole idea
here — ``Mask`` is the layer, :class:`Drive` and :class:`MlflowRun` are the two
places its files come from, and :func:`sessions_from_masks` pairs them with
slides and builds one :class:`~report_fast.session.XopatSession` per case.

::

    from report_fast import Drive, Mask, MlflowRun, Report, SlideGrid
    from report_fast import sessions_from_masks

    sessions = sessions_from_masks(
        Drive("/mnt/data/colon/dysplasia"),
        [
            Mask("Tissue", MlflowRun("97084241311949189445f864d42e9d4e", "tissue_masks"),
                 color="#ffff00", opacity=0.5),
            Mask("Grades", MlflowRun("41d5e1d7d43641ea8f645f9b7945e9f7", "annot_masks"),
                 classes=3, palette=["#ffffff", "#ff0000", "#00ff00"]),
            Mask("epithelium", Drive("/mnt/projects/.../epithelium_masks/downscale")),
        ],
        only=["1094_18_HE_0", "8625_13_HE_A"],  # the cases, in report order
        min_layers=3,
    )
    Report(title="QC", blocks=[SlideGrid(sessions=sessions)]).write("report.html")

Nothing is read or downloaded. A mask becomes the DataID the tile server
resolves; files are matched to slides by stem, so ``case_001.svs`` and
``case_001.tiff`` are the same case — the rule the original tool used.

Three behaviours here are the reason this module exists rather than being a loop
in the report script. ``only`` is the cohort, in report order, taken from a
listing rather than guessed. A mask that matched **no** case at all raises — a
source nobody hits is a mistyped run id or artifact directory, and the alternative
is a report with a silently empty column that still looks finished. ``min_layers``
then drops the cases that matched *some* masks but too few, recording them in
``dropped`` so the report can say what it filtered: a slide with one of three
overlays is either a real gap or a wrong source, and only the caller knows which.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path, PurePosixPath
from typing import (
    Any,
    Collection,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
)

from .layer import colormap_layer, heatmap_layer
from .mlflow import SLIDE_SUFFIXES, Mlflow
from .session import SLIDE_PATTERNS, XopatSession
from .xopat import XopatEndpoint, mount_path

__all__ = [
    "CaseMatrix",
    "Drive",
    "MlflowRun",
    "Mask",
    "MaskSource",
    "case_matrix",
    "sessions_from_masks",
]

#: What ``background=`` and ``masks=`` accept besides a source object.
Background = Union["MaskSource", str, Path, Iterable[Union[str, Path]]]


def _stem(data_id: str) -> str:
    """The case id of a file: its name without the suffix."""
    return PurePosixPath(data_id.rsplit("/", 1)[-1]).stem


def _matches(name: str, patterns: Collection[str]) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(pattern.lstrip("*").lower()) for pattern in patterns)


def _indexed(files: Iterable[str]) -> Dict[str, str]:
    """``{case stem: DataID}``, first file wins when a stem repeats."""
    by_stem: Dict[str, str] = {}
    for data_id in files:
        by_stem.setdefault(_stem(data_id), data_id)
    return by_stem


class MaskSource:
    """Where a set of masks (or backgrounds) lives: ``{case stem: DataID}``.

    Subclasses answer one question — :meth:`index`. Write your own when the
    files come from somewhere other than a mount or a run:

        class FromInventory(MaskSource):
            def index(self, flow, endpoint):
                return {case: f"slides/{case}.svs" for case in inventory()}
    """

    def index(
        self, flow: Optional[Mlflow], endpoint: XopatEndpoint
    ) -> Dict[str, str]:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class Drive(MaskSource):
    """Files in a mounted folder (or several), as the image server sees them.

    Args:
        dir_name: Folder to walk, or a list of folders.
        patterns: Name patterns to keep. Defaults to the slide suffixes.
        recursive: Look inside subfolders too.
    """

    dir_name: Union[str, Path, Sequence[Union[str, Path]]]
    patterns: Collection[str] = SLIDE_PATTERNS
    recursive: bool = True

    def index(
        self, flow: Optional[Mlflow] = None, endpoint: Optional[XopatEndpoint] = None
    ) -> Dict[str, str]:
        root = endpoint.mount_root if endpoint else None
        folders = (
            [self.dir_name]
            if isinstance(self.dir_name, (str, Path))
            else list(self.dir_name)
        )
        found: List[str] = []
        for folder in folders:
            directory = Path(folder)
            if directory.is_file():
                candidates = [directory]
            elif directory.is_dir():
                walk = directory.rglob("*") if self.recursive else directory.glob("*")
                candidates = sorted(path for path in walk if path.is_file())
            else:
                raise FileNotFoundError(f"{directory} is not on this machine")
            found.extend(
                mount_path(path, root)
                for path in candidates
                if _matches(path.name, self.patterns)
            )
        return _indexed(found)


@dataclass(frozen=True)
class MlflowRun(MaskSource):
    """Files in the artifacts of an MLflow run.

    Args:
        run_id: The run that holds them.
        path: Artifact subdirectory, e.g. ``"tile_masks/blur"``.
        patterns: Suffixes to keep, or ``None`` for every file.
        recursive: Search inside the subdirectories of `path`.
    """

    run_id: str
    path: str = ""
    patterns: Optional[Collection[str]] = SLIDE_SUFFIXES
    recursive: bool = False

    def index(
        self, flow: Optional[Mlflow] = None, endpoint: Optional[XopatEndpoint] = None
    ) -> Dict[str, str]:
        client = flow or Mlflow.from_env()
        return _indexed(
            client.slides(
                self.run_id,
                self.path,
                patterns=self.patterns,
                recursive=self.recursive,
            )
        )


@dataclass(frozen=True)
class Mask:
    """One overlay layer: its files, its label, how it is drawn.

    With ``classes`` the layer is a class map (a v3 ``colormap``: one colour per
    class, ``palette`` of them); without it, a heatmap tinted ``color``. The
    defaults are the ones a bare mask got in the original tool.

    Args:
        name: Layer label in the viewer's layer panel.
        source: Where this mask's files are (:class:`Drive`, :class:`MlflowRun`,
            or your own :class:`MaskSource`).
        color: Heatmap colour.
        opacity: Layer alpha, 0..1.
        visible: Drawn when the report opens. Off by default — with more than a
            couple of layers the picture is the one the reader picks.
        classes: Class count, which makes this a class map.
        palette: One colour per class; required with ``classes``.
        breaks: ``classes - 1`` class boundaries. Defaults to even cuts.
        mask: One 0/1 per class, picking which classes are painted —
            ``[0, 1, 1]`` leaves class 0, the background, clear.
        params: Further v3 layer params this library does not model, handed to
            the layer as written. Anything here skips the declared-parameter
            check, so a layer that ignores one of them says nothing.
    """

    name: str
    source: MaskSource
    color: str = "#fff705"
    opacity: float = 1.0
    visible: bool = False
    classes: Optional[int] = None
    palette: Optional[Sequence[Union[str, Sequence[float]]]] = None
    breaks: Optional[Sequence[float]] = None
    mask: Optional[Sequence[int]] = None
    params: Optional[Mapping[str, Any]] = None

    def layer(self, data_id: str) -> Dict[str, Any]:
        """This mask's layer dict for one slide, once its own file is known.

        A plain dict: `classes`/`palette` draw a `colormap`, anything else a
        `heatmap`, and `params` rides along unchanged on top. See
        :mod:`report_fast.layer` for the two shapes and where they come from.
        """
        if self.classes is not None:
            if not self.palette:
                raise ValueError(
                    f"Mask({self.name!r}): {self.classes} classes need a palette"
                )
            layer = colormap_layer(
                data_id,
                name=self.name,
                classes=self.classes,
                palette=list(self.palette),
                opacity=self.opacity,
                breaks=list(self.breaks) if self.breaks is not None else None,
                mask=list(self.mask) if self.mask is not None else None,
                params=dict(self.params or {}),
            )
        else:
            layer = heatmap_layer(
                data_id,
                name=self.name,
                color=self.color,
                opacity=self.opacity,
                params=dict(self.params or {}),
            )
        layer["visible"] = self.visible
        return layer


def _source_of(background: Background) -> MaskSource:
    """Whatever ``background=`` was given, as a source."""
    if isinstance(background, MaskSource):
        return background
    if isinstance(background, (str, Path)):
        return Drive(background)
    if isinstance(background, Iterable):
        paths = list(background)
        if not paths:
            raise ValueError("background= is empty.")
        if all(isinstance(path, (str, Path)) for path in paths):
            return _Given(paths)
        raise ValueError(
            "background= takes a Drive/MlflowRun, a folder, or a list of paths."
        )
    raise ValueError(f"background= cannot be a {type(background).__name__}.")


class _Given(MaskSource):
    """Backgrounds the caller listed by hand, kept in their order."""

    def __init__(self, paths: Sequence[Union[str, Path]]):
        self.paths = list(paths)

    def index(
        self, flow: Optional[Mlflow] = None, endpoint: Optional[XopatEndpoint] = None
    ) -> Dict[str, str]:
        root = endpoint.mount_root if endpoint else None
        return {
            _stem(mount_path(path, root)): mount_path(path, root) for path in self.paths
        }


def _label(source: MaskSource) -> str:
    """A source's name for a human, e.g. ``MlflowRun(970842…, tissue_masks)``.

    Filtering the knobs out of the repr is what keeps a plan readable; the repr
    stays the identity key, because two sources that differ in a pattern are
    genuinely two listings.
    """
    if not is_dataclass(source):
        return type(source).__name__
    shown = [
        str(getattr(source, field.name))
        for field in fields(source)
        if field.name not in {"patterns", "recursive"}
    ]
    return f"{type(source).__name__}({', '.join(shown)})"


@dataclass(frozen=True)
class CaseMatrix:
    """What a background and a set of masks add up to, before there is a report.

    Attributes:
        sessions: The cases that survived, in report order.
        coverage: ``{mask name: cases that got a file from it}`` — the number to
            read when a layer looks missing.
        dropped: ``{case: layer count}`` for cases under ``min_layers``, so a
            filtered report can say what it filtered.
        sources: ``{source label: files found}``, deduplicated: masks sharing a
            source are listed once, and this says so.
    """

    sessions: List[XopatSession]
    coverage: Dict[str, int]
    dropped: Dict[str, int]
    sources: Dict[str, int]


def case_matrix(
    background: Background,
    masks: Sequence[Mask] = (),
    *,
    only: Sequence[str] = (),
    min_layers: int = 0,
    endpoint: Optional[XopatEndpoint] = None,
    flow: Optional[Mlflow] = None,
    params: Optional[Dict[str, Any]] = None,
) -> CaseMatrix:
    """One session per case: a background, and every mask that has a file for it.

    Args:
        background: :class:`Drive` folder, :class:`MlflowRun` artifacts, a folder
            path, or the list of slides itself.
        masks: The overlays, in layer order.
        only: Case ids to include, **in this order**. Empty means every case the
            background holds.
        min_layers: Drop cases with fewer overlays than this — a case missing
            nine of eleven masks is usually a retrieval mistake, not a finding.
        endpoint: Deployment the sessions link against.
        flow: MLflow client, built from the environment when a run is read
            without one.
        params: Session ``params`` for every case.

    Returns:
        One :class:`~report_fast.session.XopatSession` per case, report order.
    """
    source = _source_of(background)
    wanted = list(only) or None
    every = source.index(flow, endpoint)
    cases = wanted if wanted is not None else list(every)
    unknown = [case for case in cases if case not in every]
    if unknown:
        raise ValueError(
            f"{len(unknown)} of the cases are not in the background: "
            + ", ".join(unknown[:5])
            + ("..." if len(unknown) > 5 else "")
        )

    for mask in masks:
        if not isinstance(mask, Mask):
            raise ValueError(
                "masks= takes Mask(name, source, ...) entries, not "
                + type(mask).__name__
            )
    repeated = {
        mask.name for mask in masks if [m.name for m in masks].count(mask.name) > 1
    }
    if repeated:
        raise ValueError(
            "Every Mask needs its own name; these repeat: "
            + ", ".join(sorted(repeated))
        )

    listed: Dict[str, Dict[str, str]] = {}
    counts: Dict[str, int] = {_label(source): len(every)}

    def index_of(source: MaskSource) -> Dict[str, str]:
        """One listing per distinct source, however many masks share it."""
        key = repr(source)
        if key not in listed:
            listed[key] = source.index(flow, endpoint)
            # Two sources that differ only in a filtered knob share a label;
            # then the repr is the only thing that tells them apart.
            label = _label(source)
            counts[label if label not in counts else key] = len(listed[key])
        return listed[key]

    indexes = {mask.name: index_of(mask.source) for mask in masks}
    no_hit = [
        mask.name
        for mask in masks
        if not any(case in indexes[mask.name] for case in cases)
    ]
    if no_hit:
        raise ValueError(
            "Not one of these cases has a file for: "
            + ", ".join(no_hit)
            + ". Check those sources -- a mask nobody gets is a mistyped run id"
            " or artifact directory, not an empty layer."
        )

    sessions: List[XopatSession] = []
    coverage = {mask.name: 0 for mask in masks}
    dropped: Dict[str, int] = {}
    for case in cases:
        picked = [mask for mask in masks if case in indexes[mask.name]]
        if len(picked) < min_layers:
            dropped[case] = len(picked)
            continue
        for mask in picked:
            coverage[mask.name] += 1
        sessions.append(
            XopatSession.from_slide(
                every[case],
                [mask.layer(indexes[mask.name][case]) for mask in picked],
                name=case,
                params=params,
                endpoint=endpoint,
            )
        )
    if not sessions:
        raise ValueError(
            f"No case reached {min_layers} of {len(masks)} masks."
            " Check the run ids and the artifact directories."
        )
    return CaseMatrix(
        sessions=sessions, coverage=coverage, dropped=dropped, sources=counts
    )


def sessions_from_masks(
    background: Background,
    masks: Sequence[Mask] = (),
    *,
    only: Sequence[str] = (),
    min_layers: int = 0,
    endpoint: Optional[XopatEndpoint] = None,
    flow: Optional[Mlflow] = None,
    params: Optional[Dict[str, Any]] = None,
) -> List[XopatSession]:
    """One session per case: a background, and every mask that has a file for it.

    See :func:`case_matrix`, which does this and keeps the counts; this is its
    sessions and nothing else.
    """
    return case_matrix(
        background,
        masks,
        only=only,
        min_layers=min_layers,
        endpoint=endpoint,
        flow=flow,
        params=params,
    ).sessions
