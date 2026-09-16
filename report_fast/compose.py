"""Composing a report from sessions and components, with nothing persisted.

DESIGN.md step 4: the door prompt mode enters through. `reportfast build
--sessions-dir …` reads agent-authored session JSON, validates it through the
step-2 gate, composes from the frozen set, writes one HTML file, and leaves no
intermediate behind. Same gates as a manifest, no YAML, no persisted state.

The shape this exists to serve, and the reason the API is split the way it is:

    design = compose.SessionTemplate.from_config(     # ONE hand-authored JSON
        json.load(open("design.json")), slots={0: "slide", 1: "mask"}, strict=True
    )
    sessions = [                                      # ...iterated over 300 slides
        design.bind(slide=path, mask=f"{path}.prob.tif", name=Path(path).stem)
        for path in slides
    ]
    report = Composition(title="QC", blocks=[SlideGrid(sessions=sessions)]).build()

One session *design* comes from the agent's hand; the 300 instances come from a
loop. That is why `Composition` is not the thing that reads a directory of
hundred-session JSON files, and why `expand()` below is opt-in sugar rather than
the main path: the design is the authored object, and instantiation is Python the
caller can see.

Two decisions worth their weight:

* **The gate runs at composition, not at write time.** By the time
  `Composition.build` runs, the blocks hold constructed components, and asking a
  finished `RawHtml` object to justify itself means unwinding it. So
  `authorize()` is what turns a spec into objects, and `build()` refuses specs it
  was not given. Passing pre-built components is still allowed -- that is the
  Python path, where the author is a person writing Python, and decision 10 is a
  rule about what the *agent* may emit.
* **`to_manifest()` is a suggestion.** `--emit-manifest` prints a manifest that
  would reproduce the report, for a human to keep and edit afterwards. Nothing
  calls it in the build path: the default prompt flow persists no intermediates,
  and printing something on the way to a finished HTML file would make the
  intermediate the contract by accident.
"""

from __future__ import annotations

import json
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
)

from .audit import join, split
from .components.slide_grid import SlideCard, SlideGrid
from .core import BaseComponent, Report
from .frozen import Block, authorize, create
from .manifest import Plan
from .session import SessionTemplate, XopatSession
from .xopat import XopatEndpoint, XopatError

if TYPE_CHECKING:  # pragma: no cover - annotations only, keeps the cycle off
    # provenance imports walk_sessions from here, so this module must not
    # import it at module scope. The arrow points one way at runtime.
    from .provenance import Provenance

#: The slot a bare `--sessions-dir` fills when nobody names one. `slide` because
#: it is the slot a one-background design has, so the 300-slide case needs no flag.
DEFAULT_SLOT = "slide"

#: How a manifest-less report lays its cards out, and the flag's two spellings.
#: `grid` because that is what a QC report of 300 slides is: one card per case,
#: browsed. `rows` is the manifest's `grid: false`, one card per row, for a report
#: whose cards are tall enough to want the whole width.
DEFAULT_LAYOUT = "grid"
LAYOUTS = ("grid", "rows")


def layout_blocks(
    sessions: Sequence[XopatSession], layout: str = DEFAULT_LAYOUT
) -> List[Any]:
    """The blocks a `--sessions-dir` report is made of: its cards, laid out.

    Returned as *specs*, not components, so they go through the frozen gate like
    everything else on this door -- a door that built `SlideGrid` directly would be
    a composition that never consults the table, which is the half of decision 10
    that has to hold for the path an agent drives.

    Raises:
        ComposeError: an unknown layout, naming the two there are. A typo'd
            `--layout gird` must not quietly become a grid.
    """
    if layout not in LAYOUTS:
        raise ComposeError(
            f"--layout {layout!r} is not one of {list(LAYOUTS)}. A folder of "
            "sessions has exactly two shapes: a grid of cards, or one card per row."
        )
    if not sessions:
        raise ComposeError(
            "no sessions to lay out. Each --session file is one session document, "
            "and --sessions-dir reads every *.json in the folder."
        )
    if layout == "grid":
        return [{"SlideGrid": {"sessions": list(sessions)}}]
    return [{"SlideCard": {"session": session}} for session in sessions]


#: Where a composition writes when `-o` is not given. A fixed, printed name --
#: the manifest path derives its filename from the manifest's own path, and a
#: composition has no path to derive one from. Inventing one from the title would
#: write `Ductal Carcinoma In Situ.html` and surprise someone on the second run.
DEFAULT_OUT = Path("report.html")


class ComposeError(ValueError):
    """A composition that cannot be honoured, said in terms of the composition."""


