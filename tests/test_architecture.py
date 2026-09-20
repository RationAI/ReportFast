"""The shape of the library, asserted rather than remembered.

The direction is a small library that builds sessions and an agent that reads
the viewer to decide what a session may contain. That direction is only kept by
a check: the natural reflex when a report renders wrongly is to add a registry
of what the viewer supports, and three of them already lived here and were
deleted (a shader-type enum, a `params` allowlist, and a generated JSON Schema
derived from the viewer's source). Each was a second source of truth about a
deployment this library does not run, and each went stale in a release.

So these tests grep. They are not about style: every banned shape below is a
file that existed, and a fact that would have needed re-deriving by hand.

Run standalone: ``uv run python tests/test_architecture.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "report_fast"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

#: Where a shader type name may be written. `layer.py` builds the two layer
#: shapes the library cares about; `xopat.py` passes whatever it is handed
#: straight through, and names no type of its own.
LAYER_VOCABULARY = {"layer.py"}

#: A viewer layer type. Only the ones this library has ever hard-coded, so the
#: list is a tripwire on the past, not a claim about the viewer today.
#: `background` is deliberately absent: it is both a layer type and the name of
#: the session's top-level `background[]`, and the grep cannot tell them apart.
SHADER_TYPES = (
    "heatmap",
    "colormap",
    "classify",
    "segmentation",
    "bounding_box",
    "iconmap",
    "sobel",
)

#: Imports that would mean the library grew a configuration layer, a schema, or
#: a second document format of its own.
BANNED_IMPORTS = {
    "yaml": "no manifest: a report is a script the agent writes",
    "jsonschema": "no schema: the agent reads the viewer's types",
    "pydantic": "no model of the session: it is passed through as written",
    "PIL": "no imaging: a thumbnail is a tile-server URL",
    "skimage": "no imaging: a mask is a path, not an array",
    "cv2": "no imaging: a mask is a path, not an array",
    "requests": "httpx comes in through fasthtml; nothing else needs it",
}


def sources():
    return sorted(p for p in PACKAGE.rglob("*.py"))


def code_only(text: str) -> str:
    """`text` with docstrings and comments blanked, for import-shaped greps."""
    without_docstrings = re.sub(
        r'("""|\'\'\')(?:(?!\1).)*?\1', '""', text, flags=re.S
    )
    return "\n".join(
        line.split("#", 1)[0] for line in without_docstrings.splitlines()
    )


def test_only_layer_py_names_a_shader_type():
    """A type the viewer registers is the viewer's fact, not this library's.

    A registry here is a copy that goes stale when the viewer adds or retires a
    type, and the stale copy is worse than none: it rejects a session the
    current viewer would render.
    """
    offenders = {}
    for path in sources():
        if path.name in LAYER_VOCABULARY:
            continue
        hits = [
            name
            for name in SHADER_TYPES
            if re.search(rf'["\']{name}["\']', code_only(path.read_text()))
        ]
        if hits:
            offenders[path.name] = sorted(set(hits))
    assert not offenders, (
        f"these files name viewer shader types: {offenders}. Say it to the "
        "agent in the skill instead; the viewer's `flex-renderer.js` is the "
        "list, and it moves."
    )


def test_the_library_reads_no_config_files():
    """Nothing is configured from a file: endpoints come from the environment.

    A YAML layer means a schema for the YAML, a loader, a validation error
    format, and a second way to say what a Python script says directly.
    """
    offenders = {}
    for path in sources():
        body = code_only(path.read_text())
        hits = [
            banned
            for banned in BANNED_IMPORTS
            if re.search(rf"^\s*(?:import|from)\s+{banned}\b", body, re.M)
        ]
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"unexpected dependencies: {offenders}"


def test_there_is_no_copy_of_the_viewer_vocabulary():
    """No generated data files, no allowlists, no registry of viewer settings.

    `schema/` was 2,400 generated lines with one reader, and its own tests
    existed only to catch the generator drifting. The agent has the viewer
    checkout; a Python copy of the same facts had to be re-derived forever.
    """
    assert not (PACKAGE / "schema").exists(), "schema/ was derived, not authored"
    assert not (PACKAGE / "contract.py").exists()
    assert not (PACKAGE / "audit.py").exists(), (
        "the only surviving check is `_check_references` in session.py, which "
        "reads the document's own lengths and no viewer vocabulary"
    )
    for path in sources():
        body = code_only(path.read_text())
        assert "PARAM_KEYS" not in body, (
            "an allowlist of session params is a claim about the deployment"
        )
        assert "SHADER_TYPES" not in body


