"""Tests for the report shell and the components.

Run: .venv/bin/python tests/test_components.py
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import urllib.parse
import warnings
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fasthtml.common import Div, to_xml  # noqa: E402

from report_fast import (  # noqa: E402
    Chart,
    MetricTable,
    Prose,
    RawHtml,
    Report,
    Section,
    SessionTemplate,
    SlideCard,
    SlideGrid,
    XopatEndpoint,
    XopatSession,
    build_report,
)
from report_fast.components.slide_grid import xOpatViewer  # noqa: E402

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
    report = Report(title="QC", blocks=[Prose(text="hello"), SlideCard(session())])
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


def test_report_writes_one_self_contained_file():
    report = Report(title="QC", blocks=[SlideCard(session())])
    with tempfile.TemporaryDirectory() as tmp:
        written = report.write(Path(tmp) / "nested" / "report.html")
        assert written.exists()
        assert written.read_text(encoding="utf-8").startswith("<!doctype html>")
        # no sibling assets: nothing but the file itself
        assert [path.name for path in written.parent.iterdir()] == ["report.html"]


def test_report_survives_a_block_that_raises():
    class Broken(Prose):
        def render(self):
            raise RuntimeError("bad mask")

    html = Report(title="QC", blocks=[Broken(), Prose(text="still here")]).to_html()
    assert "bad mask" in html
    assert "still here" in html, "one broken block must not lose the report"


def test_report_theme_adds_an_explicit_class():
    assert 'class="rf-root rf-dark"' in Report(title="t", theme="dark").to_html()
    assert 'class="rf-root rf-dark"' not in Report(title="t").to_html()


def test_raw_fasthtml_can_be_a_block():
    html = Report(title="t", blocks=[Div("hand written")]).to_html()
    assert "hand written" in html


def test_section_groups_blocks_and_collapses_without_javascript():
    section = Section("Slides", [Prose(text="a"), Prose(text="b")], collapsible=True)
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


def test_card_embeds_the_viewer_when_asked():
    html = to_xml(SlideCard(session(), embed=True).render())
    assert "<iframe" in html
    assert f'src="{BASE}#%7B' in html


def test_legacy_viewer_name_still_builds_a_card():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        viewer = xOpatViewer(source=SLIDE, endpoint=endpoint())
    assert isinstance(viewer, SlideCard)
    assert any("SlideCard" in str(entry.message) for entry in caught)


# ── grid ────────────────────────────────────────────────────────────────────


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
    import re

    report = Report(
        title="ids",
        blocks=[
            SlideGrid([session()], endpoint=endpoint(), title="slides"),
            Prose("prose"),
            Section(title="section", blocks=[Prose("nested"), Prose("also nested")]),
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
    import re

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


# ── data components ─────────────────────────────────────────────────────────


def test_metric_table_accepts_pairs_dicts_and_records():
    assert "0.91" in to_xml(MetricTable({"auc": 0.9123}).render())
    by_slide = MetricTable({"a": {"auc": 0.9}, "b": {"auc": 0.8}}).render()
    html = to_xml(by_slide)
    assert "<th></th>" in html and "<th>auc</th>" in html and ">a<" in html
    records = to_xml(MetricTable([{"slide": "a", "auc": 0.9}]).render())
    assert "<th>slide</th>" in records and "<th>auc</th>" in records


def test_metric_table_formats_values_without_losing_meaning():
    html = to_xml(
        MetricTable({"on": True, "off": False, "none": None, "n": 12}).render()
    )
    assert ">yes<" in html and ">no<" in html and "—" in html and ">12<" in html


def test_chart_embeds_bytes_and_files():
    inline = Chart(b"png-bytes")
    assert inline.src.startswith("data:image/png;base64,")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chart.png"
        path.write_bytes(b"png-bytes")
        assert Chart(path).src == inline.src
        assert Chart(path, external=True).src == str(path)
    html = to_xml(Chart(b"x", caption="_auc_").render())
    assert "<figure" in html and "_auc_" in html


def test_chart_from_matplotlib_does_not_touch_a_file():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    figure = plt.figure()
    plt.plot([0, 1], [0, 1])
    chart = Chart.from_matplotlib(figure, caption="trend")
    plt.close(figure)
    assert chart.src.startswith("data:image/png;base64,")
    assert len(base64.b64decode(chart.src.split(",", 1)[1])) > 500


def test_raw_html_passes_through():
    html = to_xml(RawHtml(html="<custom-widget a='1'>hi</custom-widget>").render())
    assert "<custom-widget a='1'>hi</custom-widget>" in html


# ── out of the box ──────────────────────────────────────────────────────────


def test_build_report_from_paths_alone():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.html"
        report = build_report(
            ["/data/slides/a.tif", "/data/slides/b.tif"],
            masks=["/data/masks/a.tif"],
            title="QC",
            intro="Two slides.",
            metrics={"slides": 2},
            out=out,
        )
        html = out.read_text(encoding="utf-8")
        assert "QC" in html and "Two slides." in html
        assert html.count('class="rf-card rf-slide-card"') == 2
        assert len(report.blocks) == 3  # prose, grid, metrics


def test_build_report_scans_a_folder_and_takes_a_template():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "slides").mkdir()
        (root / "slides" / "a.tif").touch()
        (root / "slides" / "b.tif").touch()
        template = SessionTemplate.from_config(
            {
                "data": ["placeholder.tif", "mask.tif"],
                "background": [{"dataReference": 0, "visualizationIndex": 0}],
                "visualizations": [
                    {"shaders": {"l": {"type": "heatmap", "dataReferences": [1]}}}
                ],
            },
            slots={0: "slide"},
            endpoint=endpoint(),
        )
        report = build_report(
            directory=root / "slides", template=template, endpoint=endpoint()
        )
        html = report.to_html()
        assert "thumbnail/max_size" in html
        first = [card.session.data_ids[0] for card in report.blocks[0].cards]
        assert [Path(data_id).name for data_id in first] == ["a.tif", "b.tif"]


def test_build_report_can_collapse_the_grid():
    report = build_report(
        ["/data/s.tif"], grid={"collapsible": True, "title": "Slides"}
    )
    assert "<details" in report.to_html()


def test_build_report_appends_custom_blocks():
    report = build_report(["/data/s.tif"], blocks=[Prose(text="written by the user")])
    assert "written by the user" in report.to_html()


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
