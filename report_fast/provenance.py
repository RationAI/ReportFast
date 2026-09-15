"""What a report was made of, written beside it — never into it.

Decision 8, ruled in review and worth restating because it is the constraint that
shapes everything here: **the page stays untouched**. No footer block, no comment
in the head, no hidden div. A report looks identical whether or not anyone is
keeping records, and the record lives in `report.provenance.json` next to the HTML.

The accepted cost, stated so nobody rediscovers it as a bug: **a mailed HTML
carries no provenance**. The sidecar does not travel with the file. If a report
has to be self-explaining wherever it goes, the answer is to deliver the pair, or
to want a manifest — not to stamp the page.

Why the sidecar matters more since prompt mode, not less: with no manifest kept,
"same input" is only answerable from this file. Reproducibility is asserted over
*(sessions + composition) → HTML*, and after the run there is no manifest left to
point at — the sessions may never have been saved at all. So the sidecar records
the **session design as authored** (one document, not 300 instantiations), the
endpoint, the viewer stamp the schema was derived from, the tool version, and what
the build was actually given.

Two properties that are the whole point and are therefore tested:

* **Generated from resolved data.** It records the endpoint the links were built
  against and the sessions the page links, not what a spec declared. The old
  tool's published report and its logged config disagreed because one was resolved
  and one was declared; a record written after resolution cannot do that.
* **Reproducible.** No clock, no absolute paths chosen by the tool, fixed key
  order. Same build → byte-identical sidecar, like the HTML. A provenance file
  that differs between two runs is not evidence, it is noise.

What it can never contain is the agent's reasoning — the CLI never saw it. That
stays in the transcript, which is why "keep the spec?" remains a question worth
asking once.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from .contract import viewer_stamp
from .session import XopatSession
from .verify import session_data_ids
from .xopat import XopatEndpoint, resolve_endpoint

#: The sidecar's name, given an HTML path: `report.html` → `report.provenance.json`.
SUFFIX = ".provenance.json"

#: Every key this version writes, in the order it writes them. `json.dumps` keeps
#: insertion order, so this is also the file's layout.
KEYS = (
    "kind",
    "tool",
    "viewer",
    "endpoint",
    "report",
    "design",
    "inputs",
    "sources",
    "data_ids",
)


def tool_version() -> str:
    """The installed version of this package, as the metadata says it.

    Read rather than hardcoded: a version string copied into a module is a second
    version of the truth, and the one in `pyproject.toml` is the one that ships.
    """
    try:
        return metadata.version("report-fast")
    except metadata.PackageNotFoundError:  # a source tree with nothing installed
        return "0+unknown"


def sidecar_path(html: Union[str, Path]) -> Path:
    """Where the sidecar for this HTML file goes.

    Suffix rather than a sibling directory: the pair has to be obvious when someone
    finds one of the two files three months later, and `<name>.html` and
    `<name>.provenance.json` sort together in every listing.
    """
    path = Path(html)
    return path.with_name(f"{path.stem}{SUFFIX}")


# ---------------------------------------------------------------------- record


@dataclass
class Provenance:
    """One report's record, and the only thing that knows the file's key order.

    Attributes:
        report: Title and the cases the page links, in page order.
        endpoint: The deployment the links were built against — resolved, so what
            the environment said counts as what was used.
        design: The session design as authored, when the build came through a
            template. One document, so a rebuild needs that and the case list
            rather than the 300 files a loop produced.
        inputs: What the build was given, in the vocabulary the CLI used:
            a sessions folder, session files, a manifest path.
        sources: Filesystem roots the inputs came from, deduplicated.
        data_ids: Every DataID the page links, in first-seen order.
        viewer: The contract's stamp: viewer version, commit, source digests.
    """

    report: Dict[str, Any] = field(default_factory=dict)
    endpoint: Dict[str, Any] = field(default_factory=dict)
    design: Optional[Dict[str, Any]] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)
    data_ids: List[str] = field(default_factory=list)
    viewer: Dict[str, Any] = field(default_factory=viewer_stamp)
    #: `manifest` or `composition` — which door the build came through, stated by
    #: the caller rather than inferred. Inferred from `inputs.manifest` it is wrong
    #: twice over: a manifest read from a Python dict has no filename but is still
    #: a manifest, and a composition whose caller mentioned a manifest for context
    #: is not one.
    kind: str = "composition"

    def __post_init__(self) -> None:
        if self.kind not in ("manifest", "composition"):
            raise ProvenanceError(
                f"kind is {self.kind!r}; the record says which door built the page, "
                "so it is either 'manifest' or 'composition'."
            )

    def to_dict(self) -> Dict[str, Any]:
        """The record, keys in the fixed order :data:`KEYS` says.

        A dict built in literal order rather than sorted afterwards: the file is
        meant to be read by a person as well as diffed, and `viewer` above
        `report` above `design` is the order the questions come in.
        """
        return {
            "kind": self.kind,
            "tool": {"name": "report-fast", "version": tool_version()},
            "viewer": dict(self.viewer),
            "endpoint": dict(self.endpoint),
            "report": dict(self.report),
            "design": None if self.design is None else _plain(self.design),
            "inputs": _plain(self.inputs),
            "sources": list(self.sources),
            "data_ids": list(self.data_ids),
        }

    def to_json(self) -> str:
        """The file's bytes as text: two-space indent, no clock, fixed order.

        `ensure_ascii=False` so a case named `科` stays legible instead of turning
        into escapes; the file is written as UTF-8 either way.
        """
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    def digest(self) -> str:
        """sha256 of the record — what a publish can log without the file."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def write(self, html: Union[str, Path]) -> Path:
        """Write the sidecar for this HTML path and return where it went.

        Refuses to invent a directory nobody named, and refuses to write beside a
        path that is not HTML: `report.provenance.json` next to `report.pdf` would
        read as that PDF's provenance, which it is not.
        """
        target = sidecar_path(html)
        if Path(html).suffix.lower() != ".html":
            raise ProvenanceError(
                f"{html} is not an .html file; the sidecar is named after the "
                "report it describes, so point at the report."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        return target

    def read_back(self, html: Union[str, Path]) -> Dict[str, Any]:
        """The written record, re-read. For a publish that wants to log what it
        wrote rather than a second copy of it."""
        return json.loads(self.write(html).read_text(encoding="utf-8"))


class ProvenanceError(ValueError):
    """A record that cannot be honestly written, said in terms of the build."""


# ------------------------------------------------------------------- collecting


def for_report(
    *,
    title: str,
    cases: Sequence[str],
    sessions: Iterable[XopatSession],
    endpoint: Optional[XopatEndpoint] = None,
    design: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    sources: Optional[Iterable[Union[str, Path]]] = None,
    kind: str = "composition",
) -> Provenance:
    """Collect the record for a page that has been built.

    Every argument is something the caller already resolved rather than something
    this function re-derives — the endpoint the links used, the sessions the grid
    holds. That is the point of the shape: a record assembled from live objects
    cannot disagree with the page, where a record assembled from a spec can.

    Args:
        title: Page title, as the page has it.
        cases: Case names in page order.
        sessions: The sessions the page links (same objects, not copies).
        endpoint: Deployment the links were built against.
        design: The authored session design, when a template was used.
        inputs: What the build was given — see :func:`for_cli`.
        sources: Filesystem roots the inputs came from.

    Raises:
        ProvenanceError: a session is not a session. A record whose `data_ids`
            came from something else would silently be a record of nothing.
    """
    every = list(sessions)
    for number, session in enumerate(every):
        if not isinstance(session, XopatSession):
            raise ProvenanceError(
                f"session {number} is {type(session).__name__}, expected a "
                "XopatSession — the record has to name the DataIDs the page links."
            )
    target = resolve_endpoint(endpoint)
    return Provenance(
        report={"title": title, "cases": list(cases), "sessions": len(every)},
        endpoint=_endpoint_row(target),
        design=None if design is None else dict(design),
        inputs=dict(inputs or {}),
        sources=sorted({str(Path(source)) for source in (sources or ())}),
        data_ids=session_data_ids(every),
        kind=kind,
    )


def for_page(
    report: Any,
    sessions: Iterable[XopatSession],
    *,
    endpoint: Optional[XopatEndpoint] = None,
    design: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    sources: Optional[Iterable[Union[str, Path]]] = None,
) -> Provenance:
    """The same record, from a built :class:`~report_fast.core.Report`.

    The door both build paths come through: a `Report` already holds its title and
    its blocks, and the sessions are read out of the page rather than passed
    alongside it. Two callers describing the same page then cannot file two
    different records.

    Args:
        report: The built page.
        sessions: Kept for a caller holding a `Report` whose blocks were wrapped
            (a raw FastHTML tree carries no session); the page's own blocks win,
            and this list is the fallback.
        endpoint, design, inputs, sources: As :func:`for_report`.
    """
    from .compose import case_labels, walk_sessions  # only compose knows the grid shape

    blocks = list(getattr(report, "blocks", ()))
    found = walk_sessions(blocks)
    if not found:
        found = [item for item in sessions if isinstance(item, XopatSession)]
    return for_report(
        title=str(getattr(report, "title", "")),
        cases=case_labels(found),
        sessions=found,
        endpoint=endpoint,
        design=design,
        inputs=inputs,
        sources=sources,
    )


def for_cli(
    *,
    title: str,
    cases: Sequence[str],
    sessions: Iterable[XopatSession],
    endpoint: Optional[XopatEndpoint] = None,
    design: Optional[Mapping[str, Any]] = None,
    sessions_dir: Optional[Union[str, Path]] = None,
    session_files: Sequence[Union[str, Path]] = (),
    manifest: Optional[Union[str, Path]] = None,
    layout: Optional[str] = None,
    grid: bool = True,
) -> Provenance:
    """The record for a build the command line ran: what it was *given*.

    `inputs` is deliberately phrased as the flags. A person holding
    `report.provenance.json` and no shell history is reconstructing a command, and
    `--sessions-dir sessions/ --layout rows` is the shape that transfers; a
    normalised internal description of the composition is not.

    Relative paths stay relative where they were given relative. An absolute path
    in a record is a claim about one machine — and a build of the same report on
    the workstation next to it then differs in the file that exists to be compared.
    """
    given: Dict[str, Any] = {}
    if manifest is not None:
        given["manifest"] = str(manifest)
    if sessions_dir is not None:
        given["sessions_dir"] = str(sessions_dir)
    if session_files:
        given["sessions"] = [str(item) for item in session_files]
    if layout:
        given["layout"] = layout
    if grid is False:
        given["grid"] = False

    roots: List[Union[str, Path]] = []
    if sessions_dir is not None:
        roots.append(Path(sessions_dir))
    for item in session_files:
        # A JSON string passed to --session is not a folder and has no root; only
        # things that look like paths contribute one.
        parent = Path(item).parent if _looks_like_path(item) else None
        if parent is not None and str(parent) not in roots:
            roots.append(parent)
    if manifest is not None:
        roots.append(Path(manifest).parent)

    return for_report(
        title=title,
        cases=cases,
        sessions=sessions,
        endpoint=endpoint,
        design=design,
        inputs=given,
        sources=roots,
    )


def for_manifest(
    spec: Any,
    resolved: Any,
    *,
    endpoint: Optional[XopatEndpoint] = None,
    design: Optional[Mapping[str, Any]] = None,
) -> Provenance:
    """The record for a manifest build, written from the *resolved* plan.

    `spec` contributes one thing: its path, so the record points back at the file
    someone can re-run. Everything else comes from `resolved` — the plan after
    drives were walked, masks matched and `min_layers:` applied. A record built
    from the declared spec is the old tool's bug restated: the published report and
    the logged config disagreeing because one was resolved and one was declared.

    Args:
        spec: The :class:`~report_fast.manifest.Spec` that was built.
        resolved: The :class:`~report_fast.manifest.Plan` it resolved to.
        endpoint: The deployment the links were built against.
        design: The authored design, when the manifest's sessions came from a
            template. Rare — manifests declare sources — so it is passed in rather
            than looked for.
    """
    from .manifest import Spec  # only to name the shape; no behaviour is used

    path = spec.path if isinstance(spec, Spec) else None
    declared = spec.spec if isinstance(spec, Spec) else {}
    given: Dict[str, Any] = {}
    # `manifest.read` gives a mapping it was handed the placeholder path
    # `<manifest>`. Recording that as a path would send a reader to a file that
    # does not exist, so an in-memory spec says so instead.
    named = path is not None and not str(path).startswith("<")
    if named:
        given["manifest"] = str(path)
    else:
        given["manifest"] = None
    for key in ("background", "masks", "sessions", "sessions_from"):
        if declared.get(key) is not None:
            # Named, not copied: the manifest already holds the definition, and a
            # second copy here is a second thing that can go stale.
            given[key] = f"declared in {path.name}" if named else key

    return for_report(
        title=resolved.title,
        cases=list(resolved.cases),
        sessions=resolved.sessions,
        endpoint=endpoint,
        design=design,
        inputs=given,
        sources=_manifest_roots(declared, path if named else None),
        kind="manifest",
    )


def _manifest_roots(declared: Mapping[str, Any], path: Optional[Path]) -> List[str]:
    """Filesystem roots a manifest reads from, where it named one plainly.

    Only `drive:` strings, and only the ones that are literal paths. A
    `sessions_from:` function reads wherever it likes and this cannot know, so it
    contributes nothing — which is the honest answer, and better than a guess that
    looks like a source.
    """
    found: List[str] = []

    def collect(row: Any) -> None:
        if isinstance(row, Mapping):
            drive = row.get("drive")
            if isinstance(drive, (str, Path)):
                found.append(str(Path(drive)))
            for item in row.values():
                collect(item)
        elif isinstance(row, (list, tuple)):
            for item in row:
                collect(item)

    collect(declared.get("background"))
    collect(declared.get("masks"))
    if path is not None:  # the caller already dropped a placeholder path
        found.append(str(Path(path).parent))
    return found


def write_for(
    html: Union[str, Path],
    record: Provenance,
) -> Optional[Path]:
    """Write `record` for `html`, or nowhere at all if nothing was written.

    The one place that decides whether a sidecar appears, so that both doors and
    any future one get the same answer: `html` is ``None`` when a build wrote no
    file (`--check-only`), and a provenance record beside a report that does not
    exist is a record of a report nobody can open.
    """
    if html is None:
        return None
    return record.write(html)


def design_of(template: Any) -> Optional[Dict[str, Any]]:
    """The design a :class:`~report_fast.session.SessionTemplate` was made from.

    Returned as authored — the template holds the session the gate accepted, and
    `to_config()` on it is that document. `slots` is included because it is part of
    what has to be reproduced: the same design bound with `{0: "slide"}` and with
    `{0: "slide", 1: "mask"}` is a different report from the same JSON.
    """
    if template is None:
        return None
    session = getattr(template, "session", None)
    if not isinstance(session, XopatSession):
        raise ProvenanceError(
            f"{type(template).__name__} carries no session to record; pass the "
            "design as a mapping if it did not come from a template."
        )
    return {
        "session": session.to_config(),
        "slots": {str(index): name for index, name in sorted(_slots_of(template).items())},
    }


# ------------------------------------------------------------------- publishing


def logged_dir(
    record: Provenance,
    *,
    manifest_text: str = "",
    plan_json: str = "",
) -> Any:
    """A temp directory holding what a publish logs, removed on the way out.

    `manifest.yaml` and `plan.json` when there is a manifest; `provenance.json`
    always. That is decision 8's "a publish logs the sidecar beside the report when
    there is no manifest to log" — and *when there is*, it logs the sidecar too,
    because the manifest says what was meant and only the record says what resolved.

    Returns a context manager whose value is the directory path.
    """
    return _Logged(record, manifest_text, plan_json)


class _Logged:
    """The context manager behind :func:`logged_dir`, spelled out for its cleanup."""

    def __init__(self, record: Provenance, manifest_text: str, plan_json: str) -> None:
        self.record = record
        self.manifest_text = manifest_text
        self.plan_json = plan_json
        self._tmp: Any = None

    def __enter__(self) -> Path:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="reportfast-prov-")
        directory = Path(self._tmp.name)
        if self.manifest_text:
            (directory / "manifest.yaml").write_text(
                self.manifest_text, encoding="utf-8"
            )
        if self.plan_json:
            (directory / "plan.json").write_text(self.plan_json, encoding="utf-8")
        # The resolved record, under the name the pair has on disk, so a run's
        # artifacts and the folder a report was mailed from list the same thing.
        (directory / "provenance.json").write_text(
            self.record.to_json(), encoding="utf-8"
        )
        return directory

    def __exit__(self, *exc: Any) -> bool:
        if self._tmp is not None:
            self._tmp.cleanup()
        return False


