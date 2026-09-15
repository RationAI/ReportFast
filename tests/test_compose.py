"""Tests for `report_fast.compose` -- the in-memory composition, and the temp-dir door.

DESIGN.md step 4. The door prompt mode enters through: agent-authored session
JSON arrives as files, is validated through the step-2 gate, is composed from the
frozen set, and leaves exactly one HTML file behind. Three properties are the
thing under test, one per ruling:

  * **The gate is the agent's gate.** An out-of-allowlist params key -- which a
    paste would keep with a warning -- refuses the build here, by JSON path, with
    the filename in the message (fourteen files, one bad).
  * **Nothing is persisted but the HTML.** No manifest, no plan file, no session
    copy on disk, and publish is not reachable from `build` at all.
  * **The design is authored once and instantiated in a loop.** One hand-written
    session design; N bindings; a typo in one case names that case.

Run:      cd /home/jovyan/report_fast && python tests/test_compose.py
Pytest:   pytest tests/test_compose.py
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_fast import compose  # noqa: E402
from report_fast.compose import (  # noqa: E402
    DEFAULT_OUT,
    DEFAULT_SLOT,
    ComposeError,
    ComposeNotFound,
    Composition,
    expand,
    load_design,
    load_session,
    sessions_from_dir,
    temp_dir_sessions,
)
from report_fast.components import Heading, Prose, RawHtml  # noqa: E402
from report_fast.components.slide_grid import SlideCard  # noqa: E402
from report_fast.frozen import Block  # noqa: E402
from report_fast.session import XopatSession  # noqa: E402
from report_fast.verify import Check  # noqa: E402
from report_fast.xopat import XopatEndpoint  # noqa: E402

ENDPOINT = XopatEndpoint(
    base_url="https://xopat.example/xopat/",
    wsi_base_url="https://tiles.example/wsi/",
    image_protocol="wsi_service",
    mount_root="/data",
)

#: A whole session document, in the shape a viewer export has -- `shaders` keyed
#: by layer id with an `order`, `dataReferences` pointing into `data[]` by index.
#: Not the minimal document `test_session.py` builds by hand, because this door
#: reads files people export from the viewer, and `params` is where those exports
#: get interesting. Two `data` entries, one background, one overlay: the shape a
#: slide + probability-mask pair takes, which is what gets bound 300 times.
DESIGN = {
    "params": {"sessionName": "case-a", "theme": "auto", "activeBackgroundIndex": 0},
    "data": [
        {"dataID": "/data/case-a/a.tif", "protocol": "wsi_service"},
        {
            "dataID": "/data/case-a/a.prob.tif",
            "protocol": "wsi_service",
            "options": {"format": "png"},
            "imageSmoothingEnabled": False,
        },
    ],
    "background": [
        {"id": "slide", "name": "case-a", "dataReference": 0, "visualizationIndex": 0},
    ],
    "visualizations": [
        {
            "name": "case-a",
            "order": ["probability"],
            "shaders": {
                "probability": {
                    "type": "colormap",
                    "name": "Probability",
                    "visible": 1,
                    "fixed": False,
                    "dataReferences": [1, 0],
                    "params": {"color": "#ff8c00", "threshold": 0.5},
                }
            },
        }
    ],
}


def design(**override):
    """A fresh copy of the design, so a test can break it without affecting others."""
    import copy

    return {**copy.deepcopy(DESIGN), **override}


def ids(session: XopatSession):
    """The `data[]` entries' ids, whether the entries are objects or bare strings."""
    return [
        entry["dataID"] if isinstance(entry, dict) else entry
        for entry in session.data
    ]


# ── load_session: the gate at the door ─────────────────────────────────────


def test_an_authored_session_loads_clean_and_is_marked_authoritative():
    session = load_session(json.dumps(design()), endpoint=ENDPOINT, origin="a.json")
    assert session.authoritative is True
    assert session.findings() == []
    assert session.names == ["case-a"]
    # One session = one window. The slide and its mask are two `data[]` entries
    # inside it, joined by the overlay's `dataReferences` -- not two sessions.
    assert len(session.background) == 1 and len(session.data) == 2
    assert ids(session) == ["/data/case-a/a.tif", "/data/case-a/a.prob.tif"], (
        "an authored session keeps its DataIDs exactly as written -- only a bound "
        "slot goes through mount_root, because only then does the tool pick the id"
    )


