"""``reportfast`` — the one command, and the reason there is only one.

    reportfast skill show                        # print SKILL.md
    reportfast skill show --reference NAME       # print one file from the bundle

There is no ``build``. A report is requested in agent chat, and what answers it is
a Python script the agent writes for that folder — list the slides, bind the
masks, one card per case, write the page. That script *is* the record of the
report: it names the folders, the colours, the case order and the layout, and
unlike a spec file it can be re-run. A ``build`` command would be a second, worse
way to write the same loop, needing a case-list file on disk to do it.

There is no ``install`` either, and that one is a recent deletion. ``skill
install`` copied the bundle into ``~/.claude/skills``; Claude Code takes a skill
from a committed ``skills/`` directory or as a plugin, and a library writing into
a home directory is neither. So this command only reads: the skill is in the
checkout for anyone who has one, and inside the wheel for anyone who does not
(:mod:`report_fast.skill`).

Exit codes, for the CI job and the agent alike:

===== ==========================================================
``0``  fine
``1``  this install is broken -- the skill is not in it, or a named file is not in the bundle
``4``  usage: a bad flag, no action given
===== ==========================================================

1 and 4 stay different because the fixes are in different places: 1 means this
installation, 4 means that command line. Nothing here writes, so there is no
code for a write that failed.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

__all__ = ["main"]

#: Bad command line. Different from 1, which says something is missing from
#: *this installation* rather than from the call.
USAGE_ERROR = 4


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the CLI. Returns the process exit code; never raises on bad input."""
    parser = _parser()
    arguments = list(sys.argv[1:] if argv is None else argv)

    from .skill import SkillError

    try:
        args = parser.parse_args(arguments)
        if getattr(args, "func", None) is None:
            parser.print_help()
            return USAGE_ERROR
        return int(args.func(args))
    except _Usage:
        # argparse already printed the usage and the reason.
        return USAGE_ERROR
    except SystemExit as exit_:  # `--help`, `--version`: argparse prints and quits
        return int(exit_.code or 0)
    except SkillError as error:
        # Every SkillError this command can raise means the same thing: the file
        # asked for is not in this install. That is a build or an install problem,
        # never a command-line one, so it is 1 and not 4.
        print(f"reportfast: {error}", file=sys.stderr)
        return 1
    except (FileNotFoundError, NotADirectoryError) as error:
        print(f"reportfast: {error}", file=sys.stderr)
        return USAGE_ERROR


# ── the parser ──────────────────────────────────────────────────────────────


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that exits 4 on a usage error, not argparse's 2.

    argparse's own 2 is not in this tool's table, and an undocumented code is
    worse than a duplicated one: an agent reading 2 cannot tell whether anything
    it cares about failed.
    """

    def error(self, message: str):  # pragma: no cover - argparse prints the usage
        self.print_usage(sys.stderr)
        print(f"reportfast: error: {message}", file=sys.stderr)
        raise _Usage(message)


class _Usage(Exception):
    """Bad command line. Carried as an exception so `main` owns the exit code."""


class _VersionAction(argparse.Action):
    """Print the installed version and exit.

    Read from the package metadata rather than a constant, so the number cannot
    disagree with the wheel that is actually installed. The version of the *viewer*
    this release was written against is a line in the skill, not a flag here: the
    library no longer carries a derived contract to print a stamp from.
    """

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: D102
        from importlib.metadata import PackageNotFoundError, version

        try:
            print(f"report-fast {version('report-fast')}")
        except PackageNotFoundError:  # a checkout, never installed
            print("report-fast 0+unknown (this checkout is not installed)")
        raise SystemExit(0)


SKILL_HELP = """The procedure this version of the tool ships, printed to stdout.

`uv add report-fast` puts the library in a project's venv and the skill inside the
wheel, where `skill show` can read it back. Nothing is installed and nothing is
written: a project that wants Claude Code to see the skill commits a `skills/`
directory, and Claude Code has its own way of installing one. `show` is for the
reader who has an install and no checkout."""


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="reportfast",
        description=(
            "Print the agent procedure that ships with this package. Building a "
            "report is not a command here: the agent writes the script that does "
            "it, working from the skill this command prints."
        ),
    )
    parser.add_argument(
        "--version",
        action=_VersionAction,
        nargs=0,
        help="print the installed version and exit",
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    skill = commands.add_parser(
        "skill", help="the agent procedure this install ships", description=SKILL_HELP
    )
    skill_actions = skill.add_subparsers(dest="skill_action", metavar="<action>")

    show = skill_actions.add_parser("show", help="print SKILL.md")
    show.add_argument(
        "--reference",
        default=None,
        metavar="NAME",
        help="print one file from references/ instead of SKILL.md",
    )
    show.set_defaults(func=_skill)
    skill.set_defaults(func=_skill)
    return parser


def _skill(args: argparse.Namespace) -> int:
    """`reportfast skill show` — read the bundle, print it, change nothing."""
    from . import skill as skill_module

    if getattr(args, "skill_action", None) != "show":
        print("reportfast: skill needs an action: show.", file=sys.stderr)
        return USAGE_ERROR

    if getattr(args, "reference", None):
        print(skill_module.reference(args.reference), end="")
    else:
        print(skill_module.skill_text(), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    sys.exit(main())