class ComposeNotFound(ComposeError, FileNotFoundError):
    """A session file or folder that is not there.

    The same two halves :class:`report_fast.manifest.ManifestNotFound` carries, for
    the same reason: a library caller catching `ComposeError` still gets it, while
    the CLI reads the `FileNotFoundError` half as "nothing to work on" (exit 4, go
    look at the folder) rather than "your JSON is wrong" (exit 1, go edit it at
    this path). Those are different errands, and one exception type would make the
    CLI guess from the wording of a message.
    """


def _is_path(source: Any) -> bool:
    """Whether `source` names a file to read, rather than being the document.

    A `Path` is always a path. A string is one only when it looks like one,
    because the CLI's `--session '{"data": …}'` and a file argument arrive through
    the same door, and `Path('{"data": …}')` is a legal-looking path that simply
    does not exist -- which would otherwise be reported as a missing file instead
    of read as the JSON it is.
    """
    if isinstance(source, Path):
        return True
    if not isinstance(source, str):
        return False
    text = source.strip()
    return not text.startswith(("{", "[")) and len(text) < 500 and "\n" not in text


def load_session(
    source: Union[str, Path, Mapping[str, Any]],
    *,
    endpoint: Optional[XopatEndpoint] = None,
    strict: bool = True,
    origin: str = "session",
) -> XopatSession:
    """Read one authored session, through the step-2 gate.

    `strict` is the whole difference between this and `from_config`: a
    key outside the allowlist, an undeclared layer param, a dangling `data[]`
    index is an **error** here, named by its JSON path, because the author is an
    agent that can go and edit the file. A paste keeps its bytes and gets a
    warning instead -- see :meth:`report_fast.session.XopatSession.validate`.

    Args:
        source: A `.json` path, a JSON string, or a mapping.
        endpoint: Deployment the session will be linked against.
        strict: Refuse anything the viewer would drop. `False` warns, for a
            composition that declares `strict: false` -- rare, and worth noticing
            in review when it appears.
        origin: Where it came from, for the message. A filename:
            `visualizations[0].shaders.dose.params.threshhold` is only actionable
            once you know *which* of fourteen files to open.

    Raises:
        ComposeError: the file is unreadable, or the audit refuses it.
    """
    try:
        document = Path(source).read_text(encoding="utf-8") if _is_path(source) else source
    except (FileNotFoundError, IsADirectoryError) as error:
        # Named types only. A PermissionError says *this file, this content, your
        # credentials* -- exit 1, an errand about the file -- while these two say
        # the file is not there to have an opinion about.
        raise ComposeNotFound(f"{origin}: no such file -- {error.filename or source}") from error
    except OSError as error:
        raise ComposeError(f"{origin}: cannot be read -- {error}") from error

    # Read non-strict, then audit here. Not cosmetic: on this path every soft
    # finding is an error, so either we raise or there were none -- and a run that
    # printed ten warnings and *then* refused for those same ten reasons reads
    # like two separate problems.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            # `strict=False` says "warn, don't shout": the audit below turns every
            # finding into one error with every path in it, instead of ten warnings
            # followed by a refusal for the same ten reasons. It must not also set
            # the *session's* verdict -- `from_config` derives `authoritative` from
            # that flag, and `authoritative` is what every later `validate()` and
            # every `bind()` in a 300-case loop obeys. Left at False, a session
            # that cleared this door would go back to warning about the same keys
            # at build time, which is the halfway-loop bug `bind` documents.
            # So: read leniently, then stamp the verdict this door actually gave.
            session = XopatSession.from_config(document, endpoint=endpoint, strict=False)
        except XopatError as error:
            # The findings a paste is refused too -- a `data[]` reference that is
            # not there. `from_config` raises for those regardless of `strict`.
            raise ComposeError(f"{origin}: {error}") from error
        except json.JSONDecodeError as error:
            raise ComposeError(
                f"{origin}: is not JSON -- {error}. The file is the session "
                'document itself ({"data": [...], "background": [...]}), not a '
                "wrapper around it."
            ) from error

    session.authoritative = bool(strict)
    if not strict:
        return session
    errors, _ = split(session.findings(), strict=True)
    if errors:
        raise ComposeError(
            f"{origin}: will not load in xOpat v3 as authored -- {join(errors)}. "
            "Fix the JSON at those paths. This is the agent's door, so a key the "
            "viewer would merely drop is refused rather than warned about: "
            "off-allowlist means a typo until proven otherwise."
        )
    return session


