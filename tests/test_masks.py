"""Tests for `report_fast.masks` — backgrounds and masks, from a folder or a run.

The MLflow side runs against the double in `tests/test_mlflow.py`; the folder
side against a temporary directory. No mount, no server.

Run:      cd /home/jovyan/report_fast && python tests/test_masks.py
Pytest:   pytest tests/test_masks.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_fast import (  # noqa: E402
    Drive,
    Mask,
    MlflowRun,
    Report,
    SlideGrid,
    XopatEndpoint,
    sessions_from_masks,
)
from report_fast.shader import ShaderType  # noqa: E402

from test_mlflow import RUN, FakeClient, flow  # noqa: E402

ENDPOINT = XopatEndpoint(base_url="https://viewer.test/v3/", mount_root="/mnt")


def case(session):
    """The label the viewer shows for this session."""
    return session.background[0]["name"]


def shaders(session):
    return {s["name"]: s for s in session.visualizations[0]["shaders"].values()}


def data_ids(session):
    return [
        entry if isinstance(entry, str) else entry.get("dataID")
        for entry in session.data
    ]


def folder(slides=("case_001.tif", "case_002.tif"), masks=("case_001.tif",)):
    """A mounted-looking folder tree with the given slide and mask names."""
    root = Path(tempfile.mkdtemp())
    slides_dir = root / "slides"
    masks_dir = root / "masks"
    for names, directory in ((slides, slides_dir), (masks, masks_dir)):
        directory.mkdir(parents=True)
        for name in names:
            (directory / name).write_bytes(b"not really an image")
    return slides_dir, masks_dir


# ── the two sources ─────────────────────────────────────────────────────────


def test_a_folder_of_masks_lands_on_the_slide_with_the_same_stem():
    slides, masks = folder()
    sessions = sessions_from_masks(
        Drive(slides),
        [Mask("Tissue", Drive(masks), color="#ffff00", opacity=0.5)],
        endpoint=ENDPOINT,
    )
    assert [case(session) for session in sessions] == ["case_001", "case_002"]
    session = sessions[0]  # case_001, the one with a mask
    assert [Path(entry).name for entry in data_ids(session)] == [
        "case_001.tif",
        "case_001.tif",
    ]
    assert data_ids(session)[0].startswith(str(slides))
    layer = shaders(session)["Tissue"]
    assert layer["type"] == "heatmap"
    assert layer["dataReferences"] == [1]
    assert layer["params"]["color"] == "#ffff00"
    assert layer["params"]["opacity"] == 0.5


def test_a_run_of_masks_works_the_same_way():
    slides, _ = folder(masks=())
    sessions = sessions_from_masks(
        Drive(slides),
        [Mask("Heatmaps", MlflowRun(RUN, "masks"))],
        endpoint=ENDPOINT,
        flow=flow(FakeClient()),
    )
    assert [case(session) for session in sessions] == ["case_001", "case_002"]
    assert data_ids(sessions[0])[1].startswith("mflow/")
    assert len(sessions[1].data) == 1  # case_002 has no mask in run "masks"


def test_the_background_can_come_from_a_run_too():
    sessions = sessions_from_masks(
        MlflowRun(RUN, "slides"),
        [Mask("Heatmaps", MlflowRun(RUN, "masks"))],
        endpoint=ENDPOINT,
        flow=flow(FakeClient()),
    )
    assert [case(session) for session in sessions] == [
        "case_001",
        "case_002",
    ]


def test_a_list_of_slides_is_a_background_too():
    slides, masks = folder()
    sessions = sessions_from_masks(
        [slides / "case_002.tif", slides / "case_001.tif"],
        [Mask("Tissue", Drive(masks))],
        min_layers=0,
        endpoint=ENDPOINT,
    )
    assert [case(session) for session in sessions] == [
        "case_002",
        "case_001",
    ], "a listed background keeps the order it was listed in"


# ── selection ───────────────────────────────────────────────────────────────


def test_only_picks_the_cases_and_sets_their_order():
    slides, masks = folder(slides=("case_001.tif", "case_002.tif"))
    sessions = sessions_from_masks(
        Drive(slides),
        [Mask("Tissue", Drive(masks))],
        only=["case_001"],
        min_layers=0,
        endpoint=ENDPOINT,
    )
    assert [case(session) for session in sessions] == ["case_001"]


def test_a_case_that_is_not_in_the_background_is_named():
    slides, masks = folder()
    try:
        sessions_from_masks(
            Drive(slides),
            [Mask("Tissue", Drive(masks))],
            only=["case_999"],
            endpoint=ENDPOINT,
        )
    except ValueError as error:
        assert "case_999" in str(error)
    else:
        raise AssertionError("a case the background does not hold is an error")


def test_min_layers_drops_thin_cases_and_says_when_none_survive():
    slides, masks = folder(slides=("case_001.tif", "case_002.tif"))
    one = [Mask("Tissue", Drive(masks))]
    assert (
        len(sessions_from_masks(Drive(slides), one, min_layers=1, endpoint=ENDPOINT))
        == 1
    )
    try:
        sessions_from_masks(Drive(slides), one, min_layers=2, endpoint=ENDPOINT)
    except ValueError as error:
        assert "2 of 1 masks" in str(error)
    else:
        raise AssertionError("no case reaching the minimum is worth a message")


# ── how the layer is drawn ──────────────────────────────────────────────────


def test_a_mask_is_off_until_the_reader_asks_for_it():
    slides, masks = folder()
    sessions = sessions_from_masks(
        Drive(slides),
        [
            Mask("Tissue", Drive(masks)),
            Mask("epithelium", Drive(masks), visible=True),
        ],
        endpoint=ENDPOINT,
    )
    layers = shaders(sessions[0])
    assert layers["Tissue"]["visible"] == 0
    assert layers["epithelium"]["visible"] == 1


def test_classes_make_a_class_map_that_leaves_the_background_clear():
    slides, masks = folder()
    sessions = sessions_from_masks(
        Drive(slides),
        [
            Mask(
                "Annotations",
                Drive(masks),
                classes=3,
                palette=["#ffffff", "#ff0000", "#00ff00"],
                breaks=[0.25, 0.75],
                mask=[0, 1, 1],
                opacity=0.5,
            )
        ],
        endpoint=ENDPOINT,
    )
    layer = shaders(sessions[0])["Annotations"]
    assert layer["type"] == ShaderType.COLORMAP.value
    assert layer["params"]["color"]["default"] == ["#ffffff", "#ff0000", "#00ff00"]
    assert layer["params"]["threshold"] == {
        "type": "advanced_slider",
        "breaks": [0.25, 0.75],
        "mask": [0, 1, 1],
    }


def test_a_class_map_without_a_palette_is_refused():
    slides, masks = folder()
    try:
        sessions_from_masks(
            Drive(slides),
            [Mask("Annotations", Drive(masks), classes=3)],
            endpoint=ENDPOINT,
        )
    except ValueError as error:
        assert "palette" in str(error)
    else:
        raise AssertionError("classes without a palette cannot be drawn")


def test_a_mask_nobody_gets_is_a_mistake_not_an_empty_layer():
    slides, masks = folder()
    try:
        sessions_from_masks(
            Drive(slides),
            [Mask("Tissue", Drive(masks)), Mask("Typo", MlflowRun(RUN, "report"))],
            endpoint=ENDPOINT,
            flow=flow(FakeClient()),
        )
    except ValueError as error:
        assert "Typo" in str(error)
    else:
        raise AssertionError("a layer no case has is a wrong run id, not a gap")


def test_two_masks_cannot_share_a_name():
    slides, masks = folder()
    try:
        sessions_from_masks(
            Drive(slides),
            [Mask("Same", Drive(masks)), Mask("Same", Drive(masks))],
            endpoint=ENDPOINT,
        )
    except ValueError as error:
        assert "Same" in str(error)
    else:
        raise AssertionError("two layers called Same are not two layers")


def test_masks_sharing_a_source_are_listed_once():
    client = FakeClient()
    calls = {"n": 0}
    original = client.list_artifacts

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    client.list_artifacts = counting
    sessions_from_masks(
        MlflowRun(RUN, "slides"),
        [
            Mask("One", MlflowRun(RUN, "masks")),
            Mask("Two", MlflowRun(RUN, "masks")),
        ],
        endpoint=ENDPOINT,
        flow=flow(client),
    )
    assert calls["n"] == 2, f"expected one listing per run, got {calls['n']}"


# ── the drive folder to a page, the way a script does it ───────────────────


def test_a_drive_and_a_named_mask_reach_the_page():
    """The end-to-end property `build_report` used to assert, on what survives.

    A folder of slides plus a named mask over it becomes one card per case whose
    link carries both DataIDs. The assembly that used to prove it lives in the
    agent's script now, so it is spelled out here -- four lines, which is the
    argument for the assembly having been a library call in the first place.
    """
    slides, masks = folder()
    sessions = sessions_from_masks(
        background=Drive(slides),
        masks=[Mask("Tissue", Drive(masks), color="#00ff00")],
        endpoint=ENDPOINT,
    )
    html = Report(
        title="Dysplasia report",
        blocks=[SlideGrid(sessions=sessions, title="Slides")],
    ).to_html()
    assert "viewer.test/v3/#" in html and "case_001" in html
    assert html.count('class="rf-card rf-slide-card"') == len(sessions)


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
