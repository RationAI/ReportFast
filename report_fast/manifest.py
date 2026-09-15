"""A report as a file: read a YAML manifest, build the same report Python would.

This is the interpreter between "make me a report of X" and :func:`build_report`.
It reads a spec, turns it into ``Drive``/``MlflowRun``/``Mask`` objects, and calls
the library. It is written once, in here, and tested here -- which is the point:
the alternative, a fresh ~40-line script per report, is the improvisation in
code form, different every run and checkable against nothing.

    reportfast plan reports/dysplasia.yaml     # resolve and report; writes nothing
    reportfast build reports/dysplasia.yaml    # write the HTML, then probe it
    reportfast build reports/dysplasia.yaml --publish

Two rules hold the whole design up:

**Strict about our keys.** An unrecognised manifest key stops the build and
names what it nearly matched. This is the opposite of :meth:`XopatSession.from_config`,
which keeps what it does not know -- and deliberately so. An xOpat field we have
not modelled must survive; a manifest key we have not modelled means somebody's
``min_layer:`` quietly did nothing.

**Vocabulary is open.** ``params:`` on a mask row reaches the v3 layer verbatim,
``from_config:`` pastes a whole session, ``sessions_from:`` calls your own
function. Whatever the viewer can do is reachable from a manifest.

Paths are resolved relative to the manifest, so a manifest is portable and prose
lives in ``.md`` files where a reworded sentence diffs as one.
"""

from __future__ import annotations

import difflib
import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from . import provenance
from .build import build_report
from .components.chart import Chart
from .components.metrics import MetricTable
from .components.prose import Bullets, Heading, LinkList, Prose, RawHtml
from .core import BaseComponent, Report, Section
from .masks import Drive, Mask, MaskSource, MlflowRun, case_matrix
from .mlflow import Mlflow, Published
from .session import XopatSession, as_session
from .verify import Check, probe, session_data_ids
from .xopat import XopatEndpoint

__all__ = [
    "Built",
    "ManifestError",
    "ManifestNoYaml",
    "ManifestNotFound",
    "Plan",
    "Spec",
    "build",
    "plan",
    "publish_target",
    "read",
]

#: Where a report goes when it does not say: beside its manifest, or in
#: `../reports` when the manifest lives in a `manifests/` folder.
DEFAULT_OUT_DIR = "reports"

_TOP = (
    "title",
    "subtitle",
    "intro",
    "out",
    "theme",
    "grid",
    "background",
    "masks",
    "only",
    "min_layers",
    "sessions",
    "sessions_from",
    "endpoint",
    "flow",
    "params",
    "preset",
    "metrics",
    "charts",
    "blocks",
    "publish",
)
_SOURCE = ("drive", "dir", "run", "path", "patterns", "recursive", "python")
#: Everything a mask row carries besides its source keys. `name` is in here for
#: the check (a row arrives here whole) and taken out again for the constructor.
_MASK_DRAWING = ("name", "color", "opacity", "visible", "classes", "palette",
                 "breaks", "mask", "params")
_MASK = _SOURCE + _MASK_DRAWING
_SESSION = ("from_config", "from_url", "from_file")
_BLOCK = ("prose", "heading", "bullets", "links", "metrics", "chart", "raw_html", "section")
_ENDPOINT = ("base_url", "wsi_base_url", "image_protocol", "mount_root")
_FLOW = ("tracking_uri", "web_url", "artifact_prefix")


class ManifestError(ValueError):
    """A manifest that cannot be honoured, said in terms of the manifest."""


class ManifestNotFound(ManifestError, FileNotFoundError):
    """The manifest itself is not there -- nothing was read, so nothing is wrong
    *with* it. Carries both halves on purpose: a library caller catching
    `ManifestError` still gets it, and the CLI reads the `FileNotFoundError`
    half as "nothing to work on" (exit 4) rather than "your YAML is wrong"
    (exit 1), which are two different things to go and fix.
    """


class ManifestNoYaml(ManifestError, ImportError):
    """A manifest was asked for and PyYAML is not installed.

    Its own type because it is the one manifest failure that is an environment
    fact rather than a mistake in the file -- the CLI exits differently for the
    two, and telling someone their `min_layers:` is broken when the fix is
    `uv sync` wastes an afternoon.
    """