def load_design(
    source: Union[str, Path, Mapping[str, Any]],
    *,
    slots: Optional[Mapping[int, str]] = None,
    endpoint: Optional[XopatEndpoint] = None,
    strict: bool = True,
    origin: str = "session",
) -> SessionTemplate:
    """Read one authored session *design* as a template, gated once.

    This is the step-2 gate as the agent's design gate: the hand-written JSON is
    audited strictly, once, at the door, and every one of the 300 bindings
    inherits that verdict through `SessionTemplate.bind`. Passing `strict` through
    matters for a reason that is easy to miss -- `XopatSession.from_config` sets
    the session's `authoritative` flag from it, so a design read non-strict would
    stay non-strict for all 300 bindings, and the gate would have silently become
    a warning nobody reads.

    Args:
        source: The design: a `.json` path, JSON text, or a mapping.
        slots: Which `data[]` indices are filled per case, named: `{0: "slide",
            1: "mask"}`. Unset means index 0, named `slide`.
        endpoint: Deployment to link the bound sessions against.
        strict: Refuse anything the viewer would drop.
        origin: Filename, for the message.

    Raises:
        ComposeError: the design fails the gate, or names a slot the session does
            not have. The latter is the mistake a 300-slide loop makes when a
            design gains a background and the caller's `slots=` did not follow.
    """
    session = load_session(source, endpoint=endpoint, strict=strict, origin=origin)
    try:
        return SessionTemplate.from_config(
            session.to_config(),
            slots=dict(slots) if slots else {0: DEFAULT_SLOT},
            endpoint=endpoint,
            strict=strict,
        )
    except Exception as error:  # XopatError: a slot outside data[]
        raise ComposeError(f"{origin}: {error}") from error


def expand(
    design: Union[str, Path, Mapping[str, Any], SessionTemplate],
    cases: Iterable[Mapping[str, Any]],
    *,
    slots: Optional[Mapping[int, str]] = None,
    endpoint: Optional[XopatEndpoint] = None,
    strict: bool = True,
    origin: str = "design",
) -> List[XopatSession]:
    """Bind one design to many cases -- the loop, with the error messages kept.

    The explicit loop in the module docstring is the real API and this is sugar
    over it; the only thing added is that a failure says *which* case failed.
    Without that, slide 217 of 300 raising on a slot typo costs a search through
    the list.

    A case is one mapping per report row: slot values by name, plus the optional
    ``name`` and ``params`` keys `bind` already takes.

    Args:
        design: The design, or a template already made from one.
        cases: One mapping per row, as described above.
        slots: Which `data[]` indices the design exposes, forwarded to
            :func:`load_design` when a design is handed over rather than a
            template. Ignored for a template, which already knows.
        endpoint: Deployment to link the sessions against.
        strict: Refuse anything the viewer would drop.
        origin: Filename, for the messages.

    Raises:
        ComposeError: some case could not be bound, named by index and by the slot
            values that were offered for it.
    """
    template = (
        design
        if isinstance(design, SessionTemplate)
        else load_design(
            design, slots=slots, endpoint=endpoint, strict=strict, origin=origin
        )
    )
    sessions: List[XopatSession] = []
    for number, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ComposeError(
                f"{origin}: case {number} is {type(case).__name__}, expected a "
                "mapping of slot names to values, e.g. {'slide': '/m/a.tif'}"
            )
        row = dict(case)
        name = row.pop("name", None)
        params = row.pop("params", None)
        try:
            sessions.append(template.bind(name=name, params=params, **row))
        except Exception as error:
            raise ComposeError(
                f"{origin}: case {number} ({', '.join(f'{key}={value}' for key, value in sorted(row.items()))}) "
                f"could not be bound -- {error}"
            ) from error
    if not sessions:
        raise ComposeError(
            f"{origin}: no cases. A report with nothing in it is not a report -- "
            "check the folder or the list you meant to bind."
        )
    return sessions


