"""Tests for `report_fast.skill` -- the skill that travels inside the wheel.

The contract, in one sentence: the skill is *bundled*, never installed. `uv add
report-fast` puts the library in a project's venv and the skill inside the wheel,
and `reportfast skill show` reads it from there; a project that wants Claude Code
to see the skill commits a `skills/` directory, which is Claude Code's mechanism
rather than this package's. These tests pin the bundling: that the bundle is found
from either layout, that its files are readable by name, and that a name outside it
is refused.

There used to be an `install()` here, and with it `target_for`, `find_installed`,
the personal/project directories and seven tests -- all deleted, so nothing in this
file writes to a filesystem. The wheel is still not built and inspected (that is a
build-step test, slow and dependent on the packaging tool); instead the force-include
target is asserted against `pyproject.toml`, so a renamed target fails a test rather
than producing an install where `skill show` lies.

Run:      cd /home/jovyan/report_fast && python tests/test_skill.py
Pytest:   pytest tests/test_skill.py
"""

from __future__ import annotations

import ast
import dataclasses
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_fast import skill as skill_module  # noqa: E402
from report_fast.__main__ import main  # noqa: E402
from report_fast.skill import SkillError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def run(*argv) -> tuple:
    """`main(argv)` with output captured; returns ``(code, out, err)``."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


# ── the bundle is there, and found ──────────────────────────────────────────


def test_the_skill_is_bundled_from_a_checkout():
    assert skill_module.skill_dir() == ROOT / "skills" / "reportfast"
    assert (skill_module.skill_dir() / skill_module.SKILL_FILE).is_file()


def test_the_wheel_target_matches_where_the_code_looks():
    """The one place a rename would break an install without breaking a test elsewhere.

    `skill.py` reads `report_fast/skill/`; `pyproject.toml` decides whether anything
    is ever put there. Checked as data in the build config rather than by building a
    wheel, because the failure this prevents is silent: the checkout keeps working,
    the install does not, and only someone in another project finds out.
    """
    # `tomllib` is 3.11+; the package supports 3.10, where this one check cannot
    # run (and `config.py` says so out loud for TOML presets for the same reason).
    # Skipped rather than dropped: the 3.12 leg of CI still runs it, and the
    # failure it prevents -- an install that silently ships no skill -- is silent
    # only on a machine someone else is using.
    try:
        import tomllib
    except ImportError:
        raise unittest.SkipTest("tomllib needs Python 3.11+") from None
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    included = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert included.get("skills/reportfast") == "report_fast/skill", included
    assert included.get("examples") == "report_fast/examples", included


def test_examples_are_bundled_and_named_the_way_the_skill_says():
    directory = skill_module.examples_dir()
    assert directory == ROOT / "examples"
    # The skill tells an agent to imitate these; an empty bundle is the same lie
    # as a missing one.
    assert len(list(directory.glob("*.json"))) >= 3


def test_the_bundled_skill_is_the_one_the_tests_read():
    """One source of truth. A second copy would drift, which is the whole argument
    for force-include instead of a duplicated file."""
    bundled = skill_module.skill_path().read_text(encoding="utf-8")
    assert bundled == (ROOT / "skills" / "reportfast" / "SKILL.md").read_text(encoding="utf-8")


# ── reading the bundle ──────────────────────────────────────────────────────


def test_reference_reads_a_skill_reference():
    text = skill_module.reference("references/xopat-v3.md")
    assert len(text) > 200 and "data" in text.lower()


def test_reference_accepts_an_example_both_ways_it_is_written():
    """`examples/x.json` as the skill prints it, and the bare filename."""
    prefixed = skill_module.reference("examples/dysplasia_case.json")
    bare = skill_module.reference("dysplasia_case.json")
    assert prefixed == bare and prefixed.lstrip().startswith("{")


def test_a_prefixed_name_cannot_reach_the_other_root():
    """`examples/SKILL.md` is not the skill's SKILL.md.

    The bug this pins: stripping a prefix and then searching every root would
    resolve it to a file the caller did not name, which in an agent's hands is a
    session file that turns out to be a procedure.
    """
    for escaped in ("examples/SKILL.md", "references/SKILL.md"):
        try:
            skill_module.reference(escaped)
        except SkillError as error:
            assert "no " in str(error), error
        else:
            raise AssertionError(f"{escaped} resolved to something it should not")


def test_reference_refuses_to_escape_the_bundle():
    for name in ("../pyproject.toml", "/etc/passwd", "examples/../../pyproject.toml"):
        try:
            skill_module.reference(name)
        except SkillError:
            pass
        else:
            raise AssertionError(f"{name} was read from outside the bundle")


def test_a_missing_reference_lists_what_there_is():
    """The caller is an agent that guessed a filename; the refusal is its only hint."""
    try:
        skill_module.reference("references/nope.md")
    except SkillError as error:
        assert "xopat-v3.md" in str(error), error
    else:
        raise AssertionError("a missing file raised nothing")


# ── nothing here installs ───────────────────────────────────────────────────
#
# There used to be five tests for `install()` and two for `skill where`, and they
# were deleted rather than rewritten: Claude Code takes a skill from a committed
# `skills/` directory or as a plugin, so a library that copies one into
# `~/.claude/skills` was reimplementing someone else's mechanism badly. What is
# checked instead is the property that made the deletion safe -- the module has no
# write path left in it.


def test_the_module_has_no_way_to_write():
    """`install` is gone from the API, not merely from the CLI.

    Checked against `__all__` and the source rather than against prose: the failure
    this prevents is the function coming back under the same name because a caller
    asked for it, which would put a home-directory write back into a library.
    """
    gone = {"install", "target_for", "find_installed", "PERSONAL_DIR", "PROJECT_DIR"}
    exported = set(skill_module.__all__)
    assert not gone & exported, f"the installer is exported again: {sorted(gone & exported)}"
    assert not gone & {n for n in dir(skill_module) if not n.startswith("_")}, (
        "an installer function is back in report_fast.skill"
    )
    source = (ROOT / "report_fast" / "skill.py").read_text(encoding="utf-8")
    bodies = re.sub(r'""".*?"""', "", source, flags=re.S)
    for write in ("shutil", "copytree", "rmtree", "symlink", "mkdir", "write_text"):
        assert write not in bodies, f"skill.py touches the filesystem again: {write}"


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_skill_show_prints_the_procedure_and_a_reference():
    code, printed, _ = run("skill", "show")
    assert code == 0 and printed.startswith("---\nname: reportfast"), printed[:60]
    code, printed, _ = run("skill", "show", "--reference", "references/deployment.md")
    assert code == 0 and printed.startswith("# Deployment"), printed[:60]


def test_skill_without_an_action_asks_for_one():
    """The refusal names the one action there is, and not the two there were."""
    code, _, err = run("skill")
    assert code == 4 and "needs an action: show" in err, err
    assert "install" not in err, err


def test_show_writes_nothing():
    """`show` to stdout is the whole command: run it and check the tree is unchanged.

    The point of the deletion was that this package stops putting files in places
    its users did not choose, so the guarantee is checked by watching rather than by
    reading: a snapshot of the bundle before and after, entry for entry.
    """
    bundle = skill_module.skill_dir()
    def snapshot() -> list:
        return sorted(
            (str(item.relative_to(bundle)), item.stat().st_mtime_ns)
            for item in bundle.rglob("*")
            if item.is_file()
        )

    before = snapshot()
    assert run("skill", "show")[0] == 0
    assert run("skill", "show", "--reference", "references/xopat-v3.md")[0] == 0
    assert snapshot() == before, "printing the skill changed the bundle"


def test_a_reference_that_is_not_there_exits_one():
    """1, not 4: the command line was fine and the thing asked for does not exist."""
    code, _, err = run("skill", "show", "--reference", "references/absent.md")
    assert code == 1 and "the bundle has no" in err, err


# ── the class of bug this whole module exists to kill ───────────────────────


def test_the_skill_names_examples_by_a_reference_the_code_answers():
    """The prose and the lookup are two files; this is the seam between them."""
    text = skill_module.skill_text()
    for named in ("dysplasia_case.json", "examples/"):
        assert named in text, named
    assert skill_module.reference("examples/dysplasia_case.json").startswith("{")


# ── the skill's API claims, checked against the package ─────────────────────
#
# These replaced three tests that split SKILL.md on literal sentences and checked
# what fell out. Those could only fail on a rename, which is why three of them
# were still asserting a `build --publish`/`--design` CLI that had been deleted:
# the strings they hunted survived the thing they described. What is checked here
# instead is a *claim* -- that a name the skill tells someone to reach for exists
# -- so it fails when the claim goes false and not when the prose is reworded.

#: Backticked names that match `API_SHAPE` and are exempt from `__all__`, each
#: with the reason. Kept short on purpose: the pattern reads only PascalCase and
#: snake_case, so a session field (`params`, `sessionName`), a dotted path
#: (`params.ui`), a filename or an English word never lands here. Every entry is
#: checked below, so one that stops being used fails a test.
NOT_LIBRARY_API = {
    # Named in the one paragraph explaining that they were removed.
    "Prose": "listed as deleted",
    "MetricTable": "listed as deleted",
    "Chart": "listed as deleted",
    "RawHtml": "listed as deleted",
    # A keyword argument of an exported class, not a name you import.
    "mount_root": "endpoint field",
    # An environment fact the skill tells someone to check for.
    "PATH": "the shell's",
}

#: Matches a backticked name a reader could believe is this package's API: a
#: class shape, or a snake_case identifier. Deliberately not everything -- a
#: dotted session path (`params.ui`) and a sentence fragment should not need
#: exempting.
API_SHAPE = re.compile(r"^[A-Z][A-Za-z0-9]*$|^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")


def _prose(text: str) -> str:
    """SKILL.md without its fenced blocks: what the file *asserts*, not what it runs."""
    return re.sub(r"```.*?```", "", text, flags=re.S)


def test_every_api_name_the_skill_prose_names_is_public():
    """A name in prose is an instruction, and an invented one is followed.

    Writing these docs produced three names that did not exist and surfaced one
    that did but was unreachable from the package root; none of the 174 passing
    tests could see either. This one can, in both directions: a name that never
    existed, and a name that stops existing.
    """
    import report_fast

    unreachable = [name for name in NOT_LIBRARY_API if not API_SHAPE.match(name)]
    assert not unreachable, f"NOT_LIBRARY_API exempts names the pattern cannot find: {unreachable}"

    published = set(report_fast.__all__)
    named = {
        token
        for token in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", _prose(skill_module.skill_text()))
        if API_SHAPE.match(token)
    }
    invented = sorted(named - published - set(NOT_LIBRARY_API))
    assert not invented, f"SKILL.md names as available, but it is not: {invented}"
    # An exemption left behind is a name the prose no longer says, which makes the
    # list above a claim about something that is not there.
    unused = sorted(set(NOT_LIBRARY_API) - named)
    assert not unused, f"NOT_LIBRARY_API exempts names the skill no longer says: {unused}"
    # And the guard must still be refusing something, or it is checking nothing.
    assert set(NOT_LIBRARY_API) & named, "the guard exempts nothing any more"


def test_the_deleted_components_are_named_only_where_they_are_named_as_deleted():
    """`Prose`, `MetricTable`, `Chart`, `RawHtml` may appear in exactly one place.

    That paragraph exists to stop someone reinventing them, so it has to name
    them. Anywhere else, the same backticks read as "this is available" -- which
    is how a deleted component comes back: not by being re-added, but by being
    suggested in prose an agent follows.
    """
    deleted = ("Prose", "MetricTable", "Chart", "RawHtml")
    text = skill_module.skill_text()
    marker = "There used to be ten components"
    start = text.index(marker)
    paragraph = text[start : text.index("\n\n", start)]
    elsewhere = {
        name: text.count(f"`{name}`") - paragraph.count(f"`{name}`")
        for name in deleted
        if text.count(f"`{name}`") != paragraph.count(f"`{name}`")
    }
    assert not elsewhere, f"named outside the paragraph that deletes them: {elsewhere}"
    assert all(name in paragraph for name in deleted), (
        "the paragraph naming what was removed is what stops it being reinvented"
    )


def test_the_page_arguments_the_skill_lists_are_the_real_ones():
    """`Report` takes `title`, `subtitle`, ... -- and that list is a claim.

    Field names are the one part of the prose an agent types into a constructor,
    so it is checked against the dataclass rather than against a copy: adding a
    field to `Report` and not saying so, or naming one that was renamed, fails
    here rather than in someone's script.
    """
    from report_fast import Report

    text = skill_module.skill_text()
    start = text.index("`Report` takes")
    sentence = text[start : text.index("\n", start)]
    listed = set(re.findall(r"`([a-z_]+)`", sentence))
    fields = {field.name for field in dataclasses.fields(Report)}
    assert listed, f"nothing was listed as an argument in: {sentence}"
    wrong = sorted(listed - fields)
    assert not wrong, f"the skill says Report takes {wrong}, which it does not: {sentence}"
    assert "preamble" in listed, (
        "preamble is the page's only prose besides the title and subtitle; teach it"
    )


def _bundled_docs() -> dict:
    """Every markdown file the skill ships, by name -- SKILL.md and the references."""
    found = {"SKILL.md": skill_module.skill_dir() / "SKILL.md"}
    found.update(
        {
            f"references/{path.name}": path
            for path in sorted((skill_module.skill_dir() / "references").glob("*.md"))
        }
    )
    return {name: path.read_text(encoding="utf-8") for name, path in found.items()}


def test_every_python_snippet_in_the_bundle_is_valid_and_imports_only_public_names():
    """Every snippet in SKILL.md *and the references* parses, and imports only exports.

    Snippets are the most-copied text in the bundle, which makes them the most
    damaging place for a typo or a name that was never exported. Checking the
    references too costs nothing and needs no exemption list at all: a name in a
    fenced `from report_fast import …` is unambiguously meant to be importable.

    Parsing rather than running is the deliberate limit -- running would need a
    slide folder that exists, and three snippets were executed by hand while these
    files were written. What this catches for free is the case a hand run does not:
    a snippet that was correct once and drifted from a rename.
    """
    import report_fast

    published = set(report_fast.__all__)
    total = 0
    for name, text in _bundled_docs().items():
        for number, block in enumerate(re.findall(r"```python\n(.*?)```", text, flags=re.S), 1):
            total += 1
            where = f"{name} block {number}"
            tree = ast.parse(block, filename=where, mode="exec")  # SyntaxError is the failure
            imported = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("report_fast")
                for alias in node.names
            }
            unknown = sorted(imported - published)
            assert not unknown, f"{where} imports {unknown}, which is not exported"
    assert total >= 6, f"the bundle should carry its snippets; found {total}"


if __name__ == "__main__":
    every = [
        (name, made)
        for name, made in sorted(globals().items())
        if name.startswith("test_") and callable(made)
    ]
    failed = 0
    skipped = 0
    for name, made in every:
        try:
            made()
            print(f"ok   {name}")
        except unittest.SkipTest as skip:
            # A skip is not a failure and must not print as one: this file runs
            # standalone on 3.10, where the tomllib check cannot run at all, and a
            # red line for something the interpreter cannot do teaches people to
            # ignore the red lines.
            skipped += 1
            print(f"skip {name}: {skip}")
        except Exception as error:  # noqa: BLE001 - the runner's whole job
            failed += 1
            print(f"FAIL {name}: {type(error).__name__}: {error}")
    ran = len(every) - skipped
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran - failed}/{ran} passed{tail}")
    raise SystemExit(1 if failed else 0)