def test_a_path_a_json_string_and_a_mapping_all_arrive_at_the_same_session():
    path = _write(Path(_temporary_dir()) / "case-01.json")
    from_path = load_session(path, endpoint=ENDPOINT, origin=path.name)
    from_text = load_session(json.dumps(design()), endpoint=ENDPOINT)
    from_mapping = load_session(design(), endpoint=ENDPOINT)
    assert from_path.to_config() == from_text.to_config() == from_mapping.to_config()


def test_an_off_allowlist_key_refuses_the_build_and_names_the_file_and_the_path():
    """The ruling that separates this from a paste, asserted in both directions."""
    broken = design()
    broken["params"]["threshhold"] = 0.5

    try:
        load_session(broken, endpoint=ENDPOINT, origin="case-017.json")
    except ComposeError as error:
        message = str(error)
    else:
        raise AssertionError("an invented params key must not survive the agent's door")

    assert "case-017.json" in message, message
    assert "params.threshhold" in message, message
    # ...and the same document still loads on the paste path, with a warning.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        kept = XopatSession.from_config(json.dumps(broken), endpoint=ENDPOINT)
    assert any("threshhold" in str(item.message) for item in caught)
    assert kept.to_config()["params"]["threshhold"] == 0.5, "a paste keeps its bytes"


def test_a_layer_param_the_shader_never_declared_refuses_the_build():
    broken = design()
    broken["visualizations"][0]["shaders"]["probability"]["params"]["colormap"] = "viridis"
    _refuses(broken, "shaders.probability.params.colormap")


def test_a_dangling_data_reference_refuses_the_build_rather_than_loading_broken():
    """`reference`-kind findings are hard on both paths -- this is not a soft call."""
    broken = design()
    broken["visualizations"][0]["shaders"]["probability"]["dataReferences"] = [9, 0]
    _refuses(broken, "dataReferences")

    # The paste path agrees here, so this is not the soft-key message about
    # off-allowlist keys -- it is the hard one, wrapped with the filename.
    try:
        load_session(broken, endpoint=ENDPOINT, strict=False, origin="broken.json")
    except ComposeError as error:
        assert "broken.json" in str(error), error
        assert "off-allowlist" not in str(error), error
    else:
        raise AssertionError("a dangling index must not load on either path")


def test_a_file_that_is_not_json_says_so_rather_than_reporting_a_parse_stack():
    path = Path(_temporary(".txt"))
    path.write_text("data: []\n", encoding="utf-8")
    try:
        load_session(path, endpoint=ENDPOINT, origin=path.name)
    except ComposeError as error:
        assert "is not JSON" in str(error), error
        assert "not a wrapper around it" in str(error), error
    else:
        raise AssertionError("YAML is not a session document")


def test_a_missing_file_is_named_and_not_reported_as_bad_json():
    try:
        load_session("/nope/case-07.json", endpoint=ENDPOINT, origin="case-07.json")
    except ComposeNotFound as error:
        # Named, and said as *not there* rather than as an errno or as bad JSON:
        # the reader has to know whether to go write the file or go edit it.
        assert "case-07.json" in str(error) and "no such file" in str(error)
        assert isinstance(error, FileNotFoundError) and isinstance(error, ComposeError)
    else:
        raise AssertionError("a missing session file must not read as an empty one")


def test_inline_json_is_not_mistaken_for_a_filename():
    """`--session '{"data": …}'` and a path come through the same argument."""
    session = load_session(json.dumps(design()), endpoint=ENDPOINT)
    assert len(session.data) == 2
    # And a path that merely looks like one is still reported as missing.
    assert not compose._is_path('{"data": [1]}')
    assert compose._is_path("sessions/case-01.json")
    assert compose._is_path(Path("sessions/case-01.json"))


# ── load_design / expand: one design, N cases ──────────────────────────────