@dataclass
class Composition:
    """A report spec in memory: which sessions, which blocks, in what order.

    The in-memory counterpart of a manifest, and deliberately not a second
    implementation of one -- `to_plan()` hands the same `Plan` object
    :func:`report_fast.manifest.build` consumes, so the probe, the publish rules
    and the exit codes are literally the same code rather than a paraphrase of
    them. Two build paths that "both probe" is how one of them stops probing.

    Attributes:
        title: Page title.
        blocks: Either `Block` specs -- which go through the frozen gate -- or
            already-built components, which are the Python path and are not
            re-litigated.
        subtitle, theme, css: Passed to `Report`.
        strict: Whether the frozen gate runs. `False` only for a caller that has
            already decided what it wants; the CLI never passes it.
    """

    title: str = "Report"
    subtitle: str = ""
    blocks: List[Any] = field(default_factory=list)
    theme: str = "auto"
    css: str = ""
    strict: bool = True
    #: Where each block came from, for the error message. A composition read from
    #: a directory has filenames to name; one built in Python has nothing, and
    #: `blocks[3]` is then the honest answer.
    origins: List[str] = field(default_factory=list, repr=False)
    #: What this composition was *given*, for the provenance sidecar: the flags a
    #: command line used, or the folder the sessions were read from. Nothing here
    #: re-derives it -- a record of a build's inputs is only true if the caller
    #: that held the inputs wrote it. Empty is a legitimate answer for a page
    #: assembled in Python, and the sidecar says so rather than inventing one.
    inputs: Dict[str, Any] = field(default_factory=dict, repr=False)
    #: The session design as authored, when the sessions came from a template.
    #: One document, not 300 instantiations -- see `provenance.design_of`.
    design: Optional[Dict[str, Any]] = field(default=None, repr=False)
    #: The page this composition built, and the block list it was built from.
    #: `field(compare=False)` so two compositions with the same blocks still
    #: compare equal -- these two are a cache, not part of what a composition *is*.
    _built: Optional[List[BaseComponent]] = field(default=None, repr=False, compare=False)
    _built_from: Optional[List[Any]] = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------- composing

    def specs(self) -> List[Block]:
        """The blocks as `Block` specs, wrapping names/mappings where needed.

        Pre-built components are left alone and reported by `authorize` as
        authorship rather than treated as suspicious: decision 10 constrains what
        a *spec* can name, and a person holding Python is the author the rule
        permits.
        """
        made: List[Block] = []
        for block in self.blocks:
            if isinstance(block, Block):
                made.append(block)
            elif isinstance(block, str):
                made.append(Block(block, {}))
            elif isinstance(block, Mapping):
                if len(block) != 1:
                    raise ComposeError(
                        f"{self.title}: a block is one component name to its "
                        f"arguments, got the keys {sorted(str(key) for key in block)}"
                    )
                (name, arguments), = block.items()
                if isinstance(arguments, str) or not isinstance(
                    arguments, (Mapping, Sequence)
                ):
                    # The manifest shorthand (`heading: A title`, `prose: text`).
                    # This door takes the constructor's own keyword arguments,
                    # because translating a manifest's block shapes here would put
                    # a second, slightly different set of rules in front of the
                    # frozen table -- which is the one list decision 10 is about.
                    raise ComposeError(
                        f"{self.title}: {name} takes its arguments as a mapping "
                        f"of keyword arguments, not {arguments!r}. The manifest "
                        "spelling is shorter and belongs to `reportfast build "
                        "r.yaml`; here, write the component's own keywords, e.g. "
                        '{"Heading": {"text": "Cohort", "level": 3}}.'
                    )
                if isinstance(arguments, Sequence):
                    raise ComposeError(
                        f"{self.title}: {name} takes keyword arguments, not a list "
                        f"({arguments!r}). Name each one: the frozen components "
                        "have keyword-only arguments in most cases."
                    )
                made.append(Block(str(name), dict(arguments or {})))
            # A constructed component: nothing to check, nothing to construct.
        return made

    def components(self) -> List[BaseComponent]:
        """Build the spec blocks through the frozen gate; pass the rest through.

        Built once and kept. Not for speed: `sessions()`, `to_plan()`,
        `to_manifest()` and `build` each need the page, and if each of them
        constructed its own components then the sessions the plan counts, the
        sessions the probe checks and the sessions the page links would be four
        different sets of equal-but-distinct objects. Nothing here holds state
        across those calls today, but a provenance sidecar written from one walk
        and a page written from another is exactly the disagreement a record must
        not have. Re-composed when `blocks` changes, by identity.
        """
        if self._built is not None and self._built_from == self.blocks:
            return self._built
        if self.strict:
            authorize(self.specs())
        made: List[BaseComponent] = []
        built = iter(self._build_specs())
        for block in self.blocks:
            if isinstance(block, (Block, str, Mapping)):
                made.append(next(built))
            elif isinstance(block, BaseComponent):
                made.append(block)
            else:
                raise ComposeError(
                    f"{self.title}: {block!r} is not a component, a block spec or a "
                    "name; the frozen set is " + ", ".join(sorted(_FROZEN_NAMES))
                )
        self._built, self._built_from = made, list(self.blocks)
        return made

    def _build_specs(self):
        for block in self.specs():
            yield create(block) if isinstance(block, (Block, str, Mapping)) else block

    def to_report(self) -> Report:
        """The page, built and authorised."""
        return Report(
            title=self.title,
            subtitle=self.subtitle,
            blocks=self.components(),
            theme=self.theme,
            css=self.css,
        )

    # ------------------------------------------------------------- the gates

    def sessions(self) -> List[XopatSession]:
        """Every session the page links to, for probing and for the sidecar.

        Read out of the built components rather than passed in, so it cannot
        disagree with what is actually on the page -- a provenance record naming
        sessions the report does not link would be worse than no record.

        A grid's cards *are* the grid's sessions (`SlideGrid.children` returns
        them), so the walk stops at a grid rather than descending into it. The
        first version of this did both and counted every card twice, which reads
        as "60 cases" over a report of 30 and then probes 60 DataIDs.
        """
        return walk_sessions(self.to_report().blocks)

    def to_plan(self) -> Plan:
        """A manifest `Plan` for this composition, so `manifest.build` can take it.

        `out=False` in the sense that `Plan.out` is left ``None``: a composition
        has no opinion about where its file goes until `-o` says so, and a default
        path invented here is a file the user did not ask for.
        """
        sessions = self.sessions()
        if not sessions:
            raise ComposeError(
                f"{self.title}: nothing to build -- the composition has no "
                "SlideCard or SlideGrid, so the page would link to no session. "
                "That may be intentional, in which case build the Report and write "
                "it yourself rather than through this door."
            )
        return Plan(
            title=self.title,
            subtitle=self.subtitle,
            out=None,
            cases=case_labels(sessions),
            layers={
                _label(session): len(session.visualizations[0]["shaders"])
                if session.visualizations
                else 0
                for session in sessions
            },
            warnings=[],
            sessions=sessions,
        )

    def build(
        self,
        *,
        out: Union[str, Path, bool, None] = None,
        check: bool = True,
        endpoint: Optional[XopatEndpoint] = None,
        provenance: Optional["Provenance"] = None,
        with_provenance: bool = True,
    ):
        """Compose, write, probe. Returns a manifest :class:`~report_fast.manifest.Built`.

        The probe and the result type are imported from the manifest path rather
        than restated here, which is the part worth sharing: two build paths that
        each keep their own list of what to probe is how one of them stops
        probing. What is deliberately *not* imported is publish -- there is no
        `publish` parameter to pass, and building a report has never implied
        uploading one.

        Args:
            out: Where to write. Defaults to `report.html` beside the working
                directory, named and printed rather than guessed from a manifest
                path that does not exist. Pass ``False`` to build without writing
                anything -- the same promise the manifest path makes for
                `--check-only`, and honoured the same way. It has to be spelled
                `False` rather than left out, because leaving it out is the
                default and the default is a file.
            check: Probe every DataID against the image server afterwards -- the
                same gate `reportfast build --check` runs.
            endpoint: Deployment to probe against.
            provenance: The record to write beside the HTML. `None` collects one
                from what this composition knows -- title, cases, sessions, the
                resolved endpoint, plus `inputs` and `design` if a caller filled
                them in. Pass one built by `provenance.for_cli` to record the
                flags a command line was actually given.
            with_provenance: Set `False` to write the page and no sidecar. The
                default is what decision 8 rules -- *every* build records itself --
                so this is a parameter for a caller writing to a temp dir it is
                about to delete, not a way to opt out of the practice.
        """
        from .manifest import Built
        from .provenance import Provenance, write_for
        from .verify import probe, session_data_ids
        from .xopat import resolve_endpoint

        # The plan first, and the report from the same built components: the
        # DataIDs probed below are then literally the ones the page links, not an
        # equivalent list derived from a second construction of the page.
        plan = self.to_plan()
        report = self.to_report()
        built = Built(report=report, plan=plan)
        # `out is False` and `out is None` are different answers, and `if out`
        # cannot tell them apart -- which is exactly how `--check-only` ended up
        # writing report.html in the caller's directory while printing "wrote
        # nothing". Same shape as `manifest.build`'s check, for the same reason.
        if out is not False:
            destination = Path(out) if out else DEFAULT_OUT
            built.out = report.write(destination)
        if check:
            target = resolve_endpoint(endpoint)
            built.checks = probe(session_data_ids(plan.sessions), target)
        # The sidecar after the page, from the same objects the page was built
        # from: `plan.sessions` is the cached component walk, so the record names
        # the sessions the HTML links rather than a second reading of the spec.
        # `built.out` is None when `out=False`, and `write_for` writes nothing for
        # a report that does not exist -- a record beside no report is a rumour.
        if with_provenance:
            record = provenance or Provenance()
            if not record.report:
                record = self.record_provenance(endpoint=endpoint)
            built.provenance = write_for(built.out, record)
        return built

    def record_provenance(
        self,
        *,
        endpoint: Optional[XopatEndpoint] = None,
        design: Optional[Dict[str, Any]] = None,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> "Provenance":
        """The record for this composition, without building it.

        Split out because `build` needs the file written beside a path it chose,
        and a publish needs the same record without a local file at all. Both take
        it from here so neither can describe the page differently.
        """
        from .provenance import for_report

        sessions = self.sessions()
        return for_report(
            title=self.title,
            cases=[_label(session) for session in sessions],
            sessions=sessions,
            endpoint=endpoint,
            design=design if design is not None else self.design,
            inputs=inputs if inputs is not None else self.inputs,
        )

    # ------------------------------------------------------------- the offer

    def to_manifest(self) -> str:
        """A manifest that would reproduce this report, as YAML text.

        Not called by anything in the build path. `--emit-manifest` prints it for a
        human who wants to keep the composition as a file; the prompt flow
        persists nothing by default, and a gate that quietly grows an artifact is
        how an intermediate becomes the contract.

        The emission is *partial by design* and says so when it runs into the
        part. A manifest can name sessions (`sessions:`, `sessions_from:`) and
        eight kinds of block; it has no block for a grid or a card, because the
        grid is what `build_report` makes *from* `sessions:`. So a composition
        whose grid holds exactly the report's sessions becomes `sessions:` plus
        `grid:`, and one with a card in the middle of a section has no manifest
        spelling at all. Refusing there is the whole point: a printed manifest
        that `reportfast plan` rejects -- or worse, that reads but lays the page
        out differently -- turns the suggested reproducible record into the
        believable kind of wrong. A composition built in Python with hand-made
        components is the same story: the components are the record, and there is
        no honest YAML for `RawHtml(<b>mine</b>)`.
        """
        spec: Dict[str, Any] = {"title": self.title}
        if self.subtitle:
            spec["subtitle"] = self.subtitle
        if self.theme != "auto":
            spec["theme"] = self.theme

        sessions = self.sessions()
        spec["sessions"] = [
            {"from_config": session.to_config()} for session in sessions
        ]
        endpoint = _shared_endpoint(sessions)
        if endpoint is not None:
            spec["endpoint"] = endpoint

        blocks: List[Any] = []
        grid: Dict[str, Any] = {}
        walked = 0
        for block in self.components():
            # A generated grid over every session *is* the manifest's own shape:
            # `sessions:` plus an optional `grid:`. Anything else is a block.
            if (
                isinstance(block, SlideGrid)
                and list(block.sessions) == sessions
                and not blocks
                and walked == 0
            ):
                grid = _grid_options(block)
                walked += 1
                continue
            written = _manifest_block(block)
            if written is None:
                raise ComposeError(
                    f"{self.title}: this composition has no manifest form -- "
                    f"{type(block).__name__} is not a manifest block. The manifest "
                    "path has no block for a card or a nested grid, and no "
                    "expression for a component built in Python: use "
                    "`sessions:`/`sessions_from:` with `grid:`, or keep the "
                    "composition in Python, which is itself the record."
                )
            blocks.append(written)
            walked += 1
        if grid:
            spec["grid"] = grid
        if blocks:
            spec["blocks"] = blocks

        try:
            import yaml

            text = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)
        except ImportError:  # pragma: no cover - manifest extra not installed
            text = json.dumps(spec, indent=2, default=str)
        return (
            "# A composition this tool built, restated as a manifest. `reportfast "
            "plan` reads it.\n# Sessions are inlined as authored; to keep this "
            "file in git, replace them with a\n# `sessions_from:` function or "
            "`from_file:` pointing at the session JSON, and keep the\n"
            "# resolved detail in the run rather than in the file.\n" + text
        )


