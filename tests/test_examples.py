"""The examples in the package docstring are executed, not reread.

The flagship snippet in `report_fast/__init__.py` had been wrong in three ways at
once -- swapped positional arguments, a `.build()` call on a list, and a design
that never reached the sidecar -- and nothing failed, because the blocks are `::`
examples, not doctests, so no runner ever saw them. A corrected copy in a test
would rot the same way the docstring did. So this file *parses the docstring
itself*, pulls out every indented example block, and runs it. When the prose
changes, the code that runs is the changed code, and a snippet that stops working
stops the suite.

Run:      cd /home/jovyan/report_fast && python tests/test_examples.py
Pytest:   pytest tests/test_examples.py
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import report_fast  # noqa: E402
from report_fast import verify  # noqa: E402
from report_fast.skill import examples_dir  # noqa: E402


@contextlib.contextmanager
def no_image_server():
    """Empty the DataID probe for the duration of one example.

    The documented example builds with `check` at its default, which asks the tile
    server whether it can open every DataID -- half a minute of network per
    example on a good day, and a failure on a train. What is under test is that the
    snippet runs and leaves a page behind, not whether the deployment is up. The
    probe itself is covered by `tests/test_verify.py`; `compose.build` imports it
    by name inside `build`, so patching the module attribute is what the call
    sees.
    """
    real = verify.probe
    verify.probe = lambda *a, **k: []
    try:
        yield
    finally:
        verify.probe = real


def example_blocks(documented: str) -> list:
    """Every `::` example block in a docstring, dedented, in order.

    A block is the run of indented lines after a line ending in `::`. An
    unindented line ends it, which is what keeps prose that merely *mentions* two
    colons from swallowing the paragraph after it.
    """
    blocks = []
    lines = documented.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].rstrip().endswith("::"):
            index += 1
            continue
        index += 1
        taken = []
        while index < len(lines):
            line = lines[index]
            if line.strip() and not line.startswith("    "):
                break
            taken.append(line)
            index += 1
        block = textwrap.dedent("\n".join(taken)).strip()
        if block:
            blocks.append(block)
    return blocks


def blocks() -> list:
    made = example_blocks(report_fast.__doc__)
    assert len(made) >= 3, "the package docstring lost its examples"
    return made


def run_block(block: str, directory: Path) -> dict:
    """Execute one example block inside `directory`, returning its namespace.

    `cases` is supplied rather than authored by the block because a case list is
    data -- 300 rows a real caller reads off a drive -- and the example is about
    the chain, not about inventing slides. The slots match the docstring's own
    `{0: "slide", 1: "mask"}`, filled with the design's existing DataIDs so every
    binding stays inside the gate.

    The design on disk is the shipped example session, not an invented one: the
    gate refuses a hand-waved session, so a fabricated fixture would have the test
    fighting the very validation the example depends on.
    """
    source = json.loads((examples_dir() / "dysplasia_case.json").read_text())
    (directory / "design.json").write_text(json.dumps(source))
    ids = [entry["dataID"] for entry in source["data"]]
    cases = [
        {"slide": ids[0], "mask": ids[1], "name": f"case_{letter}"}
        for letter in ("a", "b")
    ]
    previous = os.getcwd()
    os.chdir(directory)
    try:
        namespace = {"cases": cases}
        with no_image_server():
            exec(compile(block, "<docstring example>", "exec"), namespace)
        return namespace
    finally:
        os.chdir(previous)


def in_dir(made):
    """Run one test body in a fresh directory, for the standalone runner below."""
    with tempfile.TemporaryDirectory() as scratch:
        return made(Path(scratch))


def test_every_block_compiles():
    for block in blocks():
        compile(block, "<docstring example>", "exec")


def test_every_block_runs():
    # The whole point of executing the prose instead of copying it: a snippet that
    # raises -- swapped arguments, a method call on the wrong type, a name that was
    # never exported -- fails here and nowhere else, because nothing else in the
    # suite reads the docstring.
    for block in blocks():
        with tempfile.TemporaryDirectory() as scratch:
            run_block(block, Path(scratch))


def test_the_first_example_renders_a_card():
    # The snippet at the top of the page, the one a reader reaches first.
    block = blocks()[0]
    assert "XopatSession" in block, "the docstring's first example moved"

    def body(directory):
        made = run_block(block, directory)
        html = made["SlideCard"](made["session"]).to_html()
        assert "rf-card" in html, "the first example renders no card"

    return in_dir(body)


def test_the_design_example_builds_a_page():
    block = next(b for b in blocks() if "load_design" in b)

    def body(directory):
        run_block(block, directory)
        page = directory / "report.html"
        assert page.exists(), "the example never wrote its page"
        assert "rf-slide-card" in page.read_text(), "the page has no cards"

    return in_dir(body)


def test_the_design_example_records_the_design():
    # The third defect the docstring carried: a design bound, `design: null` in
    # the record. The example is the answer users copy, so the example is where
    # the recording has to be visible.
    block = next(b for b in blocks() if "load_design" in b)

    def body(directory):
        run_block(block, directory)
        record = json.loads((directory / "report.provenance.json").read_text())
        assert record["design"] is not None, "the example leaves design: null"
        assert record["design"]["slots"] == {"0": "slide", "1": "mask"}
        assert "data" in record["design"]["session"]

    return in_dir(body)


def test_the_expand_example_returns_sessions():
    block = next(b for b in blocks() if re.search(r"^\s*sessions = expand\(", b, re.M))

    def body(directory):
        made = run_block(block, directory)
        sessions = made["sessions"]
        assert isinstance(sessions, list), "expand() stopped returning sessions"
        assert len(sessions) == 2
        # The docstring says plainly that this is a list and not a page; the day it
        # returns something with `.build()` is a ruling, and this fails then.
        assert not hasattr(sessions, "build")

    return in_dir(body)


def test_every_imported_name_is_public():
    # A name an example imports has to be exported. `design_of` was written,
    # tested and documented and still was not in `__all__`, which made the
    # documented outcome unreachable from the public surface.
    imported = set()
    for block in blocks():
        for line in block.splitlines():
            found = re.match(r"\s*from\s+report_fast\s+import\s+(.+)", line)
            if found:
                imported.update(piece.strip() for piece in found.group(1).split(","))
    assert imported, "the docstring stopped importing anything"
    missing = sorted(imported - set(report_fast.__all__))
    assert not missing, f"the docstring imports names that are not exported: {missing}"


def test_design_of_is_exported():
    # The only way a Python-path caller can make `design` non-null.
    assert hasattr(report_fast, "design_of")
    assert "design_of" in report_fast.__all__


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
