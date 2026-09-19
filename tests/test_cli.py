"""The entry point itself: the exit codes, and the absence of everything else.

`skill show` through the CLI is covered in `tests/test_skill.py`, where it sits
beside the module it exercises. What lives here is what only the top level owns:
the table of exit codes, the version line, and the guarantee that the commands
which used to be here stayed deleted.

That last part is the point of the file. `plan`, `build` and `find` were removed
because a report is a script the agent writes, and a CLI that still accepted
`reportfast build report.yaml` -- even to refuse it -- would be a build command
that lies about being one. `skill install` and `skill where` went the same way for
a different reason: putting a skill somewhere is Claude Code's job, not a
library's, so the command line now has one action and it only reads. Argparse
exits on an unknown subcommand through `SystemExit`, which `main` maps, so each
refusal is testable as a code.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from report_fast.__main__ import USAGE_ERROR, main  # noqa: E402


def run(*argv: str) -> int:
    return main(list(argv))


# ── the commands that are not here ─────────────────────────────────────────


@pytest.mark.parametrize("gone", ["plan", "build", "find"])
def test_a_removed_command_is_a_usage_error(capsys, gone):
    code = run(gone, "reports/dysplasia.yaml")
    assert code == USAGE_ERROR
    printed = capsys.readouterr().err
    assert "invalid choice" in printed or "usage:" in printed


def test_the_help_names_the_one_command():
    """No stale door in the help text an agent reads first.

    Checked as a negative because a docstring that mentions `build` in passing is
    fine prose but a stale promise to an agent deciding what to type.
    """
    out = subprocess.run(
        [sys.executable, "-m", "report_fast", "--help"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "skill" in out
    for gone in ("plan ", "build ", "find ", "manifest", "provenance", "publish"):
        assert gone not in out, f"the help still advertises {gone!r}"


def test_the_help_says_why_there_is_no_build():
    out = subprocess.run(
        [sys.executable, "-m", "report_fast", "--help"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    # Whitespace collapsed: argparse wraps the description at the terminal width,
    # so a phrase matched literally passes on a wide terminal and fails in CI.
    flat = " ".join(out.split())
    assert re.search(r"[Bb]uilding a report is not a command", flat), out


# ── the version line ───────────────────────────────────────────────────────


def test_version_prints_the_installed_version_and_exits_zero(capsys):
    assert run("--version") == 0
    printed = capsys.readouterr().out
    assert printed.startswith("report-fast "), printed
    # The viewer half is gone with the derived contract: the release no longer
    # carries a stamp to print, and `--version` must not imply that it does.
    assert "viewer" not in printed, printed


# ── no arguments ───────────────────────────────────────────────────────────


def test_no_arguments_prints_the_help_rather_than_failing_blindly(capsys):
    assert run() == USAGE_ERROR
    assert "usage: reportfast" in capsys.readouterr().out


# ── failure paths the top level owns ───────────────────────────────────────


def test_a_skill_that_is_not_in_the_install_exits_one(tmp_path, capsys, monkeypatch):
    """Not 4: nothing is wrong with the command line, this install is incomplete."""
    from report_fast import skill as skill_module

    def missing(*a, **k):
        raise skill_module.SkillError("no skill bundled in this install")

    monkeypatch.setattr(skill_module, "skill_text", missing)
    assert run("skill", "show") == 1
    assert "no skill bundled" in capsys.readouterr().err


@pytest.mark.parametrize("gone", ["install", "where"])
def test_a_removed_skill_action_is_a_usage_error(capsys, gone):
    """`skill install` went because placing a skill is not a library's job.

    The refusal has to be a code and not a silent fall-through to `show`: an agent
    that typed `skill install` and got SKILL.md on stdout would conclude that
    installing worked.
    """
    code = run("skill", gone)
    assert code == USAGE_ERROR
    printed = capsys.readouterr().err
    assert "invalid choice" in printed or "usage:" in printed


def test_the_cli_writes_nothing_anywhere():
    """The one write this command used to do is gone, and stays gone by grep.

    Checked in source because a permission bug only shows on a machine where the
    destination is not writable -- usually someone else's. A command whose whole
    contract is "print a file" has no business with these.
    """
    source = Path(sys.modules["report_fast.__main__"].__file__).read_text()
    bodies = re.sub(r'""".*?"""|\'\'\'.*?\'\'\'', "", source, flags=re.S)
    for gone in ("shutil", "mkdir", "copytree", "rmtree", "symlink", "open(", "write_text"):
        assert gone not in bodies, f"the CLI touches the filesystem again: {gone}"


def test_a_missing_file_is_a_usage_error(tmp_path, capsys, monkeypatch):
    from report_fast import skill as skill_module

    monkeypatch.setattr(
        skill_module, "reference", lambda name: (_ for _ in ()).throw(FileNotFoundError(name))
    )
    assert run("skill", "show", "--reference", "nope.md") == USAGE_ERROR
    assert "nope.md" in capsys.readouterr().err


def test_the_exit_code_table_in_the_docstring_matches_the_codes():
    """The table is what an agent parses when a command fails quietly.

    A row for a code nothing returns, or a code some path returns with no row, is
    a wrong table -- and a wrong table is worse than none, because it is read
    seriously.
    """
    from report_fast import __main__ as module

    documented = {int(found) for found in re.findall(r"^``(\d)``", module.__doc__ or "", re.M)}
    assert documented == {0, 1, USAGE_ERROR}, documented


def test_the_retired_exit_codes_are_not_returned_anywhere():
    """2 meant "built, and a DataID refused"; 3 meant a missing optional extra.

    Both died with the build command: nothing here writes a report, and nothing
    here needs an extra. Leftover code would be an undocumented verdict, and 3 in
    particular still has a caller-facing meaning (go install something) that this
    CLI can no longer earn.
    """
    source = Path(sys.modules["report_fast.__main__"].__file__).read_text()
    bodies = re.sub(r'""".*?"""|\'\'\'.*?\'\'\'', "", source, flags=re.S)
    assert not re.search(r"return 2\b", bodies), "exit 2 came back"
    assert not re.search(r"return 3\b", bodies), "exit 3 came back"
    assert "MISSING_EXTRA" not in bodies


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