def _manifest_block(component: BaseComponent) -> Optional[Mapping[str, Any]]:
    """One built component, written back the way a manifest would have said it.

    Returns ``None`` for anything with no manifest spelling, which the caller
    turns into a refusal rather than a guess. The mapping below is *not* a rename
    table: each entry is the key `manifest._block` reads and the value shape it
    passes to the same constructor, so `heading: Text` and `level: 3` are not
    representable together and come back out as `Heading("Text", 3)`. A component
    holding a `Path`, a URL or anything not plain data is likewise refused -- a
    printed manifest is only worth having if `reportfast plan` accepts it.
    """
    name = type(component).__name__
    if name == "Heading":
        if getattr(component, "level", 2) != 2:
            return None
        return {"heading": component.text}
    if name == "Prose":
        return {"prose": list(component.paragraphs)}
    if name == "Bullets":
        return {"bullets": list(component.items)}
    if name == "LinkList":
        return {"links": {str(label): str(url) for label, url in component.links}}
    if name == "Section":
        rows = {"title": component.title, "blocks": []}
        if component.subtitle:
            rows["subtitle"] = component.subtitle
        if component.collapsible:
            rows["collapsible"] = True
            if not component.open:
                rows["open"] = False
        for inner in component.blocks:
            written = _manifest_block(inner)
            if written is None:
                return None
            rows["blocks"].append(written)
        return {"section": rows}
    if name == "MetricTable":
        # `metrics:` is a literal table, and a table that came from a run did not
        # have to be written down at all: say where it came from instead.
        if not getattr(component, "headers", None) and not getattr(component, "rows", None):
            return {"metrics": []}
        keys = [header for header in component.headers if header]
        if not keys or _ragged(component.rows, len(component.headers)):
            return None
        if component.headers[0] == "":  # `{label: {field: value}}`, index column first
            rows = {
                row[0]: dict(zip(keys, row[1:], strict=True)) for row in component.rows
            }
        elif component.headers == ["metric", "value"]:
            rows = {str(row[0]): row[1] for row in component.rows}
        else:
            rows = [dict(zip(component.headers, row, strict=True)) for row in component.rows]
        if not _plain(rows):
            return None
        return {"metrics": rows}
    return None