def test_no_module_is_named_for_a_format_that_no_longer_exists():
    """The deleted organs, listed so their names do not come back one at a time."""
    gone = (
        "config.py",
        "manifest.py",
        "provenance.py",
        "compose.py",
        "build.py",
        "verify.py",
        "frozen.py",
        "shader.py",
        "components/chart.py",
        "components/prose.py",
        "components/metrics.py",
    )
    present = sorted(name for name in gone if (PACKAGE / name).exists())
    assert not present, f"{present} were deleted on purpose"


def test_the_component_set_is_the_two_it_is_supposed_to_be():
    """Two components, because a page made of more than two can drift.

    The set was ten. Each one was a way for a report written in March and one
    written in September to look like different products, and each was reachable
    by an agent that had a slightly different idea about what a caption needed.
    What the page needs beyond slides it carries itself: `Report`'s subtitle and
    preamble.

    This asserts the exported set, not the files on disk, because the promise is
    about what someone can `import`. A third component is not forbidden forever,
    but it has to arrive here knowingly.
    """
    import report_fast

    # `BaseComponent` is the interface, not a thing anyone renders; `Section` is
    # page furniture beside `Report` -- it groups blocks and shows a title, and
    # offers no way to say content, so it cannot be a second vocabulary.
    structural = {"BaseComponent", "Section"}
    components = {
        name
        for name in report_fast.__all__
        if getattr(getattr(report_fast, name), "component_type", None) is not None
        and name not in structural
    }
    assert components == {"SlideCard", "SlideGrid"}, (
        f"the exported components are {sorted(components)}; if that is right, "
        "say why here -- a component is a way for two reports to stop looking "
        "like the same report"
    )


def test_a_component_is_reached_by_importing_it():
    """No name -> class map, because nothing can carry the name.

    `ComponentRegistry` mapped `"slide-card"` to a class so that components could
    be *created from a config file* -- its own docstring said so. The config file
    went first, and `test_the_library_reads_no_config_files` keeps it gone, which
    left the registry as the input side of a deleted format: a lookup no caller
    has, whose only reader was the test that tested the lookup.

    Checked as a capability rather than as a class name, because the reflex this
    forbids is reasonable-looking -- "let the script pick a component by name" --
    and would arrive with a different class name. Anything exported that turns a
    string into a component is a second door into a two-component set, which is
    what `test_the_component_set_is_the_two_it_is_supposed_to_be` exists to keep
    closed.
    """
    import inspect

    import report_fast

    assert "ComponentRegistry" not in report_fast.__all__

    for name in report_fast.__all__:
        owner = getattr(report_fast, name)
        if not inspect.isclass(owner):
            continue
        # `getmembers(owner, callable)` and not `vars` + `callable`: a class's
        # `vars` hold the raw `classmethod` object, and `callable()` is False for
        # that -- the first version of this check read an actual resolver's method
        # set as empty and passed on the thing it forbids. `getmembers` goes
        # through `getattr`, which binds the descriptor into a method.
        methods = {attribute for attribute, _ in inspect.getmembers(owner, callable)}
        assert not {"register", "create"} <= methods, (
            f"{name} resolves components by name; a report names them by importing them"
        )


def test_no_public_name_escapes_html():
    """Nothing in the public surface takes markup and passes it through.

    `RawHtml` was that door and it went with the rest: the component set is what
    makes reports consistent, and one block that accepts arbitrary markup makes
    the set advisory. `Report.add` still takes a FastHTML tree, which is the
    documented extension path and is listed as an accepted exception rather than
    quietly left out -- see `test_a_raw_fasthtml_can_be_a_block` in
    test_components.py, which is where that path is pinned.
    """
    import inspect

    import report_fast

    for name in report_fast.__all__:
        owner = getattr(report_fast, name)
        if not inspect.isclass(owner):
            continue
        signature = str(inspect.signature(owner.__init__))
        assert "html" not in signature.replace("html5", ""), (
            f"{name} takes markup named 'html'"
        )