def test_a_design_is_gated_once_and_every_binding_keeps_that_verdict():
    template = load_design(
        json.dumps(design()), slots={0: "slide", 1: "mask"}, endpoint=ENDPOINT
    )
    assert template.session.authoritative is True
    assert template.slot_names == ["slide", "mask"]

    bound = [
        template.bind(slide=f"/data/case-{i}/s.tif", mask=f"/data/case-{i}/p.tif",
                      name=f"case-{i}")
        for i in range(300)
    ]
    assert all(session.authoritative for session in bound), (
        "the verdict a design entered under is the verdict its 300 bindings have"
    )
    assert ids(bound[217]) == ["case-217/s.tif", "case-217/p.tif"], (
        "a bound slot is mounted through the endpoint; the other entry keeps its own"
    )
    assert bound[217].names == ["case-217"]
    # The design's own layer params survive instantiation; 300 windows must not
    # each lose the settings the design was written to carry.
    params = bound[7].to_config()["visualizations"][0]["shaders"]["probability"]["params"]
    assert params == {"color": "#ff8c00", "threshold": 0.5}


def test_a_design_read_non_strict_stays_non_strict_for_every_binding():
    """The flag is the *session's*, so `load_design` must forward `strict`."""
    broken = json.loads(json.dumps(design()))
    broken["params"]["threshhold"] = 0.5
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        template = load_design(
            broken, slots={0: "slide"}, endpoint=ENDPOINT, strict=False
        )
    assert template.session.authoritative is False
    # The property that matters is that a binding *warns* rather than raises: the
    # bug this pins is the one where the design passed with a warning and then
    # slide 3 of 300 raised on the same key.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bound = template.bind(slide="/data/case-b/b.tif")
    assert bound.authoritative is False
    assert any("threshhold" in str(item.message) for item in caught), caught
    assert all("will not load" not in str(item.message) for item in caught)


def test_a_slot_index_the_design_does_not_have_is_refused_at_the_door():
    """A design gains a background; the caller's `slots=` did not follow."""
    try:
        load_design(json.dumps(design()), slots={0: "slide", 5: "mask"}, endpoint=ENDPOINT)
    except ComposeError as error:
        assert "data[5]" in str(error), error
    else:
        raise AssertionError("a slot pointing outside data[] must not build a template")


def test_a_bare_design_slots_index_zero_as_slide_so_the_common_case_needs_no_flag():
    template = load_design(json.dumps(design()), endpoint=ENDPOINT)
    assert template.slots == {0: DEFAULT_SLOT}


def test_expand_names_the_case_that_failed_and_its_slot_values():
    cases = [
        {"slide": "/data/1/s.tif", "mask": "/data/1/p.tif"},
        {"slide": "/data/2/s.tif", "mask": "/data/2/p.tif", "name": "second"},
        {"slidez": "/data/3/s.tif", "mask": "/data/3/p.tif"},
    ]
    try:
        expand(json.dumps(design()), cases, endpoint=ENDPOINT,
               slots={0: "slide", 1: "mask"}, origin="design.json")
    except ComposeError as error:
        message = str(error)
        assert "case 2" in message, message
        assert "slidez" in message, message
        assert "/data/3/s.tif" in message, message
    else:
        raise AssertionError("a typo in case 217 of 300 must name case 217")


def test_expand_gives_every_case_its_name_and_an_empty_run_is_not_a_report():
    sessions = expand(
        json.dumps(design()),
        [
            {"slide": "/data/1/s.tif", "mask": "/data/1/p.tif", "name": "one"},
            {"slide": "/data/2/s.tif", "mask": "/data/2/p.tif", "name": "two"},
        ],
        slots={0: "slide", 1: "mask"},
        endpoint=ENDPOINT,
    )
    assert [session.names[0] for session in sessions] == ["one", "two"]

    try:
        expand(json.dumps(design()), [], endpoint=ENDPOINT, origin="design.json")
    except ComposeError as error:
        assert "no cases" in str(error)
    else:
        raise AssertionError("an empty folder must not produce an empty report")


# ── Composition: the frozen gate at composition time ───────────────────────