def _ragged(rows: Sequence[Sequence[Any]], width: int) -> bool:
    """A row shorter than the header would come back with keys missing."""
    return any(len(row) != width for row in rows)


def _plain(value: Any) -> bool:
    """Whether `value` survives a YAML round trip as itself.

    `yaml.safe_dump` happily writes a `Path` or a numpy float as a string and the
    reader then cannot tell what it was, so a manifest is only emitted when every
    leaf is one of the four types a person could have typed.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _plain(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_plain(item) for item in value)
    return False


def _grid_options(grid: SlideGrid) -> Dict[str, Any]:
    """A grid's `grid:` mapping -- only what differs from the library default.

    Written sparsely so the manifest stays the short, hand-editable thing it is
    supposed to be: `min_width: 260` is the default and does not belong in a file
    someone has to read.
    """
    options: Dict[str, Any] = {}
    defaults = SlideGrid()
    if grid.min_width != defaults.min_width:
        options["min_width"] = grid.min_width
    if grid.collapsible != defaults.collapsible:
        options["collapsible"] = bool(grid.collapsible)
    if grid.open != defaults.open:
        options["open"] = bool(grid.open)
    card = {
        key: value
        for key, value in (grid.card_defaults or {}).items()
        if key != "endpoint" and value is not None
    }
    if card:
        options["card"] = card
    return options


def _shared_endpoint(
    sessions: Sequence[XopatSession],
) -> Optional[Dict[str, Any]]:
    """The endpoint every session shares, as a manifest's `endpoint:` row.

    ``None`` when they disagree -- in which case the manifest would have to say it
    per session, which it cannot, and printing one of the fourteen would be a
    manifest that builds a different report.
    """
    rows = set()
    for session in sessions:
        endpoint = session.endpoint
        if endpoint is None:
            return None
        rows.add(
            (
                endpoint.base_url,
                endpoint.wsi_base_url,
                endpoint.image_protocol,
                endpoint.mount_root,
            )
        )
    if len(rows) != 1:
        return None
    base_url, wsi_base_url, image_protocol, mount_root = rows.pop()
    return {
        "base_url": base_url,
        "wsi_base_url": wsi_base_url,
        "image_protocol": image_protocol,
        "mount_root": mount_root,
    }


def _label(session: XopatSession) -> str:
    """A session's name, the way the viewer and the grid both read it."""
    names = session.names
    return names[0] if names else "case"