#: A workflow line that would actually upload. Comments are stripped before this
#: is applied, because the workflow that must not publish is the one that spends
#: five lines explaining why it must not publish.
PUBLISH_STEP = re.compile(r"log_artifact|\bpublish|--upload|\baws\b.*cp|\bs3://", re.I)


def test_no_workflow_publishes():
    """Publishing is never a CI side effect, and this is where that is enforced.

    `.github/workflows/ci.yml` says so in a comment, and a comment is not a check.
    The rule it states -- publishing is never implied by a manifest key, a CI job,
    or a prompt -- is the one promise in this project that cannot be recovered from
    after the fact: an artifact logged to someone's run is in the record, and there
    is no clean way to take it back out.

    So this greps the workflows, not the Python. A `publish` step is what a
    reasonable person adds while trying to be helpful -- "build the report and put
    it where the run is" -- and it would pass review, pass tests, and upload.
    """
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "the guard is pointless with no workflow to read"
    offenders = {}
    for path in workflows:
        lines = path.read_text(encoding="utf-8").splitlines()
        hits = [
            f"{path.name}:{number}: {line.strip()}"
            for number, line in enumerate(lines, 1)
            # `#` is the whole first token only: `url#fragment` is a line to check.
            if not line.lstrip().startswith("#")
            and PUBLISH_STEP.search(line)
        ]
        if hits:
            offenders[path.name] = hits
    assert not offenders, (
        f"a workflow appears to publish: {offenders}. Publishing happens when a "
        "person asks for `Mlflow.publish()`; a pipeline has nobody to ask"
    )


def test_every_rule_ci_claims_in_a_comment_is_a_test_that_exists():
    """CI's comment cites a test by name; that name has to resolve.

    It cited `test_no_workflow_publishes` for a while after that test was deleted
    with the CLI it belonged to, which is worse than no comment: the next reader
    reasonably does not go looking for the check themselves.
    """
    cited = set()
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        cited.update(re.findall(r"\btest_[a-z0-9_]+", path.read_text(encoding="utf-8")))
    body = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "tests").glob("*.py"))
    missing = sorted(name for name in cited if f"def {name}" not in body)
    assert not missing, f"CI cites tests that do not exist: {missing}"


def test_every_test_file_can_be_run_the_way_it_says_to():
    """Every test file runs standalone, because a file that cannot is a file that lies.

    `test_xopat.py` carried `Run: python tests/test_xopat.py` from the day it was
    written and had no `__main__` block, so that command imported the module and
    exited 0 having called none of its 22 tests. pytest collected the file fine, so
    CI stayed green and the claim stayed false; what noticed was an edit to that
    file with no way to check the edit. An unrun test and a passing test look
    identical, so the shape is what gets checked, and it is checked on every file
    rather than only on the ones whose docstring promises it -- the promise being
    the part that was wrong.
    """
    files = sorted((ROOT / "tests").glob("test_*.py"))
    assert len(files) >= 9, f"the glob found {len(files)} test files; it should not be this small"
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        # Either shape is a runner: dispatch over the `test_` prefix, or hand the
        # file to pytest. What is not a runner is a `__main__` that calls nothing,
        # and what is not acceptable is a list of test names that silently stops
        # listing the tests added after it.
        dispatches = "__main__" in text and (
            'startswith("test_")' in text or "pytest.main" in text
        )
        declared = len(re.findall(r"^def test_", text, flags=re.M))
        if not dispatches:
            offenders.append(f"{path.name}: no runner")
        elif declared == 0:
            offenders.append(f"{path.name}: a runner and no tests to run")
    assert not offenders, f"unrunnable test files: {offenders}"


def test_the_package_imports_without_the_optional_extras():
    """`report_fast` alone renders. mlflow is an extra, and so is the renderer.

    Importing the package must not need a tracking server's client library:
    the loop that writes a report over a folder of slides runs on a login node
    that has no business talking to MLflow.
    """
    import subprocess

    probe = (
        "import sys, types;"
        "sys.modules['mlflow'] = None;"
        "import report_fast;"
        "print(len(report_fast.__all__))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) > 0


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"ok  {name}")
