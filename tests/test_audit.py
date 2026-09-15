"""Tests for `report_fast.audit` -- the walk, and the verdict put on it.

Run:      cd /home/jovyan/report_fast && python tests/test_audit.py
Pytest:   pytest tests/test_audit.py

Three things are worth pinning, because each is a decision rather than an
observation:

  * **the path**, not just the problem. A finding that cannot be located cannot
    be fixed, and "unknown param `threshold`" across a 30-slide session is
    unusable. Every finding is asserted with the bracket path the viewer's own
    console warnings use.
  * **the severity split.** `reference`/`structure`/`layer` are hard on both
    paths; `param`/`key` are hard only where the session is ours. Both directions
    are asserted -- a soft finding must not become hard on a paste, and a hard one
    must not go soft on an agent.
  * **what the fixtures are expected to be wrong about.** `examples/` carries
    stale keys on purpose (README: "the shapes the tool must survive"), so the
    findings they produce are part of the fixture's contract. If the viewer stops
    disagreeing with them, that is worth knowing too.

The assertions quote xOpat 3.1.0 (`18c94f2b`), pinned in
`report_fast/schema/viewer.lock.json`:

  * `src/app.ts`           -- `sanitizeAgainst` drops unknown params, logs, carries on
  * `src/parse-input.js`   -- only `background[].dataReference` is hard-validated
  * `src/classes/shader-layer.js` -- a layer binds only params it declares
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_fast.audit import (  # noqa: E402
    ERROR_KINDS,
    SOFT_KINDS,
    Finding,
    audit,
    join,
    split,
)
from report_fast.session import SessionTemplate, XopatSession  # noqa: E402
from report_fast.shader import ShaderConfig  # noqa: E402
from report_fast.xopat import XopatError  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

#: The smallest session that loads: one data entry, one background pointing at it.
MINIMAL = {"data": ["/slides/case.tif"], "background": [{"dataReference": 0}]}


def fixture(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def paths(findings) -> list:
    return [finding.path for finding in findings]


def kinds(findings) -> set:
    return {finding.kind for finding in findings}


def one(config, **kwargs) -> Finding:
    """The single finding `config` produces; asserts there is exactly one."""
    found = audit(config, **kwargs)
    assert len(found) == 1, f"expected one finding, got {[f.text for f in found]}"
    return found[0]


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


# ── the walk reports, and only reports ──────────────────────────────────────


def test_a_clean_session_reports_nothing():
    assert audit(MINIMAL) == []
    assert audit(
        {
            "params": {"theme": "dark", "ui": {"toolBar": False}},
            "data": ["/s/a.tif", {"dataID": "/s/b.tif", "options": {"format": "png"}}],
            "background": [{"dataReference": 0, "visualizationIndex": 0}],
            "visualizations": [
                {
                    "name": "v",
                    "shaders": {
                        "heat": {
                            "type": "heatmap",
                            "dataReferences": [1],
                            "params": {"threshold": 0.5, "use_channel0": "g"},
                        }
                    },
                }
            ],
            "plugins": {"slide-info": {}},
        }
    ) == []


def test_audit_never_raises_however_broken_the_input():
    # The walk answers questions for JSON that has not survived coercion yet --
    # the CLI's door hands it exactly this -- so raising on a weird shape would
    # move the crash from the browser to a traceback with no path in it.
    for broken in (None, [], "text", 7, {"data": "not-a-list"}, {"background": {}},
                   {"visualizations": [{"shaders": []}]},
                   {"visualizations": [{"shaders": {"x": 4}}]}):
        found = audit(broken)
        assert found, f"{broken!r} produced no finding at all"
        assert all(isinstance(finding, Finding) for finding in found)
    assert audit("text")[0].kind == "structure"


def test_every_finding_carries_a_locatable_path():
    config = {
        "params": {"nope": 1},
        "data": [],
        "background": [{"dataReference": 9}],
        "visualizations": [{"shaders": {"l": {"type": "nope", "params": {"x": 1}}}}],
    }
    found = audit(config)
    assert set(paths(found)) == {
        "params.nope",
        "background[0].dataReference",
        "visualizations[0].shaders.l.type",
    }, "a layer with an unregistered type is not also accused of its params"
    for finding in found:
        assert finding.path, "a finding nobody can locate is not a finding"
        assert finding.message


# ── references: hard, on both paths ─────────────────────────────────────────


def test_a_dangling_data_reference_is_named_exactly():
    finding = one({"data": ["a.tif"], "background": [{"dataReference": 4}]})
    assert finding.path == "background[0].dataReference"
    assert finding.kind == "reference"
    assert "0..0" in finding.message, "the legal range is stated, not implied"


def test_a_background_without_a_reference_is_a_structure_error():
    finding = one({"data": ["a.tif"], "background": [{}]})
    assert finding.path == "background[0].dataReference"
    assert finding.kind == "structure"


def test_a_shader_bound_to_nothing_is_caught_even_though_the_viewer_renders_it():
    # The worst case in the whole format: the page loads, the layer draws, and
    # what it draws is the shader's defaults rather than anyone's prediction.
    config = {
        "data": ["a.tif"],
        "background": [{"dataReference": 0, "visualizationIndex": 0}],
        "visualizations": [{"shaders": {"l": {"type": "heatmap", "dataReferences": [7]}}}],
    }
    finding = one(config)
    assert finding.path == "visualizations[0].shaders.l.dataReferences"
    assert finding.kind == "reference"
    assert "renders its defaults" in finding.message


def test_a_layer_reference_may_also_be_nested_in_a_group():
    config = {
        "data": ["a.tif"],
        "background": [{"dataReference": 0, "visualizationIndex": 0}],
        "visualizations": [
            {
                "shaders": {
                    "group": {
                        "type": "group",
                        "shaders": {"inner": {"type": "heatmap", "dataReferences": [3]}},
                    }
                }
            }
        ],
    }
    finding = one(config)
    assert finding.path == "visualizations[0].shaders.group.shaders.inner.dataReferences"


def test_a_visualization_index_outside_the_list_is_a_reference_error():
    finding = one(
        {
            "data": ["a.tif"],
            "background": [{"dataReference": 0, "visualizationIndex": 2}],
            "visualizations": [{"shaders": {}}],
        }
    )
    assert finding.path == "background[0].visualizationIndex"
    assert finding.kind == "reference"
    # `null` means "no overlay", which is legal and must not be flagged.
    assert audit(
        {"data": ["a.tif"], "background": [{"dataReference": 0, "visualizationIndex": None}]}
    ) == []


# ── params: soft, with the destination named ────────────────────────────────


def test_a_flat_ui_alias_is_told_where_it_belongs():
    finding = one({"params": {"navigator": False}})
    assert finding.path == "params.navigator"
    assert finding.kind == "param"
    assert "nest it as params.ui.navigator" in finding.message, (
        "`getUiOption` has a flat fallback that looks like it honors this; the "
        "message has to explain why it does not, or it gets argued with"
    )


def test_the_surviving_flat_aliases_are_not_flagged():
    # scaleBar/statusBar/toolBar ARE top-level XOpatSetup keys, so flat is legal.
    assert audit({"params": {"toolBar": False, "scaleBar": True, "statusBar": False}}) == []


def test_a_ui_child_has_to_stay_inside_ui():
    finding = one({"params": {"ui": {"zoomButton": True}}})
    assert finding.path == "params.ui.zoomButton"
    assert finding.kind == "param"


def test_an_undeclared_layer_param_lists_what_is_declared():
    config = {
        "data": ["a.tif"],
        "background": [{"dataReference": 0, "visualizationIndex": 0}],
        "visualizations": [{"shaders": {"l": {"type": "colormap", "params": {"inverse": True}}}}],
    }
    finding = one(config)
    assert finding.path == "visualizations[0].shaders.l.params.inverse"
    assert finding.kind == "param"
    assert "['color', 'connect', 'opacity', 'threshold']" in finding.message


def test_filter_flags_and_generated_control_families_are_not_findings():
    # `_buildControls` injects opacity, skips `use_*`, and expands `array:`
    # definitions into icon0..iconN -- all three look undeclared to a naive check.
    assert audit(
        {
            "data": ["a.tif"],
            "background": [{"dataReference": 0, "visualizationIndex": 0}],
            "visualizations": [
                {
                    "shaders": {
                        "icons": {
                            "type": "iconmap",
                            "params": {"icon0": "a", "icon17": "b", "use_channel0": "g"},
                        }
                    }
                }
            ],
        }
    ) == []


def test_an_unregistered_layer_type_is_hard_but_leaves_its_params_alone():
    config = {
        "data": ["a.tif"],
        "background": [{"dataReference": 0, "visualizationIndex": 0}],
        "visualizations": [{"shaders": {"l": {"type": "colormap2", "params": {"x": 1}}}}],
    }
    found = audit(config)
    assert paths(found) == ["visualizations[0].shaders.l.type"]
    assert found[0].kind == "layer"


def test_a_layer_with_no_type_is_reported_rather_than_guessed():
    finding = one(
        {
            "data": ["a.tif"],
            "background": [{"dataReference": 0, "visualizationIndex": 0}],
            "visualizations": [{"shaders": {"l": {"dataReferences": [0]}}}],
        }
    )
    assert finding.path == "visualizations[0].shaders.l.type"
    assert finding.kind == "structure"


def test_a_v2_shaders_array_is_named_as_the_shape_it_is():
    finding = one(
        {
            "data": ["a.tif"],
            "background": [{"dataReference": 0, "visualizationIndex": 0}],
            "visualizations": [{"shaders": [{"type": "heatmap"}]}],
        }
    )
    assert finding.path == "visualizations[0].shaders"
    assert "keyed by layer id" in finding.message


def test_an_inline_protocol_template_is_refused_before_the_viewer_sees_it():
    found = audit(
        {
            "data": [{"dataID": "a.tif", "protocol": "`https://x/${y}`"}],
            "background": [{"dataReference": 0, "protocol": "`https://x/`"}],
        }
    )
    assert paths(found) == ["data[0].protocol", "background[0].protocol"]
    assert kinds(found) == {"structure"}
    assert "RCE" in found[0].message
    # A registered protocol name, however odd, is not a template.
    assert audit({"data": [{"dataID": "a.tif", "protocol": "iipimage"}], "background": []}) == []


def test_a_data_entry_without_a_dataid_is_structural():
    finding = one({"data": [{"options": {"format": "png"}}], "background": []})
    assert finding.path == "data[0]"
    assert finding.kind == "structure"


# ── the verdict ─────────────────────────────────────────────────────────────


def test_the_kind_split_is_exactly_the_ruling():
    assert ERROR_KINDS == frozenset({"reference", "structure", "layer"})
    assert SOFT_KINDS == frozenset({"param", "key"})
    assert not ERROR_KINDS & SOFT_KINDS


def test_a_soft_finding_is_an_error_strict_and_a_warning_otherwise():
    findings = audit({"params": {"totallyMadeUp": True}})
    strict_errors, strict_warnings = split(findings, strict=True)
    loose_errors, loose_warnings = split(findings, strict=False)
    assert strict_errors == findings and strict_warnings == []
    assert loose_errors == [] and loose_warnings == findings


def test_a_hard_finding_stays_hard_on_the_loose_path():
    findings = audit({"data": [], "background": [{"dataReference": 0}]})
    errors, warnings_ = split(findings, strict=False)
    assert errors == findings and warnings_ == [], (
        "there is no path on which a session that cannot boot is a warning"
    )


def test_join_keeps_the_report_readable():
    many = [Finding(f"params.k{i}", "param", "bad") for i in range(40)]
    text = join(many)
    assert "and 28 more" in text
    assert text.count("params.k") == 12


# ── validate() is the audit with a verdict ─────────────────────────────────


def test_an_authored_session_is_held_to_the_letter():
    from report_fast.xopat import XopatEndpoint

    endpoint = XopatEndpoint(base_url="https://v.test/v3/", mount_root="/mnt")
    session = XopatSession.from_slide("/mnt/s.tif", name="s", endpoint=endpoint)
    session.params["totallyMadeUp"] = True
    with raises(XopatError) as raised:
        session.validate()
    assert "params.totallyMadeUp" in str(raised.exception)
    assert session.authoritative


def test_a_pasted_session_warns_and_keeps():
    config = dict(MINIMAL, params={"totallyMadeUp": True})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session = XopatSession.from_config(config)
    assert session.params["totallyMadeUp"] is True
    assert any("params.totallyMadeUp" in str(w.message) for w in caught)
    assert not session.authoritative


def test_authoritative_travels_with_a_copy_but_not_with_a_paste():
    session = XopatSession.from_config(MINIMAL)
    assert not session.copy().authoritative
    assert not XopatSession.from_config(session.to_config()).authoritative


def test_a_binding_never_promotes_a_pasted_template():
    # The bug this rules out: a template passes `from_config` with a warning and
    # then the 3rd of 300 bindings raises on the same key, so a report fails
    # partway through for a reason that was already accepted at the door.
    config = dict(MINIMAL, params={"totallyMadeUp": True})
    template = SessionTemplate.from_config(config, slots={0: "slide"})
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("ignore")
        bound = template.bind(slide="/mnt/other.tif")
    assert bound.params["totallyMadeUp"] is True, "bound, not refused"
    assert not bound.authoritative


def test_a_carried_param_stays_a_door_open_on_the_strict_path():
    # `with_params` says "the library has not modelled this, keep it". That is a
    # decision made where the intent was on the table, and it was warned about
    # once already; the strict gate must not turn it into an error no opt-out
    # reaches, or the documented door becomes a wall.
    layer = ShaderConfig(
        shader_type="heatmap", name="m", data_source="/mnt/m.tif"
    ).with_params({"some_v3_field": 7})
    session = XopatSession(authoritative=True)
    session.add_visualization("v")
    session.add_layer(layer, visualization=0, shader_id="m")
    assert session.visualizations[0]["shaders"]["m"]["params"]["some_v3_field"] == 7
    assert session.carried_paths() == [
        "visualizations[0].shaders.m.params.some_v3_field"
    ]
    session.validate()  # does not raise

    # ...and only that param. A mistake written beside it is still found.
    session.visualizations[0]["shaders"]["m"]["params"]["and_this_one"] = 1
    with raises(XopatError) as raised:
        session.validate()
    assert "and_this_one" in str(raised.exception)
    assert "some_v3_field" not in str(raised.exception)


def test_a_carried_param_survives_a_copy_and_a_merge_with_its_indices():
    def carried(field, slide, shader_id):
        session = XopatSession(authoritative=True)
        session.add_visualization("v")
        session.add_layer(
            ShaderConfig(
                shader_type="heatmap", name="l", data_source=slide
            ).with_params({field: 1}),
            visualization=0,
            shader_id=shader_id,
        )
        return session

    session = carried("some_v3_field", "/mnt/m.tif", "m")
    other = carried("other_field", "/mnt/n.tif", "n")
    merged = session.merge(other)
    assert "visualizations[0].shaders.m.params.some_v3_field" in merged.carried_paths()
    assert "visualizations[1].shaders.n.params.other_field" in merged.carried_paths(), (
        "renumbered with the visualization it came with"
    )
    merged.validate()


# ── the fixtures: what the shipped sessions are expected to be wrong about ──


def test_the_viewer_export_is_now_beyond_reproach():
    # Everything v2-about-it (`lossless`) sits on background entries, whose key
    # set the viewer declares open on purpose; `params` is clean.
    assert audit(fixture("viewer_export.json")) == []


def test_the_dysplasia_case_is_clean():
    assert audit(fixture("dysplasia_case.json")) == []


def test_the_multi_background_case_is_expected_to_carry_one_stale_param():
    """`inverse` on a `colormap` layer: invented by whoever wrote it, dropped
    silently by v3, and kept by us -- examples/README.md documents exactly that,
    and so does this test. If the viewer ever declares `inverse`, this fixture
    stops being a paste-path lesson and should be re-blessed."""
    found = audit(fixture("multi_background_case.json"))
    assert kinds(found) == {"param"}, [f.text for f in found]
    assert paths(found) == [
        f"visualizations[{index}].shaders.rtstruct.params.inverse" for index in (0, 1, 2)
    ]
    errors, notes = split(found, strict=False)
    assert errors == [] and len(notes) == 3


def test_a_pasted_fixture_warns_once_and_keeps_every_byte():
    config = fixture("multi_background_case.json")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session = XopatSession.from_config(config)
    assert session.to_config()["visualizations"][0]["shaders"]["rtstruct"]["params"][
        "inverse"
    ] is False, "kept exactly as authored, `false` and all"
    assert len(caught) == 1, "one warning for one session, not one per finding"
    assert "params.inverse" in str(caught[0].message)
    assert Path(caught[0].filename) == Path(__file__), (
        "the warning has to point at the line that pasted. A fixed stacklevel gets "
        "this wrong as soon as anything wraps from_config -- and four things do"
    )


def test_the_same_fixture_under_the_agent_gate_is_refused():
    # Handing an agent a warning it can ignore is how a fabricated key reaches a
    # patient-facing page. Same findings, different verdict.
    with raises(XopatError) as raised:
        XopatSession.from_config(
            fixture("multi_background_case.json"), strict=True
        )
    assert "params.inverse" in str(raised.exception)


def test_the_root_session_name_is_told_where_the_viewer_reads_it():
    finding = one(dict(MINIMAL, sessionName="whatever"))
    assert finding.path == "sessionName"
    assert "params.sessionName" in finding.message


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"ok  {name}")
