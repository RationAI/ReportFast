"""``reportfast plan | build | find`` — the three commands, and their three gates.

    reportfast plan  reports/dysplasia.yaml             # writes nothing
    reportfast build reports/dysplasia.yaml             # writes one HTML file
    reportfast build reports/dysplasia.yaml --publish   # the only write to MLflow
    reportfast find  <run-id> --path tile_masks         # list a run's artifacts

Sessions the agent authored arrive through a second door, which takes a folder
instead of a YAML file and persists nothing but the HTML:

    reportfast plan  --design /tmp/x/design.json --slot 0=slide --slot 1=mask
    reportfast build --sessions-dir /tmp/x/sessions -o /tmp/x/report.html
    reportfast build --sessions-dir /tmp/x/sessions --publish --run RUN  # + sidecar

The split between them is the ruling that prompt mode entered through: **one**
session design is hand-authored, the 300 instances come from a Python loop
(:func:`report_fast.compose.expand`), and this CLI is the door the finished
sessions walk through. ``--design`` therefore *validates* a design and prints its
slots; it does not bind anything, because a command line that could express the
loop would be a second, worse ``expand()`` and would need a case-list file — the
kind of intermediate the default flow was ruled not to leave behind.

Each command's blast radius is the point of the split. ``plan`` resolves every
source and prints what the report would contain, and cannot write: it is the
thing worth reading before a build, and the artifact an agent shows a human.
``build`` writes the HTML and then probes every DataID against the image server,
because a report whose DataIDs were not resolved is a report nobody has checked.
Every build writes one more file beside the page: ``report.provenance.json``, the
record of what resolved — endpoint, viewer stamp, the sessions the links carry.
Nothing is stamped into the HTML itself, so a mailed report carries no provenance;
that is a ruled trade-off, not an oversight (``report_fast.provenance``).

``--publish`` is the only path that touches MLflow, is never implied by a
manifest's ``publish:`` key, and names the run it is going to before it goes. From
the manifest-less door it needs ``--run``, and what it logs in a manifest's place
is the sidecar: with no declared spec, the record of what resolved *is* the
configuration the run keeps.

Exit codes, for the CI job and the agent alike:

===== ==========================================================
``0``  fine
``1``  the *spec* is wrong -- a bad manifest key, a missing case, a short layer,
       or an authored session the viewer would not load as written
``2``  the HTML was written and a DataID did not resolve
``3``  an optional dependency this command needs is not installed
``4``  nothing to work on -- no such manifest, no such folder, no such run
===== ==========================================================

1 and 4 stay different on purpose, and the door does not blur them: a session
file that is not there is 4 (go and look at the folder), a session file that is
there and wrong is 1 (go and edit that JSON at that path).
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

__all__ = ["main"]

#: The argument never arrived, or the environment does not have the file the
#: command was pointed at. Different from 1, which says the *manifest* is wrong.
USAGE_ERROR = 4
#: The report exists and the image server refused part of it.
BROKEN_LINKS = 2
#: An optional extra (PyYAML, mlflow) is missing from this interpreter.
MISSING_EXTRA = 3

PLAN_HELP = """Resolve a manifest and print what it adds up to. Writes nothing.

Cases per layer, layers per case, files per source, and every warning the build
would raise -- all of it before a byte of HTML exists."""

BUILD_HELP = """Write the report, then probe every DataID against the image server.

Publishing is a separate, explicit --publish. Building never uploads."""

FIND_HELP = """List the artifacts of a run you already have.

