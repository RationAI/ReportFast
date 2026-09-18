"""Tests for the report shell and the components.

Run: .venv/bin/python tests/test_components.py
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import urllib.parse
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fasthtml.common import Div, P, to_xml  # noqa: E402

from report_fast import (  # noqa: E402
    BaseComponent,
    Report,
    Section,
    SlideCard,
    SlideGrid,
    XopatEndpoint,
    XopatSession,
)


class Text(BaseComponent):
    """A block of text, for tests that need a second kind of component.

    The library ships two components and neither is prose, so a test of the
    *report* -- stylesheets gathered from every block, a block that raises, ids
    across a page -- needs a block of its own. Writing one here is also the
    documented way to extend a page, so these tests exercise that path rather
    than only the components that came with the package.
    """

    component_type = "test-text"

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.text = text

    def css(self) -> str:
        return ".rf-test-text { color: var(--rf-muted); }"

    def render(self):
        return P(self.text, cls="rf-test-text", id=self.id)


BASE = "https://xopat.example/v3/"
WSI = "https://wsi.example/v3.0"
SLIDE = "/data/slides/case_001.tif"
MASK = "/data/masks/prob_001.tif"


def endpoint(**overrides):
    values = {
        "base_url": BASE,
        "wsi_base_url": WSI,
        "image_protocol": "wsi_service",
        "mount_root": "/data",
    }
    values.update(overrides)
    return XopatEndpoint(**values)


def session(**kwargs) -> XopatSession:
    return XopatSession.from_slide(
        SLIDE, [{"path": MASK, "name": "Prob"}], name="case_001", endpoint=endpoint()
    )


def decode(url: str) -> dict:
    fragment = urllib.parse.urlsplit(url).fragment
    return json.loads(urllib.parse.unquote(fragment))


# ── report shell ────────────────────────────────────────────────────────────


def test_report_owns_the_document_and_inlines_every_stylesheet():
    report = Report(title="QC", blocks=[Text("hello"), SlideCard(session())])
    html = report.to_html()
    assert html.lstrip().startswith("<!doctype html>")
    assert "<script" not in html, "the report is static; xOpat is the interactive part"
    assert "<style>" in html
    assert ".rf-slide-card" in html, "the card's CSS is collected from the block"
    assert "hello" in html


def test_a_container_contributes_its_children_stylesheet():
    # Cards live inside a grid, not on the page. Collecting CSS from top-level
    # blocks only left the images unstyled and they overflowed the whole layout.
    grid = SlideGrid([session(), "/data/slides/b.tif", "/data/slides/c.tif"])
    css = Report(title="QC", blocks=[grid]).collect_css()
    assert ".rf-slide-card .rf-thumb img" in css
    assert css.count(".rf-slide-card {") == 1, "twelve cards, one stylesheet"
    nested = Report(title="QC", blocks=[Section("S", [SlideCard(session())])])
    assert ".rf-slide-card" in nested.collect_css()


def test_the_page_holds_two_pieces_of_prose_and_no_more():
    """`subtitle` and `preamble`, escaped, above every block.

    This is the whole prose budget of a page -- the replacement for the Prose and
    Heading components. It matters that the test is about the *budget*: a report
    whose text could be arbitrary markup is a report whose layout can drift,
    which is why the components went. Escaping is asserted in the same place
    because the argument for keeping the budget small is the argument for it not
    being a door.
    """
    report = Report(
        title="QC",
        subtitle="32 cases",
        preamble="Overlays are the model's; <script>alert(1)</script> is not.",
        blocks=[SlideCard(session())],
    )
    html = report.to_html()
    assert "32 cases" in html and "Overlays are the model" in html
    assert "class=\"rf-preamble\"" in html
    assert ".rf-preamble {" in report.collect_css()
    assert "<script" not in html, "page prose is text, escaped like every block"
    assert "&lt;script&gt;" in html, "escaped rather than dropped -- the reader sees it"
    # Above the blocks, not between them: one paragraph about the whole report.
    # Both class *attributes*, because both names also occur in the inlined
    # stylesheet, and comparing positions inside <style> proves nothing about
    # the order anything renders in.
    assert html.index('class="rf-preamble"') < html.index('class="rf-card rf-slide-card"')
    # And with neither set, neither renders: an empty paragraph is not layout.
    # Checked as a class *attribute* -- BASE_CSS names both selectors whether or
    # not the page uses them, so a bare substring search proves nothing.
    bare = Report(title="QC", blocks=[SlideCard(session())]).to_html()
    assert "class=\"rf-preamble\"" not in bare
    assert "class=\"rf-subtitle\"" not in bare


def test_report_writes_one_self_contained_file():
    report = Report(title="QC", blocks=[SlideCard(session())])
    with tempfile.TemporaryDirectory() as tmp:
        written = report.write(Path(tmp) / "nested" / "report.html")
        assert written.exists()
        assert written.read_text(encoding="utf-8").startswith("<!doctype html>")
        # no sibling assets: nothing but the file itself
        assert [path.name for path in written.parent.iterdir()] == ["report.html"]


def test_report_survives_a_block_that_raises():
    class Broken(Text):
        def render(self):
            raise RuntimeError("bad mask")

    html = Report(title="QC", blocks=[Broken("broken"), Text("still here")]).to_html()
    assert "bad mask" in html
    assert "still here" in html, "one broken block must not lose the report"


def test_report_theme_adds_an_explicit_class():
    assert 'class="rf-root rf-dark"' in Report(title="t", theme="dark").to_html()
    assert 'class="rf-root rf-dark"' not in Report(title="t").to_html()


def test_raw_fasthtml_can_be_a_block():
    html = Report(title="t", blocks=[Div("hand written")]).to_html()
    assert "hand written" in html


def test_section_groups_blocks_and_collapses_without_javascript():
    section = Section("Slides", [Text("a"), Text("b")], collapsible=True)
    html = to_xml(section.render())
    assert "<details" in html and "<summary>Slides</summary>" in html
    assert "<script" not in html
    assert "open" in html
    closed = to_xml(Section("S", [], collapsible=True, open=False).render())
    assert "open" not in closed


def test_registry_creates_components_by_name():
    from report_fast import ComponentRegistry

    assert "slide-grid" in ComponentRegistry.list()
    card = ComponentRegistry.create("slide-card", session=session())
    assert isinstance(card, SlideCard)


# ── slide card ──────────────────────────────────────────────────────────────


def test_card_links_the_session_and_embeds_a_thumbnail():
    html = to_xml(SlideCard(session()).render())
    assert "redirect.php" not in html
    assert "OpenSeadragon" not in html
    assert f'href="{BASE}#%7B' in html
    assert "thumbnail/max_size/512/512" in html
    assert "case_001" in html
    assert "1 layers" in html


def test_card_shows_the_session_json_the_link_carries():
    card = SlideCard(session())
    html = to_xml(card.render())
    preface, rest = html.split("<pre>", 1)
    shown = unescape(rest.split("</pre>", 1)[0])
    assert json.loads(shown) == card.session.to_config() == decode(card.url)


def test_card_shows_the_session_it_was_given_verbatim():
    pasted = json.loads(
        (
            Path(__file__).resolve().parents[1] / "examples" / "viewer_export.json"
        ).read_text()
    )
    html = to_xml(SlideCard(pasted, endpoint=endpoint()).render())
    assert "QcFKATZIt-Js" in html, "an id we do not model still reaches the reader"


def test_card_drops_a_preview_the_tile_server_cannot_answer():
    # Those backgrounds are `iipimage`; the WSI-Service thumbnail endpoint
    # answers a foreign DataID with a 404, so asking would print a broken image.
    pasted = json.loads(
        (
            Path(__file__).resolve().parents[1] / "examples" / "viewer_export.json"
        ).read_text()
    )
    html = to_xml(SlideCard(pasted, endpoint=endpoint()).render())
    assert "<img" not in html and "no preview" in html
    assert "Open in xOpat" in html, "the link still opens the pasted session"
    # Unpinned protocol means the deployment default serves it, so ask anyway.
    assert "<img" in to_xml(
        SlideCard(pasted, endpoint=endpoint(image_protocol=None)).render()
    )


def test_a_pasted_session_this_server_answers_gets_a_picture():
    # The demo's paste fixture is the viewer's shape over DataIDs this
    # deployment does answer, so unlike `viewer_export.json` it must draw a
    # thumbnail per background rather than a `no preview` chip.
    pasted = json.loads(
        (
            Path(__file__).resolve().parents[1] / "examples" / "dysplasia_case.json"
        ).read_text()
    )
    card = SlideCard(pasted, endpoint=endpoint())
    html = to_xml(card.render())
    assert html.count("<img") == 2, "CE and H&E"
    assert "no preview" not in html
    assert "2 backgrounds" in html
    assert decode(card.url_opening(1))["background"][1]["id"] == "b7RnQe9XsTy3"


def test_card_can_open_a_chosen_background():
    plan = XopatSession.from_slide("case/plan.tif", name="plan", endpoint=endpoint())
    followup = XopatSession.from_slide(
        "case/fu.tif", name="follow-up", endpoint=endpoint()
    )
    card = SlideCard(plan.merge(followup))
    html = to_xml(card.render())
    assert "2 backgrounds" in html
    opened = [decode(card.url_opening(index)) for index in (0, 1)]
    assert [entry["params"]["activeBackgroundIndex"] for entry in opened] == [0, 1]
    assert len(opened[0]["background"]) == 2


def test_card_single_background_links_the_plain_session():
    card = SlideCard(session())
    assert decode(card.url)["params"] == {}
    assert card.url == card.url_opening(0)


def test_card_can_drop_the_thumbnail_and_the_json():
    html = to_xml(SlideCard(session(), thumbnails=False, show_session=False).render())
    assert "thumbnail" not in html
    assert "<details" not in html
    assert "Open in xOpat" in html, "the link is the point of the card"


def test_the_meta_line_counts_plugins_rather_than_naming_them():
    """The caption line is for a reader; the session JSON is for a machine.

    A real report rendered the literal string "slide-info" under all 159 cards --
    the plugin key from the session schema, surfaced where a person reads. The count
    is kept because plugins load on boot and can change what a card draws; the key
    is not, because it is a wire-format name and there is no label for it to become
    (the contract derives only "plugin id -> options", and a hand-typed table would
    be a second source of truth).
    """
    configured = XopatSession.from_config(
        {
            "data": [{"dataID": "case_001.tif", "protocol": "wsi_service"}],
            "background": [{"id": "b", "name": "case_001", "dataReference": 0}],
            "visualizations": [],
            "plugins": {"slide-info": {}, "annotations": {}},
        },
        endpoint=endpoint(),
    )
    html = to_xml(SlideCard(configured, thumbnails=False, show_session=False).render())
    assert "2 plugins" in html, html
    # Scoped to the caption line on purpose: the fragment in `href` carries the
    # whole session *including* its plugins, and it must -- that is the payload.
    # The complaint was the key surfacing where a person reads, not its presence
    # in the page.
    meta = re.search(r'<span class="rf-meta">([^<]*)</span>', html)
    assert meta and "slide-info" not in meta.group(1), meta and meta.group(1)
    assert "slide-info" in html, "the session the link carries is unchanged"

    plain = to_xml(SlideCard(session(), thumbnails=False, show_session=False).render())
    plain_meta = re.search(r'<span class="rf-meta">([^<]*)</span>', plain)
    assert "plugins" not in (plain_meta.group(1) if plain_meta else ""), (
        "a session with no plugins should say nothing about them"
    )


def test_the_same_report_renders_byte_identical_html():
    """Two builds of the same report are the same bytes.

    A report is mailed, diffed, and re-run against a redone mask run; any byte
    that varies for no reason makes all three of those harder. The variation this
    rules out is the one that creeps in unnoticed: `BaseComponent` falls back to a
    random id, which is fine for one component on a page and wrong for a file
    someone diffs tomorrow -- so `Report` renumbers the ids it generated itself,
    and a component the caller named keeps the name it was given.
    """

    def build() -> str:
        sessions = [session(), session()]
        return Report(
            title="reproducible",
            subtitle="two cards",
            preamble="Nothing here varies.",
            blocks=[
                SlideGrid(sessions=sessions, endpoint=endpoint(), title="slides"),
                Section(title="notes", blocks=[Text("a note")]),
            ],
        ).to_html()

    first, second = build(), build()
    assert first == second, "the same report produced different bytes"
    # A caller-named id survives, because the point is that it was named.
    named = Report(blocks=[Text("kept", id="caller-chosen")]).to_html()
    assert 'id="caller-chosen"' in named


def test_card_embeds_the_viewer_when_asked():
    html = to_xml(SlideCard(session(), embed=True).render())
    assert "<iframe" in html
    assert f'src="{BASE}#%7B' in html


def test_no_element_id_appears_twice_in_a_page():
    """Every id on a page names one element.

    Found by someone reading a 159-card report: `Report` stamped its `rf-block`
    wrapper with `block.id` while every frozen component also puts `id=self.id` on
    its own root, so each block produced two nested elements with the same id --
    invalid HTML, and an anchor whose target is whichever of the two a browser
    picks. Asserted over the whole page rather than per component, because the bug
    lived in the *wrapper*, so no component's own output looked wrong.
    """
    import collections

    report = Report(
        title="ids",
        blocks=[
            SlideGrid([session()], endpoint=endpoint(), title="slides"),
            Text("prose"),
            Section(title="section", blocks=[Text("nested"), Text("also nested")]),
        ],
    )
    html = report.to_html()
    ids = re.findall(r'id="([^"]+)"', html)
    duplicated = {name: n for name, n in collections.Counter(ids).items() if n > 1}
    assert not duplicated, f"ids on more than one element: {duplicated}"
    # And not merely unique: every block still has its anchor. A fix that deleted
    # ids would pass the assertion above while making deep-links impossible.
    for block in list(report.blocks) + list(report.blocks[2].blocks):
        assert ids.count(block.id) == 1, f"{block.id} appears {ids.count(block.id)}x"


def test_a_block_that_renders_no_id_still_gets_its_anchor():
    """The other half of the wrapper rule: the id is dropped only when redundant.

    A raw FastHTML tree and a block whose `render()` raises both produce a root with
    no id on it. There the wrapper is the only element that can carry the anchor, so
    stamping it is required, not redundant.
    """
    from report_fast.core import BaseComponent, _wrap

    class Boom(BaseComponent):
        component_type = "boom"

        def render(self):
            raise RuntimeError("no component survives this")

    raw = _wrap(Div("bare tree"))
    boom = Boom()
    html = Report(title="ids", blocks=[raw, boom]).to_html()
    ids = re.findall(r'id="([^"]+)"', html)
    assert ids.count(raw.id) == 1, ids
    assert ids.count(boom.id) == 1, f"a failed block lost its anchor: {ids}"
    assert "rf-error" in html, "the failed block should still render the error card"


def test_grid_lays_out_one_card_per_session():
    grid = SlideGrid(
        [
            session(),
            "/data/slides/case_002.tif",
            {"data": ["x.tif"], "background": [{"dataReference": 0}]},
        ],
        endpoint=endpoint(),
    )
    html = to_xml(grid.render())
    assert html.count('class="rf-card rf-slide-card"') == 3
    assert [card.session.names[0] for card in grid.cards] == [
        "case_001",
        "case_002",
        "x",
    ]


def test_grid_add_is_fluent_and_keeps_the_defaults():
    grid = SlideGrid([], endpoint=endpoint(), card={"thumbnails": False})
    assert grid.add(SLIDE) is grid
    assert grid.cards[0].thumbnails is False
    assert grid.cards[0].session.endpoint == endpoint()


def test_grid_collapses_the_whole_set():
    html = to_xml(SlideGrid([session()], title="Slides", collapsible=True).render())
    assert "<details" in html and "<summary>Slides</summary>" in html


def main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures = []
    for name, test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - report every failure kind
            failures.append((name, exc))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
