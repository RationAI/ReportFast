"""``reportfast plan | build | find`` — the three commands, and their three gates.

    reportfast plan  reports/dysplasia.yaml             # writes nothing
    reportfast build reports/dysplasia.yaml             # writes one HTML file
    reportfast build reports/dysplasia.yaml --publish   # the only write to MLflow
    reportfast find  <run-id> --path tile_masks         # list a run's artifacts

Each command's blast radius is the point of the split. ``plan`` resolves every
source and prints what the report would contain, and cannot write: it is the
thing worth reading before a build, and the artifact an agent shows a human.
``build`` writes the HTML and then probes every DataID against the image server,
because a report whose DataIDs were not resolved is a report nobody has checked.
``--publish`` is the only path that touches MLflow, is never implied by a
manifest's ``publish:`` key, and names the run it is going to before it goes.

Exit codes, for the CI job and the agent alike:

===== ==========================================================
``0``  fine
``1``  a manifest error -- a bad key, a missing case, a short layer
``2``  the HTML was written and a DataID did not resolve
``3``  an optional dependency this command needs is not installed
``4``  nothing to work on -- no such manifest, no such run, no command
===== ==========================================================
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

    from .manifest import ManifestError, ManifestNoYaml, ManifestNotFound
    from .mlflow import MlflowError

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


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="reportfast",
        description="Static HTML reports over xOpat v3 sessions, from a manifest.",
        epilog=(
            "A report is a YAML file and this tool reads it. Start with "
            "`reportfast plan <manifest>.yaml` -- it cannot write anything."
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
        help="upload report + manifest + plan; the only write this tool performs",
    )
    build.add_argument(
        "--run",
        default=None,
        metavar="RUN_ID",
        help="publish into this run instead of the manifest's publish:",
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
        help="the report spec: a YAML file describing backgrounds, masks and blocks",
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
    from .manifest import plan

    resolved = plan(args.manifest, flow=_flow(args), endpoint=_endpoint(args))
    print(resolved.to_json() if args.as_json else resolved.to_text())
    return 0


def _build(args: argparse.Namespace) -> int:
    from .manifest import build
    from .verify import summary

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
            "manifest and build again.",
            file=sys.stderr,
        )
        return BROKEN_LINKS
    return 0


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
