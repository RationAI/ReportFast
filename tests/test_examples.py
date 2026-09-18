"""The examples in the package docstring are executed, not merely read.

A docstring snippet rots in ways no other test sees: a name stops being exported,
an argument swaps order, a method moves to another object. None of that fails a
test, because nothing else in the suite reads the docstring -- so this file does,
and runs what it finds.

No network here. A report example is a few lines of Python that touch no server
(a session is a DataID string, a thumbnail is a URL, nothing opens either), so
unlike the old build-command examples there is nothing to stub: the tile server
is never called, and a test on a train behaves as it does on the cluster.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import report_fast  # noqa: E402


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
    assert len(made) >= 2, "the package docstring lost its examples"
    return made


def run_block(block: str, directory: Path) -> dict:
    """Execute one example block inside `directory`, returning its namespace.

    The working directory is switched rather than the snippet rewritten, so what
    runs is the text a reader copies: an example that only works from the repo
    root is an example that fails in someone's project.
    """
    previous = os.getcwd()
    os.chdir(directory)
    try:
        namespace: dict = {}
        exec(compile(block, "<docstring example>", "exec"), namespace)
        return namespace
    finally:
        os.chdir(previous)


def in_dir(body):
    """Run one test body in a fresh directory."""
    with tempfile.TemporaryDirectory() as scratch:
        return body(Path(scratch))


def test_every_block_compiles():
    for block in blocks():
        compile(block, "<docstring example>", "exec")


def test_every_block_runs():
    # The whole point of executing the prose instead of copying it: a snippet that
    # raises -- swapped arguments, a method call on the wrong type, a name that was
    # never exported -- fails here and nowhere else.
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


def test_the_second_example_writes_a_page():
    """The loop-and-write example is what an agent copies, so it must run.

    It globs a mount that is not present on a dev machine, which is the honest
    shape of the snippet: an empty folder yields a page with no cards rather than
    an error, and that is worth knowing -- the failure someone meets when the
    mount is not attached is a thin report, not a traceback.
    """
    block = next(b for b in blocks() if ".write(" in b)

    def body(directory):
        run_block(block, directory)
        page = directory / "report.html"
        assert page.exists(), "the example never wrote its page"
        written = page.read_text()
        assert written.lower().startswith("<!doctype html>"), "the page has no doctype"
        assert "rf-main" in written, "the page is not the report shell"
        assert "<script" not in written, "the page is no longer JavaScript-free"

    return in_dir(body)


def test_every_named_name_is_public():
    # Any report_fast name the docstring spells out has to be exported. `design_of`
    # was once written, tested and documented and still was not in `__all__`, which
    # made the documented outcome unreachable from the public surface. Scanned over
    # the whole docstring rather than only the `::` blocks: prose that names
    # :func:`sessions_from_masks` is a promise to the same extent as a snippet.
    documented = report_fast.__doc__ or ""
    named = set(re.findall(r":(?:class|func|meth|mod):`([A-Za-z_][\w.]*)`", documented))
    named |= set(
        piece.strip()
        for found in re.finditer(r"from report_fast import (.+)", documented)
        for piece in found.group(1).split(",")
    )
    # A method named on another object (`XopatSession.add_data`) and a module path
    # are attributes of something public, not public names themselves.
    top = {name.split(".")[0] for name in named}
    assert top, "the docstring stopped naming anything"
    missing = sorted(top - set(report_fast.__all__))
    assert not missing, f"the docstring names things that are not exported: {missing}"


def test_the_docstring_stops_promising_a_record():
    # Nothing is written beside a report any more; the script that built it is the
    # record. A docstring that mentions a sidecar is the bug this pins against.
    documented = report_fast.__doc__ or ""
    for stale in ("provenance", "sidecar", "manifest", "build_report"):
        assert stale not in documented.lower(), f"the package docstring still says {stale!r}"


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