def test_a_composition_builds_from_names_mappings_and_objects_alike():
    session = load_session(json.dumps(design()), endpoint=ENDPOINT)
    composition = Composition(
        title="QC",
        blocks=[
            "Prose",  # a bare name: everything defaulted, an empty paragraph
            {"Prose": {"text": "Twelve cores, three masks each."}},
            Block("SlideGrid", {"sessions": [session]}),
            Prose("Built by hand, in Python."),
        ],
    )
    page = composition.to_report()
    kinds = [type(block).__name__ for block in page.blocks]
    assert kinds == ["Prose", "Prose", "SlideGrid", "Prose"]
    # The grid's cards *are* its sessions, so the walk stops at the grid; counting
    # both would report 60 cases for a report of 30 and probe twice.
    assert composition.sessions() == [session]
    # ...and every reader sees the *same* objects, not four equal constructions.
    assert composition.to_plan().sessions == composition.sessions()
    assert composition.components() is composition.components()


def test_raw_html_is_refused_however_it_is_spelled_or_nested():
    """Decision 10, at the composition rather than at the file reader."""
    for blocks in (
        [{"raw_html": {"html": "<b>hi</b>"}}],
        [{"rawHtml": {"html": "<b>hi</b>"}}],
        [{"raw_html": "<b>hi</b>"}],
        [{"Section": {"title": "Methods", "blocks": [{"raw_html": "<i>x</i>"}]}}],
    ):
        try:
            Composition(title="QC", blocks=blocks).to_report()
        except Exception as error:
            assert "frozen" in str(error) or "raw_html" in str(error), (blocks, error)
        else:
            raise AssertionError(f"{blocks} must not reach the page")


def test_a_component_outside_the_frozen_set_is_refused_by_name():
    try:
        Composition(title="QC", blocks=[{"Footer": {"text": "x"}}]).to_report()
    except Exception as error:
        assert "Footer" in str(error) and "frozen" in str(error), error
    else:
        raise AssertionError("an unknown component name must not silently vanish")

    # The miss an agent actually makes: the manifest's spelling, on a door that
    # names components as the code does. Telling them to *add* SlideGrid -- which
    # exists -- sends them to edit the frozen table for nothing.
    try:
        Composition(title="QC", blocks=[{"slide_grid": {"sessions": []}}]).to_report()
    except Exception as error:
        assert "SlideGrid" in str(error) and "manifest" in str(error), error
    else:
        raise AssertionError("a snake_case name must be refused, not guessed at")


def test_a_wrong_keyword_comes_back_as_the_door_s_error_with_a_signature():
    """Not a `TypeError` from inside a library the caller cannot see."""
    try:
        Composition(title="QC", blocks=[{"SlideGrid": {"cards": []}}]).to_report()
    except Exception as error:
        assert "SlideGrid" in str(error) and "sessions" in str(error), error
        assert "Signature" in str(error), error
    else:
        raise AssertionError("a bad keyword must not surface as a raw TypeError")


def test_a_prebuilt_component_is_the_python_path_and_is_not_re_litigated():
    """Decision 10 constrains what a *spec* may name; a person holding Python is
    the author the rule permits, so passing objects through is not a hole."""
    page = Composition(title="QC", blocks=[RawHtml("<b>mine</b>")]).to_report()
    assert type(page.blocks[0]).__name__ == "RawHtml"


def test_a_block_mapping_with_two_names_in_it_is_a_typo_not_a_guess():
    try:
        Composition(
            title="QC", blocks=[{"Heading": {"text": "a"}, "Prose": {"text": "b"}}]
        ).to_report()
    except ComposeError as error:
        assert "one component name" in str(error), error
    else:
        raise AssertionError("two names in one block cannot be ordered by luck")


def test_sessions_are_read_off_the_page_rather_than_declared_alongside_it():
    session = load_session(json.dumps(design()), endpoint=ENDPOINT)
    nested = Composition(
        title="QC",
        blocks=[
            {
                "Section": {
                    "title": "Cohort",
                    "blocks": [{"SlideCard": {"session": session.to_config()}}],
                }
            }
        ],
    )
    found = nested.sessions()
    assert len(found) == 1 and found[0].names == ["case-a"]
    # A grid and a card both count, and neither is counted twice by two walks.
    flat = Composition(
        title="QC",
        blocks=[{"SlideGrid": {"sessions": [session]}}, {"SlideCard": {"session": session}}],
    )
    assert len(flat.sessions()) == 2


