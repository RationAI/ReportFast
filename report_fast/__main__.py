"""``reportfast skill`` — the one command, and the reason there is only one.

    reportfast skill install                  # into ~/.claude/skills
    reportfast skill install --project        # into ./.claude/skills
    reportfast skill show                     # print SKILL.md, install nothing
    reportfast skill where                    # is it installed, and where

There is no ``build``. A report is requested in agent chat, and what answers it is
a Python script the agent writes for that folder — list the slides, bind the
masks, one card per case, write the page. That script *is* the record of the
report: it names the folders, the colours, the case order and the layout, and
unlike a spec file it can be re-run. A ``build`` command would be a second, worse
way to write the same loop, needing a case-list file on disk to do it.

What remains is the one thing a library cannot do by itself. ``uv add
report-fast`` puts Python in a project's environment and cannot put the skill
where an agent reads it, because installing is not allowed to write outside the
environment (:mod:`report_fast.skill`). So the skill travels inside the package
and this command places it, when asked.

Exit codes, for the CI job and the agent alike:

===== ==========================================================
``0``  fine
``1``  this install is broken -- the skill is not in it, or a session file is wrong
``4``  usage: a bad flag, no such directory, a skill already installed without --force
===== ==========================================================

1 and 4 stay different because the fixes are in different places: 1 means this
installation, 4 means that command line. A write that failed on permissions is 4
with a suggestion, since naming a writable ``--dest`` is the fix.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

__all__ = ["main"]

#: Bad command line, or a destination that is not usable. Different from 1, which
#: says something is missing from *this installation* rather than from the call.
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
        # The skill is either not in this install (a build problem, 1) or already
        # installed where it was asked to go (a flag problem, 4). The message says
        # which, so the exit code is not the only clue.
        print(f"reportfast: {error}", file=sys.stderr)
        return USAGE_ERROR if "already holds" in str(error) else 1
    except PermissionError as error:
        # `skill install` writes into a directory the caller may not own -- ~/.claude
        # on a shared machine, or a project's .claude checked out from someone else.
        print(
            f"reportfast: cannot write {error.filename}: permission denied. Name a "
            "directory you can write with --dest DIR.",
            file=sys.stderr,
        )
        return USAGE_ERROR
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


SKILL_HELP = """The procedure this version of the tool ships, and where an agent finds it.

`uv add report-fast` puts the library in a project's venv; it cannot put the skill in
a place Claude Code reads, because installing is not allowed to write outside the
environment it is installing into. So the skill travels inside the package and this
command places it: `reportfast skill install` once per machine, `--project` once per
repository. `show` prints it without installing anything."""


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="reportfast",
        description=(
            "Place the agent procedure that ships with this package. Building a "
            "report is not a command here: the agent writes the script that does "
            "it, working from the skill this command installs."
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

    place = skill_actions.add_parser(
        "install", help="put the skill where Claude Code looks for it"
    )
    place.add_argument(
        "--project",
        action="store_true",
        help="install into ./.claude/skills for this project only, instead of ~/.claude/skills",
    )
    place.add_argument(
        "--dest",
        default=None,
        metavar="DIR",
        help="install under DIR instead of either default",
    )
    place.add_argument(
        "--force",
        action="store_true",
        help="replace a skill that is already installed there",
    )
    place.add_argument(
        "--link",
        action="store_true",
        help="symlink to the installed files, so a checkout edit is live at once",
    )
    place.set_defaults(func=_skill)

    where = skill_actions.add_parser(
        "where", help="say where the skill is and whether it is installed"
    )
    where.add_argument(
        "--dest",
        default=None,
        metavar="DIR",
        help="ask about DIR rather than the two default locations",
    )
    where.add_argument(
        "--project",
        action="store_true",
        help="ask about ./.claude/skills only",
    )
    where.set_defaults(func=_skill)
    skill.set_defaults(func=_skill)
    return parser


def _skill(args: argparse.Namespace) -> int:
    """`reportfast skill show | install | where`.

    Read, write, and ask. `show` never touches the filesystem outside the package,
    `install` writes exactly one directory and says so, and `where` is the question
    asked after an agent did *not* use the skill.
    """
    from . import skill as skill_module

    action = getattr(args, "skill_action", None)
    if action is None:
        print("reportfast: skill needs an action: show, install or where.", file=sys.stderr)
        return USAGE_ERROR

    if action == "show":
        if getattr(args, "reference", None):
            print(skill_module.reference(args.reference), end="")
        else:
            print(skill_module.skill_text(), end="")
        return 0

    if action == "where":
        print(f"bundled   {skill_module.skill_dir()}")
        dest = getattr(args, "dest", None)
        installed = skill_module.find_installed(dest, project=getattr(args, "project", False))
        project_target = skill_module.target_for(None, project=True)
        if not installed:
            asked = skill_module.target_for(dest, project=getattr(args, "project", False))
            print(
                f"installed nothing at {asked}. "
                "`reportfast skill install` puts the skill where Claude Code looks."
            )
            return 0
        for path in installed:
            scope = "this project" if path == project_target else "every project"
            if dest:
                scope = "as asked"
            print(f"installed {path}  ({scope})")
        return 0

    placed = skill_module.install(
        args.dest, project=args.project, force=args.force, link=args.link
    )
    scope = args.dest or ("this project only" if args.project else "every project")
    print(f"skill     {placed}  ({scope}, {'linked' if args.link else 'copied'})")
    print(f"read      {placed / skill_module.SKILL_FILE}")
    print("note      restart the session or run /skills for Claude Code to see it.")
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    sys.exit(main())