def case_labels(sessions: Iterable[XopatSession]) -> List[str]:
    """One label per session, in order -- the plan's `cases`, the sidecar's.

    Public because two records of the same page (a plan and a provenance file)
    have to spell the cases identically, and a second implementation of the
    fallback name is how they start to differ.
    """
    return [_label(session) for session in sessions]


def walk_sessions(nodes: Iterable[Any]) -> List[XopatSession]:
    """Every session a list of components links to, in page order.

    The rule the double-count taught: a `SlideGrid` *owns* its cards, so the walk
    takes the grid's sessions and stops. Descending into the grid's children as
    well counts every case twice -- which is not merely a wrong number printed,
    it is the wrong number of DataIDs sent to the image server.
    """
    found: List[XopatSession] = []

    def walk(node: Any) -> None:
        if isinstance(node, SlideGrid):
            found.extend(node.sessions)
            return
        if isinstance(node, SlideCard):
            found.append(node.session)
        for child in getattr(node, "children", lambda: ())():
            walk(child)

    for node in nodes:
        walk(node)
    return found


_FROZEN_NAMES = frozenset(
    {"SlideCard", "SlideGrid", "Prose", "Heading", "Bullets", "LinkList",
     "MetricTable", "Chart", "Section", "Report"}
)


def sessions_from_dir(
    directory: Union[str, Path],
    *,
    endpoint: Optional[XopatEndpoint] = None,
    strict: bool = True,
    pattern: str = "*.json",
) -> List[XopatSession]:
    """Every session JSON in a folder, in name order, each through the gate.

    The `--sessions-dir` door, minus the page: one JSON per case, each file
    audited as authored. Ordering is by filename because a report the same command
    runs twice has to lay the cards out the same twice, and `Path.glob` does not
    promise that.
    """
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise ComposeNotFound(
            f"--sessions-dir {root} is not a directory. Nothing was read, so "
            "nothing is wrong with the sessions inside it."
        )
    # Our own sidecars are not sessions. `-o` inside the folder being reported on
    # is a natural thing to type, and the sidecar it drops there is a `*.json` the
    # glob below would otherwise collect -- so the first build would poison the
    # second one's input, and the gate's complaint would be about `kind`, `tool`
    # and `data_ids` not being viewer keys. True, useless, and self-inflicted:
    # this tool's output is not this tool's input, which is the same rule that
    # keeps `reports/*.html` out of the source tree.
    from .provenance import SUFFIX

    files = sorted(
        item
        for item in root.glob(pattern)
        if item.is_file() and not item.name.endswith(SUFFIX)
    )
    if not files:
        # A folder of nothing but sidecars says so, because that is the one shape
        # of this mistake where the fix is obvious once it is named -- and the
        # generic message would have the reader checking files that are fine.
        only = [item.name for item in root.glob(pattern) if item.name.endswith(SUFFIX)]
        if only:
            raise ComposeNotFound(
                f"--sessions-dir {root} holds only provenance sidecars "
                f"({', '.join(only[:3])}{'...' if len(only) > 3 else ''}), which "
                "describe reports rather than being sessions. Point it at the "
                "folder of session JSON, or write the report somewhere else with -o."
            )
        # Not-found rather than wrong: an empty folder is nothing to work on
        # (exit 4), not a spec that needs editing (exit 1).
        raise ComposeNotFound(
            f"--sessions-dir {root} holds no {pattern} files, so the report would "
            "have no cases. Each file is one session document."
        )
    return [
        load_session(path, endpoint=endpoint, strict=strict, origin=path.name)
        for path in files
    ]