Not run discovery: nothing here searches MLflow, so a run id comes from the UI or
from a person. What this does answer is 'what is under this run's artifacts/',
which is the question behind a manifest's run: and path: pair."""


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the CLI. Returns the process exit code; never raises on bad input."""
    parser = _parser()
    arguments = list(sys.argv[1:] if argv is None else argv)

    from .compose import ComposeError, ComposeNotFound
    from .frozen import CompositionError
    from .manifest import ManifestError, ManifestNoYaml, ManifestNotFound
    from .mlflow import MlflowError
    from .xopat import XopatError

    try:
        args = parser.parse_args(arguments)
        if getattr(args, "func", None) is None:
            parser.print_help()
            return USAGE_ERROR
        return int(args.func(args))
    except _Usage:
        # argparse already printed the usage and the reason.
        return USAGE_ERROR
    except SystemExit as exit_:  # `--help`: argparse prints and quits
        return int(exit_.code or 0)
    except UsageError as error:
        # A parseable line that asks for two things at once. Before the ValueError
        # family below, because it *is* a ValueError and would otherwise be
        # reported as a broken spec when nothing was ever read.
        print(f"reportfast: {error}", file=sys.stderr)
        return USAGE_ERROR
    except ComposeNotFound as error:
        # Named before ComposeError for the reason ManifestNotFound is named before
        # ManifestError: no file, and wrong file, are different errands.
        print(f"reportfast: {error}", file=sys.stderr)
        return USAGE_ERROR
    except ComposeError as error:
        # Includes the step-2 gate refusing an authored session, which is the most
        # common exit 1 on this door and the one whose message is a JSON path.
        print(f"reportfast: {error}", file=sys.stderr)
        return 1
    except CompositionError as error:
        # Decision 10: a composition naming a component the frozen set forbids.
        print(f"reportfast: {error}", file=sys.stderr)
        return 1
    except XopatError as error:
        # A session this tool would not hand to the viewer, reached outside the
        # door's own wrapping. Same verdict as a bad spec: the session is wrong.
        print(f"reportfast: {error}", file=sys.stderr)
        return 1
    except ManifestNotFound as error:
        # Named before ManifestError on purpose: a manifest that is not there is
        # not a manifest that is wrong, and the fixes are unrelated.
        print(f"reportfast: {error}", file=sys.stderr)
        return USAGE_ERROR
    except ManifestNoYaml as error:
        return _missing_extra("PyYAML", error, hint="uv sync --extra manifest")
    except MlflowError as error:
        # One exception type, two very different reasons: the extra is not
        # installed (fix the environment) versus the run or the tracking server
        # did not answer (fix the id, or the VPN). Saying "uv sync" about a
        # mistyped run id sends someone off installing things.
        if "not installed" in str(error):
            return _missing_extra("mlflow", error, hint="uv sync --extra mlflow")
        print(f"reportfast: {error}", file=sys.stderr)
        return USAGE_ERROR
    except ManifestError as error:
        print(f"reportfast: {error}", file=sys.stderr)
        return 1
    except (FileNotFoundError, NotADirectoryError) as error:
        print(f"reportfast: {error}", file=sys.stderr)
        return USAGE_ERROR


# ── the parser ──────────────────────────────────────────────────────────────


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that exits 4 on a usage error, not argparse's 2.

    argparse's default collides with 2 = "built, and a DataID did not resolve",
    and the two want different fixes: one is a typo on the command line, the
    other is a wrong run id in the manifest.
    """

    def error(self, message: str):  # pragma: no cover - argparse prints the usage
        self.print_usage(sys.stderr)
        print(f"reportfast: error: {message}", file=sys.stderr)
        raise _Usage(message)


class _Usage(Exception):
    """Bad command line. Carried as an exception so `main` owns the exit code."""