# ---------------------------------------------------------------------- helpers


def _endpoint_row(endpoint: XopatEndpoint) -> Dict[str, Any]:
    """The deployment as four scalar fields, not a repr.

    A record is for reading and for diffing; `XopatEndpoint(...)` in a JSON string
    is neither, and would put the whole dataclass into the file at its own key
    order.
    """
    return {
        "base_url": endpoint.base_url,
        "wsi_base_url": endpoint.wsi_base_url,
        "image_protocol": endpoint.image_protocol,
        "mount_root": endpoint.mount_root,
    }


def _slots_of(template: Any) -> Mapping[int, str]:
    slots = getattr(template, "slots", None)
    return dict(slots) if isinstance(slots, Mapping) else {}


def _looks_like_path(value: Union[str, Path]) -> bool:
    if isinstance(value, Path):
        return True
    text = str(value).strip()
    return text.startswith(("/", ".")) or text.endswith(".json")


def _plain(value: Any) -> Any:
    """JSON-safe copy of a design or an input mapping.

    Local on purpose rather than the one in `compose`: this module must not import
    compose at module scope (compose owns the grid shape this module reads), and a
    session design is a nested document that needs the recursion. `Path` becomes a
    string because a `Path` in a record is one machine's spelling of a path.
    """
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    return str(value)