# ── reading ─────────────────────────────────────────────────────────────────


@dataclass
class Spec:
    """A manifest, its own location, and the directory its paths mean.

    Attributes:
        spec: The parsed document.
        path: Where it was read from.
        base: What a relative path in it resolves against.
    """

    spec: Dict[str, Any]
    path: Path
    base: Path

    def __getitem__(self, key: str) -> Any:
        return self.spec[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.spec.get(key, default)


def _yaml() -> Any:
    try:
        import yaml
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise ManifestNoYaml(
            "Manifests are YAML and PyYAML is not installed. "
            "`uv sync --extra manifest` (or `uv add pyyaml`)."
        ) from error
    return yaml


def read(source: Union[str, Path, Mapping[str, Any]]) -> Spec:
    """Parse a manifest from a path or take a mapping as given.

    Unknown keys are rejected here, at the one moment they are cheap to talk
    about, rather than being silently ignored for the rest of the build.
    """
    if isinstance(source, Mapping):
        spec = dict(source)
        path, base = Path("<manifest>"), Path.cwd()
    else:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise ManifestNotFound(f"No manifest at {path}")
        loaded = _yaml().safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise ManifestError(f"{path} is not a YAML mapping")
        spec, base = dict(loaded), path.parent
    _check(spec, _TOP, "manifest")
    if not (spec.get("background") or spec.get("sessions") or spec.get("sessions_from")):
        raise ManifestError(
            f"{path}: needs background:, sessions: or sessions_from: -- a report "
            "without cases is not a report."
        )
    if spec.get("background") and (spec.get("sessions") or spec.get("sessions_from")):
        # A grid and a pasted session are two different ways to get cases, and
        # `plan()` can only follow one: the background wins and the pasted rows
        # would never render. Rejecting beats that -- silently dropping rows a
        # person wrote is the exact failure unknown keys are rejected for, and
        # combining them would need a rule about ordering nobody has asked for.
        raise ManifestError(
            f"{path}: background: and sessions: both name cases, and only one can "
            "be the grid. Keep background: for the paired slide-and-mask grid, or "
            "drop it and list the cases under sessions:."
        )
    _check_rows(spec)
    return Spec(spec=spec, path=path, base=base)


def _check_rows(spec: Mapping[str, Any]) -> None:
    """Check the shape of every row the manifest can carry, before resolving it.

    Checking here rather than where each row is consumed is deliberate: reading
    a manifest is instant and listing a run is not, so a manifest with a typo in
    `masks[7]` should fail in front of the reader before it costs five seconds
    and a hundred artifact calls -- and before an agent has been shown a partial
    plan. It is also what lets one rule hold everywhere: a row is either an
    object the caller built, or a mapping drawn from this vocabulary.
    """
    if spec.get("background") is not None:
        _check_source(spec["background"], "background")
    for number, row in enumerate(spec.get("masks") or ()):
        _check_mask(row, number)
    for number, row in enumerate(spec.get("sessions") or ()):
        _check_session(row, number)
    for number, row in enumerate(spec.get("blocks") or ()):
        _check_block(row, number)
    if spec.get("endpoint") is not None:
        _check_endpoint(spec["endpoint"])
    if isinstance(spec.get("flow"), Mapping):
        _check(spec["flow"], _FLOW, "flow")
    if spec.get("publish") is not None and not isinstance(
        spec["publish"], (str, int, Mapping)
    ):
        raise ManifestError(
            "publish: is a run id or {run_id: …} -- it names where a report "
            "belongs and never publishes on its own."
        )


def _check_endpoint(row: Any) -> None:
    from .xopat import XopatEndpoint

    if isinstance(row, XopatEndpoint):
        return
    if not isinstance(row, Mapping):
        raise ManifestError(f"endpoint: expected a mapping of {', '.join(_ENDPOINT)}")
    _check(row, _ENDPOINT, "endpoint")


def _check_source(row: Any, where: str, omit: Sequence[str] = ()) -> None:
    """A source row, keys only: `MaskSource` objects pass, mappings are checked.

    `omit=` is how a mask row gets here -- a layer carries drawing keys beside
    its source, and `_check_mask` has already checked those. What is left to
    check here is the source half, and it stays strict: a mistyped `patern:`
    still stops the build, it just stops it naming the mask row's own vocabulary
    (where `patterns` actually lives) and then what the source half takes.

    The location rule -- exactly one of drive/run/python -- is not duplicated
    here; that belongs with `_source`, which is where a row becomes an object.
    """
    if not isinstance(row, Mapping):
        return  # a MaskSource, a folder path, or a list of paths -- all legal
    try:
        _check(row, (*_SOURCE, *omit), where)
    except ManifestError as error:
        if not omit:
            raise
        # `from error`: the message quotes the error it came from, so chaining it
        # is not decoration -- a reader of the traceback sees both halves.
        raise ManifestError(
            f"{error} -- for the source half, the keys are {', '.join(_SOURCE)}."
        ) from error


def _check_mask(row: Any, number: int) -> None:
    if isinstance(row, Mask):
        return
    named = row.get("name", "unnamed") if isinstance(row, Mapping) else "unnamed"
    where = f"masks[{number}] ({named})"
    if not isinstance(row, Mapping):
        raise ManifestError(f"{where}: expected a mask row or a Mask object")
    _check(row, _MASK, where)
    if "name" not in row:
        raise ManifestError(
            f"masks[{number}]: every layer needs a name -- it is what the "
            "reader switches on in the viewer."
        )
    _check_source(row, where, omit=_MASK_DRAWING)


def _check_session(row: Any, number: int) -> None:
    from .session import XopatSession

    if isinstance(row, XopatSession):
        return
    where = f"sessions[{number}]"
    if not isinstance(row, Mapping):
        raise ManifestError(f"{where}: expected from_config:, from_url: or from_file:")
    _check(row, _SESSION, where)
    if len(row) != 1:
        raise ManifestError(f"{where}: exactly one of {', '.join(_SESSION)}")


def _check_block(row: Any, number: int) -> None:
    if isinstance(row, BaseComponent):
        return
    where = f"blocks[{number}]"
    if not isinstance(row, Mapping) or len(row) != 1:
        raise ManifestError(f"{where}: one block per item, from {', '.join(_BLOCK)}")
    kind, = row
    if kind not in _BLOCK:
        hint = _near(str(kind), _BLOCK)
        raise ManifestError(
            f"{where}: {kind!r} is not a block I know"
            + (f" (did you mean {hint[0]!r}?)" if hint else "")
            + f". Blocks here: {', '.join(_BLOCK)}."
        )


def _check(row: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    """Reject keys this version does not implement, naming the near misses."""
    unknown = [key for key in row if key not in allowed]
    if not unknown:
        return
    hints = [
        f"'{key}'"
        + (f" (did you mean {suggestion[0]!r}?)" if (suggestion := _near(key, allowed)) else "")
        for key in unknown
    ]
    raise ManifestError(
        f"{where}: {', '.join(hints)} is not a key I know. "
        f"Keys here: {', '.join(allowed)}."
    )


def _near(key: str, allowed: Sequence[str]) -> Sequence[str]:
    """The one key this looks most like, or nothing. Sliced rather than taken
    directly so an empty result is falsy and `_check` can branch on it."""
    return difflib.get_close_matches(key, list(allowed), n=1, cutoff=0.7)


def _resolve(value: Any, base: Path) -> Path:
    return Path(value) if Path(str(value)).is_absolute() else base / str(value)


def _text(value: Any, base: Path) -> str:
    """Either the text itself or, when it names a file, that file's contents.

    Prose in a manifest rots the page's diff; prose in a `.md` file beside it
    reads and rewords like prose.
    """
    path = _resolve(value, base)
    if isinstance(value, str) and path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return str(value)


def _paragraphs(value: Any, base: Path) -> List[str]:
    every = value if isinstance(value, (list, tuple)) else [value]
    return [_text(item, base) for item in every]


def _cases(value: Any, base: Path) -> List[str]:
    """`only:` -- the ids, or the file that lists them, one per line."""
    if isinstance(value, str):
        path = _resolve(value, base)
        if not path.is_file():
            raise ManifestError(
                f"only: {value!r} is neither a list of cases nor a file that holds them."
            )
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return [str(item) for item in value or ()]


# ── sources and layers ──────────────────────────────────────────────────────


def _source(row: Any, where: str, omit: Sequence[str] = ()) -> MaskSource:
    """`{drive:}` / `{run:, path:}` / `{python: "pkg.mod:fn"}` / a bare folder.

    `omit=` is how a mask row gets here; see :func:`_check_source`. The keys are
    checked there, once, so this is the row's shape and the row's meaning.
    """
    if isinstance(row, MaskSource):
        return row
    if not isinstance(row, Mapping):
        # A folder, a bare path, or a list of paths -- exactly what `background=`
        # takes in Python, so the same words mean the same thing from YAML and
        # from a script, including "a list keeps the order I typed it in".
        from .masks import _source_of

        try:
            return _source_of(row)
        except (TypeError, ValueError) as error:
            raise ManifestError(f"{where}: {error}") from error
    if not isinstance(row, Mapping):
        raise ManifestError(f"{where}: expected a folder or a run, got {type(row).__name__}")
    _check_source(row, where, omit=omit)
    keys = [key for key in ("drive", "dir", "run", "python") if key in row]
    if len(keys) != 1:
        raise ManifestError(
            f"{where}: say exactly one of drive:, run:, python:"
            + (f" (found {', '.join(keys)})" if keys else "")
        )
    kind = keys[0]
    if kind == "python":
        return _callable_source(row[kind], where)

    # Only the knobs the manifest actually names are passed on. Filling in
    # `patterns=None` / `recursive=True` here would override what each source
    # class decides for itself, and the two decide differently on purpose:
    # `Drive` recurses because a mask folder is usually organised by case, while
    # `MlflowRun` does not, because `path: tile_masks/blur` names one directory
    # and pulling in its siblings would silently add layers. A None pattern list
    # is worse still: it means "every file", so `metrics.csv` becomes a case.
    knobs: Dict[str, Any] = {}
    if row.get("patterns") is not None:
        knobs["patterns"] = tuple(row["patterns"])
    if "recursive" in row:
        knobs["recursive"] = bool(row["recursive"])

    if kind == "run":
        return MlflowRun(str(row[kind]), str(row.get("path", "")), **knobs)
    return Drive(row[kind], **knobs)


def _callable_source(reference: str, where: str) -> Any:
    """Call ``"pkg.module:function"`` -- the door for a source of your own."""
    target = _import(reference, where)
    made = target() if callable(target) else target
    if not isinstance(made, MaskSource):
        raise ManifestError(
            f"{where}: {reference} returned {type(made).__name__}, not a MaskSource"
        )
    return made


def _import(reference: str, where: str):
    if ":" not in reference:
        raise ManifestError(f"{where}: {reference!r} is not 'pkg.module:function'")
    module_name, _, attribute = reference.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise ManifestError(
            f"{where}: cannot import {module_name!r} ({error}). Is it on the path "
            "of the project the report belongs to?"
        ) from error
    try:
        return getattr(module, attribute)
    except AttributeError as error:
        raise ManifestError(f"{where}: {module_name} has no {attribute!r}") from error


def _mask(row: Any, number: int) -> Mask:
    """One row of `masks:` as a :class:`Mask`.

    A `Mask` the caller built passes through untouched -- that is how the same
    builder serves a YAML row and a Python list, and why a report can start as a
    manifest and end as code without either path re-learning the other's rules.
    """
    if isinstance(row, Mask):
        return row
    _check_mask(row, number)  # keys and the name, so the errors read as row errors
    where = f"masks[{number}] ({row.get('name', 'unnamed')})"
    known = {key: row[key] for key in _MASK_DRAWING if key in row and key != "name"}
    return Mask(
        name=str(row["name"]), source=_source(row, where, omit=_MASK_DRAWING), **known
    )


# ── the plan ────────────────────────────────────────────────────────────────


@dataclass
class Plan:
    """What a manifest resolves to, before a byte of HTML exists.

    The review artifact: an agent runs `plan`, reads this, and only then builds.
    Text and JSON forms carry the same numbers, so a CI job can assert on them.
    """

    title: str
    subtitle: str
    out: Path
    cases: List[str]
    layers: Dict[str, int]
    coverage: Dict[str, int] = field(default_factory=dict)
    dropped: Dict[str, int] = field(default_factory=dict)
    sources: Dict[str, int] = field(default_factory=dict)
    publish: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    sessions: List[XopatSession] = field(default_factory=list, repr=False)

    @property
    def layer_count(self) -> int:
        return max(self.layers.values(), default=0)

    def to_text(self) -> str:
        lines = [
            f"{self.title}: {len(self.cases)} cases, "
            f"up to {self.layer_count} overlays"
        ]
        if self.sources:
            lines.append("sources")
            lines += [f"  {label}  {count} files" for label, count in self.sources.items()]
        if self.coverage:
            lines.append("coverage  (cases that got a file from each layer)")
            lines += [
                f"  {name}: {hit}/{len(self.cases)}" for name, hit in self.coverage.items()
            ]
        if self.dropped:
            thin = ", ".join(f"{case} ({count})" for case, count in self.dropped.items())
            lines.append(f"dropped by min_layers: {thin}")
        if self.warnings:
            lines.append("warnings")
            lines += [f"  {warning}" for warning in self.warnings]
        lines.append(f"out      {self.out}")
        lines.append(
            f"publish  {self.publish or 'not set'}"
            + (" -- only with --publish" if self.publish else "")
        )
        return "\n".join(lines)

    def to_json(self) -> str:
        """Machine-readable plan, keys in a fixed order."""
        return json.dumps(
            {
                "title": self.title,
                "out": str(self.out),
                "publish": self.publish,
                "cases": self.cases,
                "layers": self.layers,
                "coverage": self.coverage,
                "dropped": self.dropped,
                "sources": self.sources,
                "warnings": self.warnings,
            },
            indent=2,
            sort_keys=True,
        )


def _endpoint(spec: Spec) -> Optional[XopatEndpoint]:
    row = spec.get("endpoint")
    if row is None:
        return None
    if isinstance(row, XopatEndpoint):
        return row
    _check(row, _ENDPOINT, "endpoint")
    return XopatEndpoint(**dict(row))


def _flow(spec: Spec) -> Mlflow:
    row = spec.get("flow")
    if isinstance(row, Mlflow):
        return row
    _check(row or {}, _FLOW, "flow")
    return Mlflow.from_env(**dict(row or {}))


def _out(spec: Spec) -> Path:
    if spec.get("out"):
        return _resolve(spec["out"], spec.base)
    stem, directory = spec.path.stem, spec.path.parent
    if directory.name == "manifests":
        return directory.parent / DEFAULT_OUT_DIR / f"{stem}.html"
    return directory / f"{stem}.html"


def plan(
    source: Union[str, Path, Mapping[str, Any], Spec],
    *,
    flow: Optional[Mlflow] = None,
    endpoint: Optional[XopatEndpoint] = None,
) -> Plan:
    """Resolve a manifest: read the sources, pair masks with cases, count.

    Nothing is written and no slide is opened; a run's artifacts are listed, not
    fetched. Expect it to be the slow step only in proportion to how many runs
    the masks come from.
    """
    spec = source if isinstance(source, Spec) else read(source)
    target = endpoint or _endpoint(spec)
    warnings: List[str] = []
    sessions: List[XopatSession] = []
    coverage: Dict[str, int] = {}
    dropped: Dict[str, int] = {}
    sources: Dict[str, int] = {}

    if spec.get("background") is not None:
        masks = [_mask(row, number) for number, row in enumerate(spec.get("masks") or ())]
        wanted = _cases(spec["only"], spec.base) if spec.get("only") else ()
        # `case_matrix` speaks in ValueError and FileNotFoundError; a manifest
        # consumer -- the CLI, an agent reading a traceback -- needs the failure
        # in manifest terms, named after the file that caused it. The messages
        # are already good, so this only translates the type and says where.
        try:
            matrix = case_matrix(
                _source(spec["background"], "background"),
                masks,
                only=wanted,
                min_layers=int(spec.get("min_layers") or 0),
                endpoint=target,
                flow=flow or (_flow(spec) if _needs_run(spec) else None),
                params=dict(spec.get("params") or {}),
            )
        except ManifestError:
            raise
        except (FileNotFoundError, ValueError) as error:
            raise ManifestError(f"{spec.path}: {error}") from error
        sessions, coverage, dropped, sources = (
            matrix.sessions,
            matrix.coverage,
            matrix.dropped,
            matrix.sources,
        )
        short = [name for name, hit in coverage.items() if hit < len(matrix.sessions)]
        if short:
            warnings.append(
                "layers not on every case: "
                + ", ".join(f"{name} {coverage[name]}/{len(sessions)}" for name in short)
            )
    else:
        sessions = _pasted(spec, target)
        if spec.get("masks"):
            raise ManifestError(
                "masks: belongs with background= -- sessions: carries its own layers."
            )

    if not sessions:  # pragma: no cover - case_matrix raises first
        raise ManifestError(f"{spec.path}: resolves to no cases.")

    return Plan(
        title=str(spec.get("title") or "Report"),
        subtitle=str(spec.get("subtitle") or ""),
        out=_out(spec),
        cases=[str(session.background[0].get("name") or "") for session in sessions],
        layers={
            str(session.background[0].get("name") or ""): len(
                session.visualizations[0]["shaders"]
            )
            for session in sessions
        },
        coverage=coverage,
        dropped=dropped,
        sources=sources,
        publish=_publish_run(spec),
        warnings=warnings,
        sessions=sessions,
    )


def _needs_run(spec: Spec) -> bool:
    if isinstance(spec.get("background"), Mapping) and "run" in spec["background"]:
        return True
    return any("run" in row for row in spec.get("masks") or () if isinstance(row, Mapping))


def publish_target(source: Union[str, Path, Mapping[str, Any], Spec]) -> Optional[str]:
    """The run id a manifest wants published to, or ``None``.

    Public because the CLI repeats it back before an upload, and reading a
    private function out of a library from a command line is how the two get out
    of step. Note what this does *not* do: it only reports where a report
    belongs. Asking for it is not the same as publishing, and nothing here
    publishes.
    """
    spec = source if isinstance(source, Spec) else read(source)
    return _publish_run(spec)


def _publish_run(spec: Spec) -> Optional[str]:
    row = spec.get("publish")
    if row is None:
        return None
    if isinstance(row, Mapping):
        return str(row.get("run_id") or "") or None
    return str(row)


def _pasted(spec: Spec, endpoint: Optional[XopatEndpoint]) -> List[XopatSession]:
    """`sessions:` -- documents the library passes through untouched, and
    `sessions_from:` -- a function of your own that returns them."""
    made: List[XopatSession] = []
    for number, row in enumerate(spec.get("sessions") or ()):
        if isinstance(row, XopatSession):
            made.append(row)
            continue
        # Re-checked here because `plan()` accepts a mapping directly: read() is
        # the usual door, but a caller who hands over a dict in a script should
        # get the same message as one who points at a file.
        _check_session(row, number)
        (kind, value), = row.items()
        session = _paste(kind, _resolve(value, spec.base) if kind != "from_url" else value)
        made.append(as_session(session, endpoint=endpoint))
    if spec.get("sessions_from"):
        made.extend(_sessions_from(spec["sessions_from"], spec))
    return made


def _paste(kind: str, value: Any) -> XopatSession:
    document = (
        Path(value).read_text(encoding="utf-8")
        if kind in ("from_config", "from_file") and Path(str(value)).is_file()
        else value
    )
    if kind == "from_url":
        return XopatSession.from_url(str(document))
    if isinstance(document, Path):
        document = document.read_text(encoding="utf-8")
    return XopatSession.from_config(document)


def _sessions_from(reference: str, spec: Spec) -> List[XopatSession]:
    made = _import(reference, "sessions_from")()
    every = [made] if isinstance(made, XopatSession) else list(made or ())
    if not all(isinstance(session, XopatSession) for session in every):
        raise ManifestError(
            f"sessions_from: {reference} returned something that is not sessions"
        )
    return every


# ── building ────────────────────────────────────────────────────────────────


@dataclass
class Built:
    """What a build produced and what was checked about it."""

    report: Report
    plan: Plan
    out: Optional[Path] = None
    checks: List[Check] = field(default_factory=list)
    published: Optional[Published] = None
    #: Where the provenance sidecar went, next to `out`. None when no page was
    #: written -- the record describes a file, so it follows the file's rule.
    provenance: Optional[Path] = None
    #: The record itself, written or not. A publish logs it without the file, and
    #: a caller asserting on a build in CI reads this rather than re-opening it.
    record: Optional[Any] = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        """Whether every DataID the report links to answers."""
        return all(check.ok for check in self.checks if check.checked)


def build(
    source: Union[str, Path, Mapping[str, Any], Spec],
    *,
    out: Union[str, Path, bool, None] = None,
    check: bool = False,
    publish: bool = False,
    run_id: Optional[str] = None,
    flow: Optional[Mlflow] = None,
    endpoint: Optional[XopatEndpoint] = None,
    with_provenance: bool = True,
) -> Built:
    """Build the report a manifest describes.

    Args:
        source: Manifest path, parsed spec, or a mapping.
        out: Where to write. ``None`` takes the manifest's `out:`; ``False``
            builds without writing anything.
        check: Probe every DataID against the image server afterwards.
        publish: Upload it. Never implied by the manifest's `publish:` key, which
            only records where a report belongs.
        run_id: Overrides the manifest's run for this one publish.
        flow: MLflow client; built from `flow:` in the manifest otherwise.
        endpoint: Deployment coordinates, overriding `endpoint:`.
        with_provenance: Write `report.provenance.json` beside the HTML. On by
            default: a manifest is a declaration of intent and the sidecar is the
            record of what resolved, and the pair is what makes the run answerable.
            `False` for a build into a directory that is about to be thrown away.

    Returns:
        A :class:`Built` with the report, the plan behind it, the probe results
        and where it was published.
    """
    spec = source if isinstance(source, Spec) else read(source)
    resolved = plan(spec, flow=flow, endpoint=endpoint)
    target = endpoint or _endpoint(spec)

    # Note what is deliberately NOT here: a refusal of `out=False` together with
    # `publish=True`. Uploading a report that was never written locally is a real
    # want -- CI builds in a temp dir and keeps only the run's copy -- and a caller
    # who passes both has asked for each explicitly, so nothing is implied. The
    # CLI rejects that pair (`--check-only --publish`) because there the flags can
    # arrive from a shell history, and `--check-only`'s whole promise is that
    # nothing changes.
    report = build_report(
        slides=resolved.sessions,
        title=resolved.title,
        subtitle=resolved.subtitle,
        intro=_paragraphs(spec["intro"], spec.base) if spec.get("intro") else "",
        blocks=_blocks(spec),
        metrics=_metrics(spec, flow),
        charts=_charts(spec),
        grid=spec.get("grid", None) if spec.get("grid") is not False else False,
        theme=str(spec.get("theme") or "auto"),
        endpoint=target,
        out=None,
    )

    built = Built(report=report, plan=resolved)
    # `out=False` means build without writing. Checked before the falsy-coalesce
    # because False is falsy and would otherwise fall through to the manifest's
    # own out: and write anyway.
    if out is not False:
        destination = Path(out) if out else resolved.out
        if destination is not False:
            built.out = report.write(destination)

    if check:
        built.checks = probe(session_data_ids(resolved.sessions), target)
    # Recorded from `resolved` -- the plan after drives, masks and min_layers have
    # had their say -- and never from `spec.spec`, which is what someone wrote
    # rather than what ran. That disagreement is the failure this file exists to
    # make impossible.
    record = provenance.for_manifest(
        spec, resolved, endpoint=target, design=None
    )
    # A publish with no record is refused here rather than in `_publish`, so the
    # message arrives before a byte is staged: `with_provenance=False` says "this
    # build is disposable", and a disposable build that uploads is not disposable.
    if publish and not with_provenance:
        raise ManifestError(
            "publish=True with with_provenance=False: there would be no record of "
            "what the run received. Either keep the record or do not upload."
        )
    built.provenance = provenance.write_for(built.out, record) if with_provenance else None
    built.record = record
    if publish:
        built.published = _publish(spec, built, run_id, flow)
    return built


def _blocks(spec: Spec) -> List[BaseComponent]:
    made: List[BaseComponent] = []
    for number, row in enumerate(spec.get("blocks") or ()):
        made.append(_block(row, number, spec))
    return made


def _block(row: Any, number: int, spec: Spec) -> BaseComponent:
    where = f"blocks[{number}]"
    if isinstance(row, BaseComponent):
        return row
    if not isinstance(row, Mapping) or len(row) != 1:
        raise ManifestError(f"{where}: one block per item, from {', '.join(_BLOCK)}")
    (kind, value), = row.items()
    base = spec.base
    if kind == "prose":
        return Prose(paragraphs=_paragraphs(value, base))
    if kind == "heading":
        text, _, level = str(value).rpartition("|")
        return Heading(str(value).strip(), level=int(level) or 2) if text else Heading(str(value))
    if kind == "bullets":
        return Bullets([str(item) for item in value])
    if kind == "links":
        return LinkList(_links(value))
    if kind == "metrics":
        return MetricTable(_metrics_row(value, spec))
    if kind == "chart":
        caption = ""
        if isinstance(value, Mapping):
            caption = str(value.get("caption", ""))
            value = value.get("chart") or value.get("image")
        image = _resolve(value, base)
        return Chart(image, caption=caption, alt=caption or str(value))
    if kind == "raw_html":
        return RawHtml(_text(value, base))
    if kind == "section":
        rows = value if isinstance(value, Mapping) else {}
        title = str(rows.get("title", "Section"))
        inner = [_block(item, number, spec) for number, item in enumerate(rows.get("blocks") or ())]
        return Section(title, inner)
    raise ManifestError(f"{where}: unknown block {kind!r}")  # pragma: no cover


def _links(value: Any) -> List[tuple]:
    if isinstance(value, Mapping):
        return list(value.items())
    made = []
    for row in value or ():
        if isinstance(row, Mapping):
            made.extend((key, item) for key, item in row.items())
        else:
            raise ManifestError("links: wants {label: url} pairs")
    return made


def _metrics(spec: Spec, flow: Optional[Mlflow]) -> Any:
    if spec.get("metrics") is None:
        return None
    return _metrics_row(spec["metrics"], spec, flow)


def _metrics_row(value: Any, spec: Spec, flow: Optional[Mlflow] = None) -> Any:
    """A literal table, or `{from_run: <id>}` for one read off a run."""
    if isinstance(value, Mapping) and set(value) == {"from_run"}:
        client = flow or _flow(spec)
        return client.metrics(str(value["from_run"]))
    if isinstance(value, Mapping) and "from_run" in value:
        raise ManifestError("metrics: from_run takes a run id and nothing else")
    return value


def _charts(spec: Spec) -> Any:
    rows = spec.get("charts")
    if rows is None:
        return None
    if isinstance(rows, Mapping):
        return {_resolve(key, spec.base): _resolve(value, spec.base) for key, value in rows.items()}
    return [_resolve(item, spec.base) for item in rows]


def _publish(spec: Spec, built: Built, run_id: Optional[str], flow: Optional[Mlflow]):
    if built.record is None:  # pragma: no cover - build() always fills it in
        raise ManifestError(
            "This build carries no provenance record (with_provenance=False), and a "
            "publish without one would log a report nothing describes. Build it with "
            "the record and publish that."
        )
    target = run_id or _publish_run(spec)
    if not target:
        raise ManifestError(
            "Nothing to publish to: the manifest has no publish: and --publish "
            "got no run id."
        )
    client = flow or _flow(spec)
    # Three files, three different questions, all decided in one place
    # (`provenance.logged_dir`, which the manifest-less door uses too): the
    # manifest is what someone *meant*, `plan.json` is what the resolution decided,
    # and the sidecar names the endpoint, the viewer stamp and the DataIDs the links
    # carry. `conf/` is the slot the original tool logged its Hydra config under.
    record = built.record
    with provenance.logged_dir(
        record,
        manifest_text=_yaml().safe_dump(spec.spec, sort_keys=False, allow_unicode=True),
        plan_json=built.plan.to_json(),
    ) as staging:
        return client.publish(built.report, run_id=target, extra_dir=staging)


def _unused(_: Iterable[Any]) -> None:  # pragma: no cover - keeps imports honest
    return None
