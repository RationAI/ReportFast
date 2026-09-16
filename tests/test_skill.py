"""Tests for `report_fast.skill` -- the skill that travels inside the wheel.

The install contract, in one sentence: `uv add report-fast` puts the library in a
project's venv and cannot put the skill anywhere an agent looks (packaging forbids
an install from writing outside its environment), so the skill ships inside the
package and `reportfast skill install` moves it. These tests pin both halves: that
the bundle is found from either layout, and that nothing moves without being asked.

Two things are deliberately *not* done here. Nothing writes to `~/.claude/skills` --
every install goes to a temp `--dest`, because a test that installs into the
developer's own skill directory would change which skills their next session sees.
And the wheel itself is not built and inspected (that is a build-step test, slow and
dependent on the packaging tool); instead the force-include target is asserted
against `pyproject.toml`, so a renamed target fails a test rather than producing an
install where `skill show` lies.

Run:      cd /home/jovyan/report_fast && python tests/test_skill.py
Pytest:   pytest tests/test_skill.py
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import tomllib
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


# ── installing: asked for, one directory, never silently ────────────────────


def test_install_copies_into_the_directory_it_named():
    home = Path(tempfile.mkdtemp())
    try:
        placed = skill_module.install(home)
        assert placed == home / skill_module.SKILL_NAME
        assert (placed / skill_module.SKILL_FILE).is_file()
        assert (placed / "references" / "xopat-v3.md").is_file()
        # A copy, not a pointer at the checkout: an installed library has no
        # checkout, and a skill that reads its references from one would break
        # where it is most used.
        assert not placed.is_symlink()
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_install_does_not_replace_an_edit_without_being_told_to():
    home = Path(tempfile.mkdtemp())
    try:
        skill_module.install(home)
        edited = home / skill_module.SKILL_NAME / "NOTES.md"
        edited.write_text("handwritten", encoding="utf-8")
        try:
            skill_module.install(home)
        except SkillError as error:
            assert "already holds" in str(error) and "--force" in str(error), error
        else:
            raise AssertionError("an existing install was replaced silently")
        assert edited.read_text(encoding="utf-8") == "handwritten"

        skill_module.install(home, force=True)
        assert not edited.exists(), "--force replaces the directory it was given"
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_install_can_link_so_a_checkout_edit_is_live():
    home = Path(tempfile.mkdtemp())
    try:
        placed = skill_module.install(home, link=True)
        assert placed.is_symlink()
        assert placed.resolve() == skill_module.skill_dir().resolve()
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_install_creates_a_parent_that_is_not_there_yet():
    """A fresh machine has no ~/.claude/skills; the first install makes one."""
    home = Path(tempfile.mkdtemp())
    try:
        nested = home / "never" / "made"
        placed = skill_module.install(nested)
        assert (placed / skill_module.SKILL_FILE).is_file()
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_personal_is_the_default_and_project_is_the_other_choice():
    """Default is personal, and the reason is a decision about a repository.

    A project install creates `.claude/skills/`, which then has to be gitignored or
    committed -- which is that repository's business. Personal answers the question
    an agent asks ("how do I build a report?") in every project at once.
    """
    assert skill_module.target_for(None) == skill_module.PERSONAL_DIR / skill_module.SKILL_NAME
    assert skill_module.target_for(None, project=True) == (
        skill_module.PROJECT_DIR / skill_module.SKILL_NAME
    )
    assert skill_module.target_for("/tmp/x") == Path("/tmp/x") / skill_module.SKILL_NAME


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_skill_where_reports_the_bundle_and_an_install():
    home = Path(tempfile.mkdtemp())
    try:
        code, printed, _ = run("skill", "where")
        assert code == 0, printed
        assert "bundled   " in printed and "reportfast" in printed
        assert "installed" in printed

        skill_module.install(home)
        code, printed, _ = run("skill", "where", "--dest", str(home))
        assert code == 0
        assert str(home / "reportfast") in printed
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_skill_show_prints_the_procedure_and_a_reference():
    code, printed, _ = run("skill", "show")
    assert code == 0 and printed.startswith("---\nname: reportfast"), printed[:60]
    code, printed, _ = run("skill", "show", "--reference", "references/deployment.md")
    assert code == 0 and printed.startswith("# Deployment"), printed[:60]


def test_skill_without_an_action_asks_for_one():
    code, _, err = run("skill")
    assert code == 4 and "show, install or where" in err, err


def test_skill_install_through_the_cli_reports_where_it_went():
    home = Path(tempfile.mkdtemp())
    try:
        code, printed, err = run("skill", "install", "--dest", str(home))
        assert code == 0, err
        assert str(home / "reportfast") in printed
        assert "SKILL.md" in printed
        # The restart note: an agent that installs the skill and then expects this
        # session to see it will report that installing does not work.
        assert "restart" in printed.lower() or "/skills" in printed

        code, _, err = run("skill", "install", "--dest", str(home))
        assert code == 4 and "already holds" in err, err
        code, _, err = run("skill", "install", "--dest", str(home), "--force")
        assert code == 0, err
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_reference_that_is_not_there_exits_one():
    """1, not 4: the command line was fine and the thing asked for does not exist."""
    code, _, err = run("skill", "show", "--reference", "references/absent.md")
    assert code == 1 and "the bundle has no" in err, err


# ── the class of bug this whole module exists to kill ───────────────────────


def test_the_skill_tells_an_agent_only_commands_that_work_from_an_install():
    """Every command in SKILL.md must work where the skill gets installed.

    The skill used to say "check it from anywhere: `uv run python -c …`", which is
    true inside this repo and false everywhere `uv add report-fast` was used -- and
    an install is the only reason the sentence matters. So: no command the skill
    tells an agent to run may depend on a checkout. `uv run` and `scripts/derive_schema.py`
    are allowed only when the same line says they are repo steps.
    """
    text = skill_module.skill_text()
    lines = [line.strip() for line in text.splitlines()]

    checkout_only = ("uv run ", "scripts/derive_schema.py")
    # Word-boundary on purpose: `"repo" in line` is satisfied by the word
    # "reportfast", which would let every command line in the file pass the check
    # without saying anything about where it works. "project" counts because
    # `uv run reportfast` is right in any project that installed the library --
    # the complaint is only about a line that names no place at all.
    qualified = re.compile(r"\brepo\b|\bcheckout\b|\bproject\b|never from an install")
    seen = 0
    for line in lines:
        if not any(marker in line for marker in checkout_only):
            continue
        seen += 1
        assert qualified.search(line), f"line needs a command that works from an install: {line}"
    assert seen >= 2, f"expected the repo-only steps to appear and be labelled; saw {seen}"

    # And the positive half: the install-portable spellings are taught.
    assert "python -c" in text, "the stamp check should use plain python"


def test_the_skill_names_examples_by_a_reference_the_code_answers():
    """The prose and the lookup are two files; this is the seam between them."""
    text = skill_module.skill_text()
    for named in ("dysplasia_case.json", "examples/"):
        assert named in text, named
    assert skill_module.reference("examples/dysplasia_case.json").startswith("{")


def test_the_skill_tells_the_truth_about_a_published_build_with_no_out():
    """The paragraph this guards is the one an agent reads at the moment it is
    about to look for a file that was never written.

    Publishing with no `-o` writes nothing locally -- ruled after the leak, and
    enforced in `_door_out`. The skill said nothing about it, so an agent that
    published and then went looking for `report.html` concluded the build had
    failed. Prose about a behaviour the CLI already implements needs a seam test,
    or it drifts the next time the behaviour moves.
    """
    text = skill_module.skill_text()
    publish = text.split("**`--publish` is asked for")[1].split("## ")[0]
    assert "no `-o`" in publish, "the publish paragraph lost the -o rule"
    assert "the run only" in publish, "quote the line the agent will actually see"
    assert "-o FILE" in publish, "say how to ask for the local copy as well"


def test_the_skill_teaches_design_recording_on_the_build():
    """`build --design` belongs in the design flow: an agent holding the design and
    the temp folder is exactly the caller who can record it, and the default --
    `design: null` -- loses the one document that makes the report rebuildable once
    the folder is deleted.

    Scoped to the section that binds a design, not to every `build` line: the
    quick reference above it covers authored sessions that came from nowhere in
    particular, and naming a design there would tell an agent to claim one they do
    not have.
    """
    text = skill_module.skill_text()
    flows = [chunk for chunk in text.split("```bash") if "design.bind(" in chunk]
    assert flows, "the skill lost the bind-in-Python flow"
    build = flows[0].split("```")[0]
    assert "--design" in build, build
    assert "--slot" in build, "the slots are part of the record, so teach them"


if __name__ == "__main__":
    every = [
        (name, made)
        for name, made in sorted(globals().items())
        if name.startswith("test_") and callable(made)
    ]
    failed = 0
    for name, made in every:
        try:
            made()
            print(f"ok   {name}")
        except Exception as error:  # noqa: BLE001 - the runner's whole job
            failed += 1
            print(f"FAIL {name}: {type(error).__name__}: {error}")
    print(f"\n{len(every) - failed}/{len(every)} passed")
    raise SystemExit(1 if failed else 0)