def read(path: Union[str, Path]) -> Dict[str, Any]:
    """Read a sidecar back. For a CI job asserting on what a build recorded.

    Raises:
        ProvenanceError: no such file, or a file that is not JSON. Both mean the
            record cannot be trusted, which is the same answer as it being absent.
    """
    target = Path(path)
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProvenanceError(f"no provenance sidecar at {target}") from error
    except json.JSONDecodeError as error:
        raise ProvenanceError(f"{target} is not JSON: {error}") from error


def verify_pair(html: Union[str, Path]) -> List[str]:
    """Does the sidecar beside this HTML still describe it? Returns problems, not
    an exception: a report folder with three stale sidecars has three to list.

    Checks what a reader would be misled by — that a file exists, that it is for
    this report, and that the DataIDs it names are the ones in the page. Not that
    the bytes match: the sidecar is a record of the build, and a hand-edited HTML
    is a different problem than a stale record.
    """
    path = Path(html)
    if not path.is_file():
        return [f"{path.name}: no such report"]
    sidecar = sidecar_path(path)
    if not sidecar.is_file():
        return [f"{path.name}: no provenance sidecar beside it"]
    try:
        record = read(sidecar)
    except ProvenanceError as error:
        return [str(error)]

    from .verify import data_ids_from_html

    problems: List[str] = []
    titled = ((record.get("report") or {}).get("title")) or ""
    if titled and titled not in path.read_text(encoding="utf-8", errors="replace"):
        problems.append(f"{sidecar.name}: title {titled!r} is not in the HTML")
    recorded = list(record.get("data_ids") or ())
    in_page = data_ids_from_html(path.read_text(encoding="utf-8", errors="replace"))
    missing = [item for item in in_page if item not in recorded]
    if missing:
        problems.append(
            f"{sidecar.name}: {len(missing)} DataIDs in the page are not in the "
            f"record (first: {missing[0]})"
        )
    return problems


__all__ = [
    "KEYS",
    "SUFFIX",
    "Provenance",
    "ProvenanceError",
    "design_of",
    "for_cli",
    "for_manifest",
    "for_page",
    "for_report",
    "logged_dir",
    "read",
    "sidecar_path",
    "tool_version",
    "verify_pair",
    "write_for",
]