class UsageError(ValueError):
    """A command line that parses cleanly but asks for two incompatible things.

    argparse cannot say this one -- it is not about a missing flag but about a
    pair that means different things together -- so it is raised from the command
    body and answered by the top level with 4, like any other usage mistake.
    """


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="reportfast",
        description=(
            "Static HTML reports over xOpat v3 sessions: from authored session "
            "JSON, or from a manifest when you want one kept."
        ),
        epilog=(
            "Two doors, same gates. Authored sessions: `reportfast build "
            "--sessions-dir DIR -o report.html`, which persists nothing but the "
            "HTML. A manifest: `reportfast plan r.yaml` first -- it cannot write "
            "anything, and `build` can."
        ),
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    plan = commands.add_parser(
        "plan", help="resolve and report; writes nothing", description=PLAN_HELP
    )
    _add_common(plan)
    plan.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="machine-readable plan: cases, coverage, sources, warnings",
    )
    _add_authoring(plan, design=True)
    plan.set_defaults(func=_plan)

    build = commands.add_parser(
        "build",
        help="write the report HTML",
        description=BUILD_HELP,
        epilog="Publishing is never implied: only --publish uploads, and it says so first.",
    )
    _add_common(build)
    build.add_argument(
        "-o",
        "--out",
        default=None,
        help="where to write; defaults to the manifest's out:, else beside it",
    )
    build.add_argument(
        "--no-check",
        action="store_true",
        help="skip the DataID probe (offline, or a report that will not be opened)",
    )
    build.add_argument(
        "--check-only",
        action="store_true",
        help="build in memory and probe; write no file",
    )
    build.add_argument(
        "--publish",
        action="store_true",
        help=(
            "upload the report and its provenance (plus manifest + plan, when "
            "there is a manifest); the only write this tool performs"
        ),
    )
    build.add_argument(
        "--run",
        default=None,
        metavar="RUN_ID",
        help="publish into this run instead of the manifest's publish:",
    )
    _add_authoring(build)
    build.add_argument(
        "--layout",
        default="grid",
        choices=("grid", "rows"),
        help="how the cards are laid out (default: grid); --no-grid is rows",
    )
    build.add_argument(
        "--no-grid",
        action="store_const",
        const="rows",
        dest="layout",
        help="one card per row -- the same as --layout rows",
    )
    build.add_argument(
        "--emit-manifest",
        action="store_true",
        dest="emit_manifest",
        help="print a manifest that would reproduce this report; writes nothing",
    )
    build.set_defaults(func=_build)

    find = commands.add_parser("find", help="list a run's artifacts", description=FIND_HELP)
    find.add_argument("run_id", metavar="RUN_ID", help="a run id you already have")
    find.add_argument(
        "--path", default="", help="artifact subdirectory to list (default: the root)"
    )
    find.add_argument(
        "-r", "--recursive", action="store_true", help="descend into subdirectories"
    )
    find.add_argument(
        "--slides", action="store_true", help="only files with a slide suffix"
    )
    find.add_argument(
        "--data-ids",
        action="store_true",
        dest="data_ids",
        help="print one DataID per line and nothing else, for piping",
    )
    _add_tracking(find)
    find.set_defaults(func=_find)
    return parser


def _add_authoring(parser: argparse.ArgumentParser, *, design: bool = False) -> None:
    """The door sessions arrive through when no manifest exists.

    `--sessions-dir` takes a folder of session JSON: one file per case, each read
    through the agent's gate (an off-allowlist key is an error here, named by its
    JSON path and its filename). `--design` takes *one* design and stops at
    validation, which is what step 4's ruling leaves the CLI able to do -- see
    this module's docstring.

    Both live on `plan` as well as `build` for the same reason the manifest does:
    an agent needs to be able to check itself before it produces anything.
    """
    parser.add_argument(
        "--sessions-dir",
        default=None,
        metavar="DIR",
        help="a folder of agent-authored session JSON, one file per case",
    )
    parser.add_argument(
        "--session",
        action="append",
        default=[],
        metavar="PATH_OR_JSON",
        dest="sessions",
        help="one session, as a file or inline JSON; repeatable, order preserved",
    )
    if design:
        parser.add_argument(
            "--design",
            default=None,
            metavar="PATH_OR_JSON",
            help="check one authored session design and print its slots; binds nothing",
        )
        parser.add_argument(
            "--slot",
            action="append",
            default=[],
            metavar="INDEX=NAME",
            dest="slots",
            help="name a data[] index the design fills per case, e.g. 0=slide "
                 "(default: 0=slide); repeat, and --design prints the rest",
        )


def _add_tracking(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tracking-uri",
        default=None,
        metavar="URL",
        help="MLflow API root, when there is no flow: and the env is unset",
    )