# ── build: writes one file, probes, and cannot publish ─────────────────────


def test_build_writes_the_html_and_a_page_with_no_sessions_is_refused(tmp_path=None):
    session = load_session(json.dumps(design()), endpoint=ENDPOINT)
    out = Path(_temporary(".html")) if tmp_path is None else tmp_path / "r.html"
    composition = Composition(
        title="Cohort QC",
        subtitle="12 cores",
        blocks=[{"SlideGrid": {"sessions": [session]}}],
    )
    with _stubbed_probe():
        built = composition.build(out=out, check=True)

    html = Path(built.out).read_text(encoding="utf-8")
    assert [s.names[0] for s in built.plan.sessions] == ["case-a"]
    assert Path(built.out) == Path(out)
    assert "Cohort QC" in html and "case-a" in html
    assert built.plan.cases == ["case-a"]
    assert built.plan.layers == {"case-a": 1}, "one overlay: the probability map"
    assert built.published is None
    from report_fast.provenance import sidecar_path

    assert Path(built.provenance) == sidecar_path(built.out)
    _remove_pair(built.out)


def test_build_defaults_to_a_printed_fixed_name_not_one_guessed_from_the_title():
    import inspect

    signature = inspect.signature(Composition.build)
    assert signature.parameters["out"].default is None
    assert DEFAULT_OUT == Path("report.html")


def test_publish_is_not_reachable_from_the_composition_at_all():
    """Checked as syntax, not as text: the docstring *says* "publish", on purpose.

    Building a report has never implied uploading one, and the way to keep that
    true is for the word to appear nowhere in this module's code. A grep would
    fail on the prose that explains the rule, so the AST is walked instead: no
    attribute, keyword or import may be named "publish".
    """
    import ast
    import inspect

    assert "publish" not in inspect.signature(Composition.build).parameters
    assert not any("publish" in name for name in dir(Composition))

    tree = ast.parse(inspect.getsource(compose))
    reachable = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            reachable.add(node.attr)
        elif isinstance(node, ast.keyword):
            reachable.add(node.arg or "")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            reachable.update(alias.name for alias in node.names)
    assert not sorted(name for name in reachable if "publish" in name.lower())
    assert "publish" not in getattr(compose, "__all__", [])


def test_to_plan_is_the_same_plan_object_the_manifest_build_hands_on():
    """So the probe and the exit codes are literally the same code, not a copy."""
    from report_fast.manifest import Plan

    session = load_session(json.dumps(design()), endpoint=ENDPOINT)
    plan = Composition(
        title="QC", blocks=[{"SlideGrid": {"sessions": [session]}}]
    ).to_plan()
    assert isinstance(plan, Plan)
    assert plan.out is None, "a composition has no path until -o says so"
    assert plan.cases == ["case-a"] and plan.layers == {"case-a": 1}
    # The same two forms the manifest path prints, so the CLI's output shape is
    # shared rather than paraphrased by a second implementation.
    assert "1 cases" in plan.to_text()
    assert json.loads(plan.to_json())["cases"] == ["case-a"]


def test_a_page_with_no_session_at_all_is_refused_rather_than_published_empty():
    try:
        Composition(title="Notes", blocks=[Heading("Notes")]).to_plan()
    except ComposeError as error:
        assert "nothing to build" in str(error)
    else:
        raise AssertionError("a report that links no session is not this door's job")


# ── sessions_from_dir / temp_dir_sessions: the --sessions-dir door ──────────


def test_a_directory_of_sessions_loads_in_filename_order():
    """Named by hand, out of order, because `Path.glob` promises nothing.

    A report the same command runs twice has to lay the cards out the same twice,
    and a folder read in inode order would not. The temp-dir helper numbers its
    files, so this writes them directly -- that numbering is itself asserted by
    the round-trip test below.
    """
    template = load_design(json.dumps(design()), endpoint=ENDPOINT)
    root = Path(_temporary_dir())
    for name in ("case-03", "case-01", "case-02"):
        session = template.bind(slide=f"/data/{name}/s.tif", name=name)
        (root / f"{name}.json").write_text(
            json.dumps(session.to_config()), encoding="utf-8"
        )
    loaded = sessions_from_dir(root, endpoint=ENDPOINT)
    assert [s.names[0] for s in loaded] == ["case-01", "case-02", "case-03"], (
        "the same command twice must lay the cards out the same twice"
    )


