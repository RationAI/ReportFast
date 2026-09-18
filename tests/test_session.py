"""Tests for `report_fast.session` -- the session value object.

Run:      cd /home/jovyan/report_fast && python tests/test_session.py
Pytest:   pytest tests/test_session.py        # functions are plain asserts too

These assert the two contract halves: a config that goes in comes back out
unchanged (`from_config` -> `to_config`), and a config that is grown or joined
keeps its index references pointing at what they pointed at. Both are checked
against the fixtures in `examples/`, which are sessions this tool did not write.

The assertions quote xOpat 3.1.0, not this package:

  * `src/parse-input.js`   -- only `background[].dataReference` is hard-validated
  * `src/app.ts`           -- unknown `params` keys are dropped silently
  * `src/types/app.d.ts`   -- `DataOverride`, `visualizationIndex: null`
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_fast.config import (  # noqa: E402
    BUILTIN_PRESET,
    SessionPreset,
    load_preset,
    parse_preset,
)
from report_fast.session import (  # noqa: E402
    SessionTemplate,
    XopatSession,
    as_session,
    sessions_from_folder,
    sessions_from_paths,
)
from report_fast.xopat import (  # noqa: E402
    XopatEndpoint,
    XopatError,
    build_session,
    viewer_url,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SLIDE = "/mnt/data/slides/slide_001.tif"
OVERLAY = "/mnt/data/predictions/prob_001.tif"
BASE = "https://xopat.example.org/v3/"
TILES = "https://tiles.example.org/wsi-service/"


class _Raises:
    """Minimal `pytest.raises` stand-in, so the file runs without pytest."""

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


def endpoint(**overrides):
    values = {"base_url": BASE, "wsi_base_url": TILES, "mount_root": "/mnt"}
    values.update(overrides)
    return XopatEndpoint(**values)


def fixture(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


# ── the paste path ──────────────────────────────────────────────────────────


def test_viewer_export_roundtrips_verbatim():
    config = fixture("viewer_export.json")
    session = XopatSession.from_config(config)
    assert session.to_config() == session.to_config()
    assert (
        XopatSession.from_config(session.to_config()).to_config() == session.to_config()
    )


def test_import_keeps_fields_the_tool_does_not_model():
    session = XopatSession.from_config(fixture("viewer_export.json"))
    background = session.background[0]
    assert background["lossless"] is False, (
        "v2 key the viewer ignores; still not ours to delete"
    )
    assert background["visualizationIndex"] is None, (
        "null means 'no overlay', not 'index 0'"
    )
    assert background["id"] == "QcFKATZIt-Js"
    assert background["shaders"][0]["cache"]["use_channel0"] == "rgba"
    assert session.plugins == {"slide-info": {}}
    assert session.data[3] == {
        "dataID": "/data/Public/Public/breast/BreastCancerSummerSchool/Slides/O16-11870.tiff",
        "protocol": "iipimage",
    }


def test_import_drops_navigation_state_by_default():
    session = XopatSession.from_config(fixture("viewer_export.json"))
    assert "viewport" not in session.params
    assert "activeBackgroundIndex" not in session.params
    assert session.params["theme"] == "auto", (
        "a look-and-feel choice is configuration, not state"
    )

    kept = XopatSession.from_config(fixture("viewer_export.json"), drop_state=False)
    assert kept.params["viewport"]["zoomLevel"] == 1.0
    assert kept.params["activeBackgroundIndex"] == [0]


def test_import_keeps_where_the_author_was_when_asked():
    session = XopatSession.from_config(fixture("viewer_export.json"), drop_state=False)
    assert session.params["bypassCacheLoadTime"] is True


def test_unknown_params_are_kept_and_silent():
    # There is no allowlist to be unknown against. A key the viewer has not
    # heard of is dropped by the viewer, in the browser, where the user is
    # looking; the library's job is to not lose it on the way there.
    config = {
        "params": {"theme": "dark", "totallyMadeUp": True},
        "data": [],
        "background": [],
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session = XopatSession.from_config(config)
    assert not caught, "nothing is judged, so nothing is announced"
    assert session.params == {"theme": "dark", "totallyMadeUp": True}


def test_unknown_top_level_keys_are_kept():
    config = {
        "data": ["a.tif"],
        "background": [{"dataReference": 0}],
        "goals": [{"x": 1}],
    }
    session = XopatSession.from_config(config)
    again = XopatSession.from_config(session.to_config()).to_config()
    assert session.to_config()["goals"] == [{"x": 1}]
    assert again == session.to_config(), "a kept field survives a second import"


def test_import_does_not_mutate_the_callers_config():
    config = fixture("multi_background_case.json")
    before = copy.deepcopy(config)
    session = XopatSession.from_config(config)
    session.data.append("extra.tif")
    session.params["theme"] = "dark"
    session.background[0]["name"] = "changed"
    assert config == before


def test_import_of_json_text_and_dict_agree():
    text = (EXAMPLES / "multi_background_case.json").read_text(encoding="utf-8")
    assert XopatSession.from_config(text) == XopatSession.from_config(json.loads(text))


def test_import_rejects_a_dangling_background_reference():
    # The one thing the paste path does not soften. Dropping a colleague's
    # `params` key would be rude; shipping a page that does not boot would be a
    # lie, and `src/parse-input.js` hard-fails here rather than carrying on.
    config = {"data": ["a.tif"], "background": [{"dataReference": 4}]}
    with raises(XopatError) as raised:
        XopatSession.from_config(config)
    assert "background[0].dataReference" in str(raised.exception), (
        "the message names the JSON path, which is the whole unit of repair"
    )


def test_dangling_shader_reference_is_caught_before_the_link_ships():
    # An out-of-range `dataReferences` binds no overlay: the layer silently
    # renders its defaults over the wrong slide, which is the failure a reader
    # of the report cannot see.
    config = {
        "data": ["a.tif"],
        "background": [{"dataReference": 0, "visualizationIndex": 0}],
        "visualizations": [
            {"shaders": {"l": {"type": "heatmap", "dataReferences": [7]}}}
        ],
    }
    with raises(XopatError) as raised:
        XopatSession.from_config(config)
    assert "dataReferences" in str(raised.exception)


def test_multi_background_case_imports_intact():
    session = XopatSession.from_config(fixture("multi_background_case.json"))
    assert len(session.data) == 7
    assert len(session.background) == 3
    assert len(session.visualizations) == 3
    assert session.names == ["Plan", "Follow-up 1", "Follow-up 2"]
    assert [entry["id"] for entry in session.background] == ["plan", "fu1", "fu2"]
    assert [entry["dataReference"] for entry in session.background] == [0, 2, 4]
    assert session.visualizations[0]["order"] == ["dose", "rtstruct"], (
        "authored shader keys must survive"
    )
    assert sorted(session.visualizations[0]["shaders"]) == ["dose", "rtstruct"]


# ── from_url ────────────────────────────────────────────────────────────────


def test_from_url_recovers_the_session_and_the_origin():
    original = XopatSession.from_slide(SLIDE, name="Slide 1", endpoint=endpoint())
    session = XopatSession.from_url(original.url())
    assert session == original
    assert session.endpoint.base_url == BASE


def test_from_url_reads_the_legacy_query_form():
    session = XopatSession.from_slide(SLIDE, endpoint=endpoint())
    import urllib.parse

    url = f"{BASE}?visualization={urllib.parse.quote(session.to_json())}"
    assert XopatSession.from_url(url).to_config() == session.to_config()


def test_from_url_without_a_session_says_so():
    with raises(XopatError):
        XopatSession.from_url("https://xopat.example.org/v3/")


# ── building ────────────────────────────────────────────────────────────────


def test_from_slide_matches_build_session():
    session = XopatSession.from_slide(
        SLIDE,
        [{"path": OVERLAY, "type": "heatmap", "name": "Probability"}],
        name="Slide 1",
        endpoint=endpoint(),
    )
    assert session.to_config() == build_session(
        SLIDE,
        [{"path": OVERLAY, "type": "heatmap", "name": "Probability"}],
        name="Slide 1",
        endpoint=endpoint(),
    )


def test_builtin_defaults_emit_no_params():
    session = XopatSession.from_slide(SLIDE, endpoint=endpoint())
    assert session.to_config()["params"] == {}, (
        "every emitted param is bytes on every link"
    )
    assert BUILTIN_PRESET.endpoint is None, "the builtin preset pins no deployment"
    assert session.endpoint == endpoint()


def test_background_for_follows_the_active_slot_selection():
    session = XopatSession.from_config(fixture("viewer_export.json"), drop_state=False)
    assert session.background_for(0) is session.background[0]
    nothing_open = XopatSession.from_config(
        {
            "params": {"activeBackgroundIndex": []},
            "data": ["a.tif"],
            "background": [{"dataReference": 0}],
        }
    )
    assert nothing_open.background_for(0) is nothing_open.background[0]


def test_add_data_returns_the_index_it_appended():
    session = XopatSession(endpoint=endpoint())
    first = session.add_data(SLIDE)
    second = session.add_data(OVERLAY, lossless=True)
    assert (first, second) == (0, 1)
    assert session.data[0] == SLIDE.removeprefix("/mnt/")
    assert session.data[1] == {
        "dataID": "data/predictions/prob_001.tif",
        "options": {"format": "png"},
    }


def test_add_background_returns_data_index_and_mounts_a_background():
    session = XopatSession(endpoint=endpoint())
    session.add_visualization("Viz")
    index = session.add_background(SLIDE, name="Slide 1", visualization_index=0)
    assert index == 0
    assert session.background == [
        {"dataReference": 0, "name": "Slide 1", "visualizationIndex": 0}
    ]


def test_a_layer_can_carry_its_reader_and_sampling():
    session = XopatSession.from_slide(
        SLIDE,
        [
            {
                "path": OVERLAY,
                "type": "colormap",
                "options": {"plugin": "tifffile", "channels": "all"},
                "smoothing": False,
            }
        ],
        endpoint=endpoint(),
    )
    assert session.data[1] == {
        "dataID": "data/predictions/prob_001.tif",
        "options": {"plugin": "tifffile", "channels": "all", "format": "png"},
        "imageSmoothingEnabled": False,
    }


def test_a_background_can_carry_pixel_size():
    session = XopatSession.from_slide(SLIDE, endpoint=endpoint())
    session.add_background("/mnt/data/slides/other.tif", microns=0.25)
    assert session.data[1] == {"dataID": "data/slides/other.tif", "microns": 0.25}


def test_add_layer_needs_a_visualization_to_attach_to():
    session = XopatSession(endpoint=endpoint())
    with raises(XopatError):
        session.add_layer({"path": OVERLAY})


def test_url_and_thumbnail_come_from_the_same_endpoint():
    session = XopatSession.from_slide(SLIDE, name="Slide 1", endpoint=endpoint())
    assert session.url().startswith(BASE.rstrip("/") + "/#%7B")
    assert session.url() == viewer_url(session.to_config(), endpoint())
    assert session.thumbnail() == (
        f"{TILES.rstrip('/')}/v3/slides/thumbnail/max_size/500/500"
        "?slide_id=data%2Fslides%2Fslide_001.tif"
    )
    assert session.fragment() == session.url().split("#", 1)[1]


def test_names_fall_back_to_the_data_id_stem():
    session = XopatSession.from_slide(SLIDE, endpoint=endpoint())
    assert session.names == ["slide_001"]


# ── joining sessions ────────────────────────────────────────────────────────


def test_merge_renumbers_every_reference():
    plan = XopatSession.from_slide(
        "/mnt/case/plan.tif",
        [{"path": "/mnt/case/dose.tif"}],
        name="Plan",
        endpoint=endpoint(),
    )
    followup = XopatSession.from_slide(
        "/mnt/case/fu.tif",
        [{"path": "/mnt/case/fu_dose.tif"}],
        name="Follow-up",
        endpoint=endpoint(),
    )
    merged = plan.merge(followup)

    assert merged.data_ids == [
        "case/plan.tif",
        "case/dose.tif",
        "case/fu.tif",
        "case/fu_dose.tif",
    ]
    assert [entry["dataReference"] for entry in merged.background] == [0, 2]
    assert [entry["visualizationIndex"] for entry in merged.background] == [0, 1]
    assert merged.visualizations[0]["shaders"]["layer_shader_0"]["dataReferences"] == [
        1
    ]
    assert merged.visualizations[1]["shaders"]["layer_shader_0"]["dataReferences"] == [
        3
    ]
    assert len(merged.data) == len(plan.data) + len(followup.data)


def test_merge_does_not_disturb_the_originals():
    first = XopatSession.from_slide("/mnt/a/s1.tif", endpoint=endpoint())
    second = XopatSession.from_slide("/mnt/b/s2.tif", endpoint=endpoint())
    first.merge(second)
    assert first.data == ["a/s1.tif"]
    assert second.data == ["b/s2.tif"]


def test_merge_keeps_the_first_param_on_conflict():
    first = XopatSession.from_slide(
        "/mnt/a/s1.tif", params={"theme": "dark"}, endpoint=endpoint()
    )
    second = XopatSession.from_slide(
        "/mnt/b/s2.tif", params={"theme": "light"}, endpoint=endpoint()
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        merged = first.merge(second)
    assert merged.params["theme"] == "dark"
    assert any("theme" in str(warning.message) for warning in caught)


def test_merge_all_joins_many():
    sessions = [
        XopatSession.from_slide(f"/mnt/case/s{i}.tif", endpoint=endpoint())
        for i in range(3)
    ]
    merged = XopatSession.merge_all(sessions)
    assert merged.data == ["case/s0.tif", "case/s1.tif", "case/s2.tif"]
    assert [entry["dataReference"] for entry in merged.background] == [0, 1, 2]


# ── templates ───────────────────────────────────────────────────────────────


def test_template_binds_by_index_and_keeps_the_entrys_config():
    config = fixture("multi_background_case.json")
    template = SessionTemplate.from_config(config, slots={0: "mr", 1: "structures"})
    session = template.bind(
        mr="/mnt/rationai/mmci/NU22/case/new_mr.nii",
        structures="/mnt/rationai/mmci/NU22/case/new_rtstruct.nii",
        name="New case",
    )
    assert session.data[0] == {
        **config["data"][0],
        "dataID": "rationai/mmci/NU22/case/new_mr.nii",
    }, "protocol and options belong to the template, the path to the caller"
    assert session.data[1]["imageSmoothingEnabled"] is False
    assert session.data[1]["dataID"] == "rationai/mmci/NU22/case/new_rtstruct.nii"
    assert session.background[0]["name"] == "New case"
    assert session.names[0] == "New case"


def test_template_works_on_a_pasted_config_without_placeholders():
    session = XopatSession.from_slide(
        SLIDE, [{"path": OVERLAY}], name="Template", endpoint=endpoint()
    )
    template = SessionTemplate.from_config(session.to_config(), endpoint=endpoint())
    bound = template.bind(slide="/mnt/data/slides/other.tif")
    assert bound.data[0] == "data/slides/other.tif"
    assert bound.data[1] == {
        "dataID": "data/predictions/prob_001.tif",
        "options": {"format": "png"},
    }, "the template's own overlay stays"
    assert bound.background[0]["name"] == "Template"


def test_template_bare_data_ids_stay_bare():
    config = {"data": ["someone/else/slide.tif"], "background": [{"dataReference": 0}]}
    template = SessionTemplate.from_config(config, endpoint=endpoint())
    bound = template.bind(slide="mine/slide.tif")
    assert bound.data[0] == "mine/slide.tif"


def test_template_requires_every_slot():
    template = SessionTemplate.from_config(
        fixture("multi_background_case.json"), slots={0: "mr", 1: "structures"}
    )
    with raises(XopatError):
        template.bind(mr="/mnt/x/a.nii")
    partial = template.bind(mr="/mnt/x/a.nii", partial=True)
    assert partial.data[1] == fixture("multi_background_case.json")["data"][1]


def test_template_rejects_slots_it_does_not_have():
    template = SessionTemplate.from_config(fixture("multi_background_case.json"))
    with raises(XopatError):
        template.bind(slide="/mnt/x/a.nii", mask="/mnt/x/b.nii")
    with raises(XopatError):
        SessionTemplate.from_config(
            fixture("multi_background_case.json"), slots={99: "mr"}
        )


# ── folders ─────────────────────────────────────────────────────────────────


def test_sessions_from_folder_scans_names_and_links():
    with tempfile.TemporaryDirectory() as folder:
        for name in ("b.tif", "a.tif", "notes.md"):
            Path(folder, name).write_text("", encoding="utf-8")
        sessions = sessions_from_folder(folder, endpoint=endpoint())
    assert [session.names[0] for session in sessions] == ["a", "b"]
    assert sessions[0].url().startswith(BASE)


def test_sessions_from_folder_can_add_per_slide_masks():
    with tempfile.TemporaryDirectory() as folder:
        for name in ("slide_a.tif", "slide_b.tif"):
            Path(folder, name).write_text("", encoding="utf-8")
        sessions = sessions_from_folder(
            folder,
            endpoint=endpoint(mount_root=folder),
            layers_for=lambda slide: [{"path": f"{slide}.prob.tif", "type": "heatmap"}],
        )
    assert [len(session.data) for session in sessions] == [2, 2]
    assert sessions[0].data[1] == {
        "dataID": "slide_a.tif.prob.tif",
        "options": {"format": "png"},
    }


def test_sessions_from_folder_accepts_a_template():
    with tempfile.TemporaryDirectory() as folder:
        for name in ("a.tif", "b.tif"):
            Path(folder, name).write_text("", encoding="utf-8")
        template = SessionTemplate.from_config(
            {
                "data": ["placeholder.tif", "placeholder_prob.tif"],
                "background": [
                    {"dataReference": 0, "visualizationIndex": 0, "name": "T"}
                ],
                "visualizations": [
                    {
                        "name": "T",
                        "shaders": {"l": {"type": "heatmap", "dataReferences": [1]}},
                    }
                ],
            },
            slots={0: "slide"},
            endpoint=endpoint(mount_root=folder),
        )
        sessions = sessions_from_folder(folder, template=template)
    assert [session.data[0] for session in sessions] == ["a.tif", "b.tif"]
    assert sessions[0].visualizations[0]["shaders"]["l"]["dataReferences"] == [1]


def test_sessions_from_folder_is_strict_about_the_path():
    with raises(XopatError):
        sessions_from_folder("/definitely/not/here", endpoint=endpoint())


def test_sessions_from_folder_warns_when_nothing_matches():
    with tempfile.TemporaryDirectory() as folder:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sessions = sessions_from_folder(folder, endpoint=endpoint())
    assert sessions == []
    assert any("No slides matched" in str(warning.message) for warning in caught)


def test_sessions_from_paths_keeps_order_and_labels():
    sessions = sessions_from_paths(
        ["/mnt/x/s2.tif", "/mnt/x/s1.tif"],
        masks=[{"path": "/mnt/x/mask.tif"}],
        endpoint=endpoint(),
    )
    assert [session.names[0] for session in sessions] == ["s2", "s1"]
    assert sessions[0].data[1] == {"dataID": "x/mask.tif", "options": {"format": "png"}}


# ── as_session: one door for every shape a caller might arrive with ─────────


def test_as_session_accepts_every_shape_a_caller_might_have():
    built = XopatSession.from_slide(
        SLIDE, [{"path": OVERLAY}], name="s1", endpoint=endpoint()
    )
    for shape in (built, built.to_config(), built.to_json(), built.url(), SLIDE):
        session = as_session(shape, endpoint=endpoint())
        assert session.data_ids[0] == "data/slides/slide_001.tif", shape
        assert session.endpoint_for().base_url == BASE


def test_as_session_never_reaches_back_into_what_it_was_given():
    built = XopatSession.from_slide(
        SLIDE, [{"path": OVERLAY}], name="s1", endpoint=endpoint()
    )
    before = built.to_config()
    derived = as_session(
        built,
        name="relabeled",
        params={"theme": "dark"},
        endpoint=endpoint(),
    )
    derived.add_layer("/mnt/data/masks/other.tif")
    derived.bind_name("again")
    assert built.to_config() == before, "a derived session is a copy, not a handle"


def test_as_session_labels_a_pasted_session_without_rewriting_it():
    pasted = fixture("viewer_export.json")
    session = as_session(pasted, name="case_042", endpoint=endpoint())
    config = session.to_config()
    assert session.names == ["case_042"]
    assert config["data"] == pasted["data"], "renaming must not touch the data pool"
    assert config["background"][0]["id"] == pasted["background"][0]["id"]
    assert config["background"][0]["shaders"] == pasted["background"][0]["shaders"]


def test_as_session_renames_the_visualization_a_background_opens():
    built = XopatSession.from_slide(
        SLIDE, [{"path": OVERLAY}], name="s1", endpoint=endpoint()
    )
    assert (
        as_session(built, name="Relabeled", endpoint=endpoint()).visualizations[0][
            "name"
        ]
        == "Relabeled"
    )


def test_as_session_treats_a_bare_path_like_the_builders_do():
    assert (
        as_session(SLIDE, [{"path": OVERLAY}], endpoint=endpoint()).to_config()
        == XopatSession.from_slide(
            SLIDE, [{"path": OVERLAY}], endpoint=endpoint()
        ).to_config()
    )


# ── presets ─────────────────────────────────────────────────────────────────


def test_preset_supplies_defaults_the_caller_can_override():
    preset = {
        "params": {"theme": "dark", "ui": {"toolBar": False}},
        "protocol": "iipimage",
    }
    session = XopatSession.from_slide(SLIDE, endpoint=endpoint(), preset=preset)
    assert session.params == {"theme": "dark", "ui": {"toolBar": False}}
    assert session.data[0] == {
        "dataID": "data/slides/slide_001.tif",
        "protocol": "iipimage",
    }

    override = XopatSession.from_slide(
        SLIDE,
        endpoint=endpoint(),
        preset=preset,
        params={"theme": "light"},
        protocol="dzi",
    )
    assert override.params["theme"] == "light"
    assert override.params["ui"] == {"toolBar": False}
    assert override.data[0]["protocol"] == "dzi"


def test_preset_default_layers_apply_when_the_caller_passes_none():
    preset = {"layers": [{"path": OVERLAY, "type": "heatmap", "name": "Probability"}]}
    session = XopatSession.from_slide(SLIDE, endpoint=endpoint(), preset=preset)
    assert len(session.data) == 2
    explicit = XopatSession.from_slide(SLIDE, [], endpoint=endpoint(), preset=preset)
    assert len(explicit.data) == 2, "an empty layer list is a request for none"


def test_preset_refuses_the_lists_it_would_rewire():
    for key in ("data", "background", "visualizations"):
        with raises(XopatError):
            parse_preset({key: [{}]})


def test_preset_refuses_keys_the_viewer_would_not_read():
    with raises(XopatError):
        parse_preset({"parems": {"theme": "dark"}})


def test_preset_file_is_read_from_disk_and_the_environment():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder, "session.json")
        path.write_text(json.dumps({"params": {"theme": "dark"}}), encoding="utf-8")
        assert load_preset(path).params == {"theme": "dark"}

        os.environ["XOPAT_SESSION_CONFIG"] = str(path)
        try:
            session = XopatSession.from_slide(SLIDE, endpoint=endpoint())
            assert session.params == {"theme": "dark"}
        finally:
            del os.environ["XOPAT_SESSION_CONFIG"]
    assert load_preset() == BUILTIN_PRESET


def test_preset_can_pin_the_deployment():
    preset = parse_preset(
        {"base_url": BASE, "wsi_base_url": TILES, "image_protocol": "wsi"}
    )
    session = XopatSession.from_slide(SLIDE, preset=preset)
    assert session.endpoint.wsi_base_url == TILES
    assert session.data[0]["protocol"] == "wsi"
    assert session.url().startswith(BASE)


def test_preset_missing_file_is_an_error_not_a_shrug():
    with raises(XopatError):
        load_preset("/nope/session.json")


def test_session_preset_merge_prefers_the_later():
    merged = BUILTIN_PRESET.merge(
        SessionPreset(params={"theme": "dark"}, protocol="dzi")
    )
    assert merged.lossless is True
    assert merged.params == {"theme": "dark"}
    assert merged.protocol == "dzi"


# ── runner ──────────────────────────────────────────────────────────────────


def main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for name, test in tests:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                test()
        except Exception as exc:  # noqa: BLE001 - the runner reports, it does not handle
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