def _add_common(parser: argparse.ArgumentParser) -> None:
    """The manifest plus the deployment overrides, shared by plan and build.

    Every flag here exists because a report built against the wrong mount *looks*
    like it worked, and because the same manifest is often aimed at a second
    deployment without being edited.
    """
    parser.add_argument(
        "manifest",
        nargs="?",
        default=None,
        help="the report spec: a YAML file describing backgrounds, masks and blocks "
             "(omit it and pass --sessions-dir for a report with no manifest)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="the page title, for a report built from --sessions-dir (a manifest "
             "carries its own)",
    )
    parser.add_argument(
        "--subtitle",
        default="",
        help="one line under the title, for a report built from --sessions-dir",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        metavar="URL",
        help="viewer root (v3 mount); defaults to XOPAT_BASE_URL",
    )
    parser.add_argument(
        "--wsi-base-url",
        default=None,
        metavar="URL",
        help="tile server root -- its own mount, not under --base-url",
    )
    parser.add_argument(
        "--image-protocol",
        default=None,
        metavar="NAME",
        help="registered protocol for the backgrounds, e.g. wsi_service",
    )
    parser.add_argument(
        "--mount-root",
        default=None,
        metavar="PREFIX",
        help="prefix stripped from paths to form a DataID; '' strips nothing",
    )
    _add_tracking(parser)


def _endpoint(args: argparse.Namespace):
    """Endpoint flags over the environment, or ``None`` when nothing was given.

    ``None`` matters as an answer: it lets the manifest's own ``endpoint:`` win.
    Anything set goes through ``from_env`` so the flags that were *not* given
    still land on the deployment default rather than on a bare dataclass field.
    """
    from .xopat import XopatEndpoint

    given = {
        "base_url": args.base_url,
        "wsi_base_url": args.wsi_base_url,
        "image_protocol": args.image_protocol,
        # `from_env` drops None overrides, and '' survives as the real answer --
        # which is the one worth having right, since it means "no mount prefix".
        "mount_root": args.mount_root,
    }
    if not any(value is not None for value in given.values()):
        return None
    return XopatEndpoint.from_env(**given)


def _flow(args: argparse.Namespace):
    """A client from ``--tracking-uri``, or ``None`` to let the manifest/env decide."""
    if not args.tracking_uri:
        return None
    from .mlflow import Mlflow

    return Mlflow.from_env(tracking_uri=args.tracking_uri)


# ── the commands ────────────────────────────────────────────────────────────


def _plan(args: argparse.Namespace) -> int:
    """Either door, one output shape. `plan` is the review artifact either way."""
    from .manifest import plan

    given = _authoring_given(args)
    if given == "design":
        return _plan_design(args)
    if given:
        planned = _composition(args).to_plan()
        print(planned.to_json() if args.as_json else planned.to_text())
        return 0
    if not args.manifest:
        # Reachable: `--slot` alone is a parseable command line that says nothing
        # about where the sessions are.
        print(
            "reportfast: plan needs a manifest, --sessions-dir DIR, --session FILE, "
            "or --design FILE. Pass one.",
            file=sys.stderr,
        )
        return USAGE_ERROR

    resolved = plan(args.manifest, flow=_flow(args), endpoint=_endpoint(args))
    print(resolved.to_json() if args.as_json else resolved.to_text())
    return 0


def _plan_design(args: argparse.Namespace) -> int:
    """Validate one authored design, and print its slots.

    This is as far as the CLI goes with a design, and the printing is the useful
    half: the caller learns which `data[]` indices the design *fills* and which it
    carries fixed -- a shared reference atlas at index 3 is intentional and must
    not be mistaken for an unslotable mistake. The binding then happens in Python,
    where the loop over 300 slides can be seen.
    """
    from .compose import load_design

    template = load_design(
        args.design,
        slots=_slots(args),
        endpoint=_endpoint(args),
        origin=_origin(args.design, "--design"),
    )
    session = template.session
    rows = {
        "design": _origin(args.design, "--design"),
        "slots": {str(index): name for index, name in sorted(template.slots.items())},
        "unslotted_data": [
            index for index in range(len(session.data)) if index not in template.slots
        ],
        "backgrounds": len(session.background),
        "visualizations": len(session.visualizations),
        "layers": len((session.visualizations or [{}])[0].get("shaders") or {}),
        "endpoint": _endpoint_row(session),
    }
    if args.as_json:
        import json

        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    print(f"{rows['design']}: a session design, gated as authored")
    for index, name in sorted(template.slots.items()):
        print(f"  slot      data[{index}] = {name}")
    if rows["unslotted_data"]:
        # Not an error, and saying so is the point: the first reading of a design
        # with a leftover index is usually "the caller forgot a slot".
        print(
            f"  fixed     data{rows['unslotted_data']} keep the design's own "
            "DataIDs (a shared atlas is intentional; a forgotten slot is not)"
        )
    print(
        f"  shape     {rows['backgrounds']} background(s), "
        f"{rows['visualizations']} visualization(s), {rows['layers']} layer(s) in the first"
    )
    print(f"  endpoint  {rows['endpoint']}")
    print(
        "\nbind it in Python -- one design, N cases:\n"
        f"    design = compose.load_design({rows['design']!r}, "
        f"slots={{{', '.join(f'{i}: {n!r}' for i, n in sorted(template.slots.items()))}}})\n"
        "    sessions = [design.bind(slide=p, name=Path(p).stem) for p in slides]\n"
        "then point --sessions-dir at the result, or compose directly."
    )
    return 0


def _authoring_given(args: argparse.Namespace) -> str:
    """Which non-manifest door was asked for: `design`, `sessions` or ``.

    Raises on two at once rather than picking one by precedence: `--design` and
    `--sessions-dir` are two different questions ("is my design sound" versus "here
    are the finished sessions") and a build that answered both silently would be
    reporting on one of them.
    """
    design = bool(getattr(args, "design", None))
    sessions = bool(args.sessions_dir or getattr(args, "sessions", None))
    if design and sessions:
        raise UsageError(
            "--design checks one design and binds nothing; --sessions-dir builds "
            "from finished sessions. Run them as two commands."
        )
    if design:
        return "design"
    if sessions:
        return "sessions"
    return ""


def _build(args: argparse.Namespace) -> int:
    if args.check_only and args.publish:
        # Caught here rather than in the library so the run id is never announced
        # first: a log line saying `publish -> run abc` above an error is a line
        # someone reads as "it went".
        print(
            "reportfast: --check-only promises nothing is written and --publish "
            "uploads. They contradict each other; drop one of them.",
            file=sys.stderr,
        )
        return USAGE_ERROR

    if _authoring_given(args) == "design":
        raise UsageError(
            "--design validates a design and binds nothing, so it builds no report. "
            "Run `reportfast plan --design …` to check it, then bind it in Python "
            "(report_fast.compose.expand) and build from --sessions-dir."
        )
    if _authoring_given(args) or args.emit_manifest:
        return _build_composition(args)
    if not args.manifest:
        print(
            "reportfast: build needs a manifest, --sessions-dir DIR or --session "
            "FILE. Pass one.",
            file=sys.stderr,
        )
        return USAGE_ERROR

    from .manifest import build

    if args.publish:
        _announce_publish(args)

    built = build(
        args.manifest,
        out=False if args.check_only else args.out,
        check=not args.no_check,
        publish=args.publish,
        run_id=args.run,
        flow=_flow(args),
        endpoint=_endpoint(args),
    )
    return _report_built(built)


def _build_composition(args: argparse.Namespace) -> int:
    """The manifest-less door: authored sessions in, one HTML file out.

    Publish is reachable from here, and what it uploads is the provenance sidecar
    in the manifest's place -- there is no manifest to log, so the record of what
    resolved *is* the configuration the run keeps. `Composition.build` still cannot
    publish (no parameter, nothing named publish in that module): building a report
    has never implied uploading one, and the door keeps that by calling MLflow
    itself, one step after the file exists.
    """
    _publish_flags(args)

    composition = _composition(args)
    if args.emit_manifest:
        # Printed, never written: an intermediate the tool chose to keep is how an
        # intermediate becomes the contract.
        print(composition.to_manifest())
        return 0

    if args.publish:
        print(f"publish   -> run {args.run} (never implied; this uploads)", file=sys.stderr)

    record = _record(args, composition)
    built = composition.build(
        out=False if args.check_only else (args.out or _default_out(args)),
        check=not args.no_check,
        endpoint=_endpoint(args),
        provenance=record,
    )
    if args.publish:
        built.published = _publish_record(args, built, record)
    return _report_built(built)


def _publish_record(args, built, record):
    """Upload the page plus its record, and nothing else.

    A manifest publish logs three files (declared, resolved, recorded). There is no
    declaration here -- the sessions may never have been saved -- so the run gets
    the record and the page. That is decision 8's "logs the sidecar in the
    manifest's place", and it is why the record has to be generated from resolved
    data rather than from the flags: it is the only account of the build the run
    will ever have.
    """
    from .mlflow import Mlflow
    from .provenance import logged_dir

    flow = _flow(args) or Mlflow.from_env()
    with logged_dir(record) as directory:
        return flow.publish(built.report, run_id=args.run, extra_dir=directory)


def _publish_flags(args: argparse.Namespace) -> None:
    """Check the publish pair before anything is built.

    Both halves are flag errors on this door, not runtime surprises: there is no
    manifest, so there is no `publish:` key to read a run from and no way for
    `--publish` to become right later. Checking after the build would mean a report
    written to disk above an exit code that says "you passed the wrong flags" --
    and a file nobody asked for is the thing this door is built not to leave.
    """
    if args.run and not args.publish:
        raise UsageError(
            "--run names a run and --publish is what writes to it. Publishing is "
            "never implied by naming a destination: add --publish if that is what "
            "you meant."
        )
    if args.publish and not args.run:
        raise UsageError(
            "--publish needs a run: --run RUN. Without a manifest there is no "
            "publish: key to read one from, and inventing a run is not this tool's "
            "call to make."
        )


def _record(args, composition):
    """The sidecar for a door build, from the flags rather than the composition.

    The record's whole use is that someone holding only `report.provenance.json`
    can tell what was run, so it carries the flags as given: `--sessions-dir`,
    the individual `--session` files, the layout. `Composition.build` would
    otherwise fall back to describing the composition, which is true but is not a
    command anyone can retype.
    """
    from .provenance import for_cli

    sessions = composition.sessions()
    return for_cli(
        title=composition.title,
        cases=[session.names[0] if session.names else "case" for session in sessions],
        sessions=sessions,
        endpoint=_endpoint(args),
        design=composition.design,
        sessions_dir=args.sessions_dir,
        session_files=tuple(args.sessions or ()),
        layout=getattr(args, "layout", None),
        grid=(getattr(args, "layout", None) or "grid") == "grid",
    )


def _report_built(built) -> int:
    """One reporting tail for both doors, so the probe cannot be printed by one
    of them and missed by the other."""
    from .verify import summary

    # Where the report actually landed, not where the manifest guessed. A plan
    # that names a file nobody wrote is the kind of near-miss that makes a plan
    # untrustworthy, and this is the same object that gets logged to the run.
    if built.out:
        built.plan.out = built.out
    print(built.plan.to_text())
    print()
    print(f"wrote     {built.out}" if built.out else "wrote     nothing (--check-only)")
    if built.checks:
        verdict = summary(built.checks).splitlines()
        print(f"probe     {verdict[0]}")
        for line in verdict[1:]:
            # The per-ID detail under the totals line, which is what a person
            # scrolls to when the totals line is not all-clear.
            print(f"          {line}")
    if built.published is not None:
        print(f"published {built.published}")

    if built.checks and not built.ok:
        refused = sum(1 for check in built.checks if check.checked and not check.ok)
        print(
            f"\nreportfast: {refused} of {len(built.checks)} DataIDs would not open. "
            "Those links are a black card in the viewer -- fix the source or the "
            "spec and build again.",
            file=sys.stderr,
        )
        return BROKEN_LINKS
    return 0


def _composition(args: argparse.Namespace):
    """The composition the command line describes, gated on the way in.

    `--sessions-dir` and `--session` are the same list, concatenated in the order
    they were given, because the grid's order is the report's order and a folder
    read in an arbitrary order is a report that changes between runs.
    """
    from .compose import Composition, load_session, sessions_from_dir

    endpoint = _endpoint(args)
    sessions = [
        load_session(source, endpoint=endpoint, origin=_origin(source, "--session"))
        for source in args.sessions
    ]
    if args.sessions_dir:
        sessions += sessions_from_dir(args.sessions_dir, endpoint=endpoint)

    return Composition(
        title=args.title or "Report",
        subtitle=args.subtitle,
        blocks=_card_blocks(sessions, layout=getattr(args, "layout", None)),
    )


def _card_blocks(sessions, *, layout=None):
    """One grid, or one card per row. `--layout` is the manifest's `grid:` knob.

    `plan` has no `--layout` -- the layout does not change what a plan reports --
    so the default is taken here rather than from a namespace that may not carry
    one.
    """
    from .compose import DEFAULT_LAYOUT, layout_blocks

    return layout_blocks(sessions, layout or DEFAULT_LAYOUT)


def _slots(args: argparse.Namespace) -> dict:
    """`--slot 0=slide --slot 1=mask` as `{0: "slide", 1: "mask"}`.

    Empty means `load_design`'s own default (`{0: "slide"}`), which is what makes
    the common one-slide design need no flag at all.
    """
    rows = {}
    for item in getattr(args, "slots", None) or ():
        index, sep, name = str(item).partition("=")
        if not sep or not name.strip():
            raise UsageError(
                f"--slot {item!r} wants INDEX=NAME, e.g. --slot 0=slide"
            )
        try:
            rows[int(index)] = name.strip()
        except ValueError as error:
            raise UsageError(
                f"--slot {item!r}: INDEX is the position in data[], so it must be "
                "a number like 0 or 1."
            ) from error
    if not rows:
        return {}
    return rows


def _origin(source, flag: str) -> str:
    """The name a message quotes: a filename if it has one, else the flag."""
    from .compose import _is_path

    return str(source) if _is_path(source) else flag


def _endpoint_row(session) -> str:
    endpoint = session.endpoint
    if endpoint is None:
        return "the environment default"
    return f"{endpoint.base_url} (tiles {endpoint.wsi_base_url})"


def _default_out(args: argparse.Namespace):
    """Where a manifest-less report goes when `-o` is not given."""
    from .compose import DEFAULT_OUT

    return DEFAULT_OUT


def _announce_publish(args: argparse.Namespace) -> None:
    """Name the destination before uploading to it.

    This is not a prompt. A prompt is unreadable in a log and auto-answered by
    anything non-interactive, which is the failure the design calls out: the run
    id has to end up on the record, so it is printed rather than asked. The
    library will still refuse without a target -- this only says the target early.
    """
    from .manifest import publish_target

    try:
        target = args.run or publish_target(args.manifest)
    except Exception as error:  # a broken manifest fails in build(), with better wording
        print(f"publish   to be resolved ({type(error).__name__})", file=sys.stderr)
        return
    if target:
        print(f"publish   -> run {target}", file=sys.stderr)
    else:
        print(
            "publish   no run: publish: in the manifest and no --run; the build "
            "will stop before uploading",
            file=sys.stderr,
        )


def _find(args: argparse.Namespace) -> int:
    from .mlflow import Mlflow

    flow = Mlflow.from_env(tracking_uri=args.tracking_uri)
    if args.slides:
        found = flow.slides(args.run_id, args.path, recursive=args.recursive)
    else:
        found = flow.data_ids(args.run_id, args.path, recursive=args.recursive)

    if args.data_ids:
        print("\n".join(found))
        return 0

    print(f"{len(found)} file(s) under artifacts/{args.path or '.'} of {args.run_id}")
    width = max((len(str(name).rsplit("/", 1)[-1]) for name in found), default=0)
    for data_id in found:
        print(f"  {str(data_id).rsplit('/', 1)[-1]:<{width}}  {data_id}")
    print(f"\nlink      {flow.link(args.run_id)}")
    print(
        "note      this lists a run you named; MLflow is not searchable from here."
    )
    return 0


# ── failure, said once ──────────────────────────────────────────────────────


def _missing_extra(package: str, error: Exception, *, hint: str) -> int:
    """An optional extra is absent. An environment fix, not a manifest bug."""
    print(
        f"reportfast: {package} is not installed and this command needs it.\n"
        f"\n    {hint}\n"
        f"\n{error}",
        file=sys.stderr,
    )
    return MISSING_EXTRA


if __name__ == "__main__":  # pragma: no cover - the entry point
    sys.exit(main())