def test_one_bad_file_refuses_the_directory_and_names_the_file():
    good = load_session(json.dumps(design()), endpoint=ENDPOINT)
    broken = json.loads(json.dumps(design()))
    broken["params"]["threshhold"] = 0.5
    with temp_dir_sessions([good]) as root:
        (root / "999-broken.json").write_text(json.dumps(broken), encoding="utf-8")
        assert len(list(root.glob("*.json"))) == 2
        try:
            sessions_from_dir(root, endpoint=ENDPOINT)
        except ComposeError as error:
            assert "999-broken.json" in str(error), error
        else:
            raise AssertionError("a bad session must not be skipped silently")


def test_an_empty_or_absent_directory_is_said_as_an_absent_directory():
    with temp_dir_sessions([]) as root:
        try:
            sessions_from_dir(root, endpoint=ENDPOINT)
        except ComposeError as error:
            assert "no *.json files" in str(error), error
        else:
            raise AssertionError("an empty folder is not an empty report")
    try:
        sessions_from_dir(Path(_temporary_dir()) / "not-here", endpoint=ENDPOINT)
    except ComposeError as error:
        assert "not a directory" in str(error) and "Nothing was read" in str(error)


def test_the_temp_dir_is_gone_afterwards_because_the_door_persists_nothing():
    sessions = [load_session(json.dumps(design()), endpoint=ENDPOINT)]
    with temp_dir_sessions(sessions) as root:
        assert root.is_dir() and len(list(root.glob("*.json"))) == 1
        inside = root
    assert not inside.exists()


def test_sessions_written_by_the_helper_reload_through_the_same_gate():
    """Round-trip: a session this tool built must be acceptable as authored."""
    original = load_design(
        json.dumps(design()), slots={0: "slide", 1: "mask"}, endpoint=ENDPOINT
    ).bind(slide="/data/case-c/s.tif", mask="/data/case-c/p.tif", name="case-c")
    with temp_dir_sessions([original]) as root:
        [reloaded] = sessions_from_dir(root, endpoint=ENDPOINT)
    assert reloaded.to_config() == original.to_config()
    assert ids(reloaded) == ids(original) == ["case-c/s.tif", "case-c/p.tif"], (
        "the bound slot keeps the mounted id it was given; the door does not re-mount"
    )


# ── to_manifest: an offer, never a side effect ──────────────────────────────


def test_emit_manifest_is_text_only_and_no_build_path_calls_it():
    session = load_session(json.dumps(design()), endpoint=ENDPOINT)
    composition = Composition(
        title="QC", blocks=[{"SlideGrid": {"sessions": [session]}}]
    )
    text = composition.to_manifest()
    assert "QC" in text and "from_config" in text
    assert not Path("QC.yaml").exists() and not Path("report.yaml").exists()

    # A manifest is only worth printing if `reportfast plan` reads it back, so
    # the emitted document goes through the real reader here.
    path = Path(_temporary(".yaml"))
    path.write_text(text, encoding="utf-8")
    spec = _read_manifest(path)
    assert spec["title"] == "QC"
    assert len(spec["sessions"]) == 1
    path.unlink(missing_ok=True)


def test_a_composition_with_no_manifest_form_says_so_instead_of_printing_a_lie():
    """A printed manifest that `plan` rejects is worse than no printed manifest."""
    session = load_session(json.dumps(design()), endpoint=ENDPOINT)
    for blocks in (
        [SlideCard(session)],  # a card built in Python: there is no YAML for it
        [Prose("notes"), RawHtml("<b>mine</b>")],
    ):
        try:
            Composition(title="QC", blocks=blocks).to_manifest()
        except ComposeError as error:
            assert "no manifest form" in str(error), error
        else:
            raise AssertionError(f"{blocks!r} has no manifest spelling")


