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