def temp_dir_sessions(
    sessions: Iterable[XopatSession],
) -> Any:
    """A context manager holding the sessions as JSON in a temp dir.

    For the reverse direction -- driving a CLI from Python, or a test of the door,
    where the sessions were built in memory but the door reads files. The
    directory is removed on exit, which is the point: the temp-dir door leaves no
    intermediates, and a helper that littered would contradict the feature.
    """
    return _TempSessions(list(sessions))


class _TempSessions:
    def __init__(self, sessions: Sequence[XopatSession]):
        self.sessions = list(sessions)
        self._directory: Any = None

    def __enter__(self) -> Path:
        self._directory = tempfile.TemporaryDirectory(prefix="reportfast-sessions-")
        root = Path(self._directory.name)
        for number, session in enumerate(self.sessions):
            (root / f"{number:03d}.json").write_text(
                json.dumps(session.to_config(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return root

    def __exit__(self, *exception: Any) -> None:
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None


__all__ = [
    "DEFAULT_LAYOUT",
    "DEFAULT_OUT",
    "DEFAULT_SLOT",
    "LAYOUTS",
    "ComposeError",
    "ComposeNotFound",
    "Composition",
    "expand",
    "layout_blocks",
    "load_design",
    "load_session",
    "sessions_from_dir",
    "temp_dir_sessions",
    "walk_sessions",
    "case_labels",
]