def test_a_rich_composition_round_trips_through_the_manifest_reader():
    """Blocks written as components come back out as the manifest's own keys."""
    session = load_session(json.dumps(design()), endpoint=ENDPOINT)
    composition = Composition(
        title="Cohort",
        subtitle="12 cores",
        blocks=[
            # The grid first, because that is where a manifest puts it: the page
            # order a manifest builds is grid-then-blocks, and a composition with
            # the grid last therefore has no manifest form (see the test below).
            {"SlideGrid": {"sessions": [session], "min_width": 320}},
            {"Heading": {"text": "Findings"}},
            {"Prose": {"paragraphs": ["one", "two"]}},
            {"Bullets": {"items": ["a", "b"]}},
            {"LinkList": {"links": {"viewer": "https://xopat.example/"}}},
            {"Section": {"title": "Methods", "blocks": [{"Heading": {"text": "Prep"}}]}},
        ],
    )
    path = Path(_temporary(".yaml"))
    path.write_text(composition.to_manifest(), encoding="utf-8")
    spec = _read_manifest(path)
    assert [list(row)[0] for row in spec["blocks"]] == [
        "heading", "prose", "bullets", "links", "section",
    ], spec["blocks"]
    assert spec["grid"] == {"min_width": 320}, "the grid is `grid:`, not a block"
    assert spec["subtitle"] == "12 cores"
    assert spec["endpoint"]["mount_root"] == "/data"
    path.unlink(missing_ok=True)


def test_no_build_path_calls_to_manifest():
    """`--emit-manifest` is an offer; a gate that grows an artifact by itself is
    how an intermediate becomes the contract. Checked as syntax, because the
    docstrings say the word while explaining why nothing calls it."""
    import ast
    import inspect
    import textwrap

    for name, member in inspect.getmembers(Composition, inspect.isfunction):
        if name == "to_manifest" or name.startswith("__"):
            continue  # dataclass-generated dunders have no source to read
        tree = ast.parse(textwrap.dedent(inspect.getsource(member)))
        called = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        attributes = [
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        ]
        assert "to_manifest" not in called + attributes, name


# ── helpers ────────────────────────────────────────────────────────────────


def _read_manifest(path: Path):
    from report_fast.manifest import read

    return read(path)


def _refuses(document, path: str, **kwargs) -> None:
    try:
        load_session(document, endpoint=ENDPOINT, origin="case.json", **kwargs)
    except ComposeError as error:
        assert path in str(error), f"{path!r} missing from: {error}"
        assert "case.json" in str(error), error
    else:
        raise AssertionError(f"{path} must be refused on the agent's door")


def _remove_pair(html) -> None:
    """Delete a written report *and* its sidecar.

    A build writes two files now, and a helper that removed one left the other in
    the system temp directory forever -- ten test runs of litter is how a shared
    machine fills up, and an orphan sidecar beside no HTML is precisely the state
    `provenance.verify_pair` exists to report.
    """
    from report_fast.provenance import sidecar_path

    Path(html).unlink(missing_ok=True)
    sidecar_path(html).unlink(missing_ok=True)


def _temporary(suffix: str) -> str:
    import tempfile

    handle, path = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    os.unlink(path)
    return path


def _temporary_dir() -> str:
    import tempfile

    return tempfile.mkdtemp()


def _tmpdir_session_path() -> Path:
    return Path(_temporary_dir()) / "case-01.json"


def _write(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DESIGN, indent=2), encoding="utf-8")
    return path


class _stubbed_probe:
    """Replace the network probe with a fixed verdict.

    `Composition.build` is required to call the *manifest's* probe, so the test
    patches that one function rather than injecting a fake prober -- an injected
    one would still pass if `build` forgot to probe at all.
    """

    def __enter__(self):
        import report_fast.verify as verify

        self._verify = verify
        self._original = verify.probe
        # 200 is what `Check.ok` reads; the field is the HTTP status, not a bool.
        verify.probe = lambda ids, endpoint: [
            Check(data_id=data_id, status=200) for data_id in ids
        ]
        return self

    def __exit__(self, *exception):
        self._verify.probe = self._original


if __name__ == "__main__":
    import traceback

    failures = 0
    for name, function in sorted(list(globals().items())):
        if not name.startswith("test_") or not callable(function):
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                if function.__code__.co_argcount:
                    function(None)
                else:
                    function()
        except Exception:
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
