"""Tests for `report_fast.manifest` — a YAML file that describes a report.

The folder side runs against a temporary directory of fake images; the run side
against the double in `tests/test_mlflow.py`. Nothing here opens a slide, needs
a mount, or reaches a server.

Run:      cd /home/jovyan/report_fast && python tests/test_manifest.py
Pytest:   pytest tests/test_manifest.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_fast import XopatEndpoint  # noqa: E402
from report_fast.manifest import (  # noqa: E402
    ManifestError,
    build,
    plan,
    publish_target,
    read,
)
from report_fast.masks import Drive, Mask  # noqa: E402

from test_mlflow import RUN, FakeClient, flow  # noqa: E402

ENDPOINT = XopatEndpoint(base_url="https://viewer.test/v3/", mount_root="/mnt")


def workspace(**files) -> Path:
    """A folder on disk holding `{relative path: contents}`."""
    root = Path(tempfile.mkdtemp())
    for name, contents in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    return root


def tree(cases=("case_001", "case_002"), masks=("case_001",)) -> Path:
    """A background folder and a mask folder, named so the stem join works."""
    root = Path(tempfile.mkdtemp())
    for names, which in ((cases, "slides"), (masks, "masks")):
        directory = root / which
        directory.mkdir(parents=True, exist_ok=True)
        for stem in names:
            (directory / f"{stem}.tif").write_bytes(b"not really an image")
    return root


def spec(**extra) -> dict:
    """The smallest manifest that means something, with keys bolted on."""
    root = tree()
    every = {
        "title": "Dysplasia",
        "background": {"drive": str(root / "slides")},
        "masks": [{"name": "Tissue", "drive": str(root / "masks")}],
    }
    every.update(extra)
    return every


def raises(function, *, message: str = "") -> str:
    """Run `function`, require ManifestError, return its message."""
    try:
        function()
    except ManifestError as error:
        return str(error)
    raise AssertionError(f"expected ManifestError containing {message!r}")


# ── reading: strict about our keys, verbatim about the viewer's ─────────────


def test_a_manifest_is_read_from_a_file_or_taken_as_a_mapping():
    root = workspace(**{"r.yaml": "title: From file\nbackground: {drive: /slides}\n"})
    from_file = read(root / "r.yaml")
    assert from_file["title"] == "From file"
    # Paths mean the manifest's folder, which is what makes a manifest portable:
    # the same file resolves identically from any working directory.
    assert from_file.base == root

    inline = read({"title": "Inline", "background": {"drive": "/slides"}})
    assert inline["title"] == "Inline"


def test_a_yaml_document_that_is_not_a_mapping_is_refused():
    root = workspace(**{"r.yaml": "- just\n- a\n- list\n"})
    message = raises(lambda: read(root / "r.yaml"), message="mapping")
    assert "mapping" in message


def test_an_unknown_manifest_key_stops_the_build_and_names_the_near_miss():
    # The reason keys are strict: `min_layer:` quietly doing nothing is the
    # failure that costs a day, because the report still builds and looks fine.
    message = raises(lambda: read(spec(min_layer=3)), message="min_layer")
    assert "min_layer" in message
    assert "min_layers" in message  # the suggestion, not just the rejection


def test_an_unknown_key_with_no_near_match_says_what_is_allowed():
    message = raises(lambda: read(spec(quantum_flux=1)), message="quantum_flux")
    assert "did you mean" not in message
    assert "min_layers" in message  # the vocabulary is printed, so no grep needed


def test_a_mask_row_keeps_its_drawing_keys_and_rejects_a_typo():
    # `color:` on a layer is legal; the row is checked once, against the mask
    # vocabulary -- not twice, the second time against the narrower source one.
    parsed = read(
        spec(
            masks=[
                {
                    "name": "Grades",
                    "drive": "/masks",
                    "color": "#ffff00",
                    "opacity": 0.5,
                    "classes": 3,
                    "palette": ["#ffffff", "#ff0000", "#00ff00"],
                    "breaks": [0.25, 0.75],
                    "mask": [0, 1, 1],
                    "visible": True,
                }
            ]
        )
    )
    assert parsed["masks"][0]["color"] == "#ffff00"

    message = raises(
        lambda: read(spec(masks=[{"name": "T", "drive": "/m", "patern": ["*.tif"]}])),
        message="patern",
    )
    assert "patterns" in message  # the suggestion, from the full row vocabulary
    assert "drive, dir, run, path, patterns, recursive, python" in message


def test_a_mask_needs_a_name_because_it_is_the_layer_label():
    message = raises(lambda: read(spec(masks=[{"drive": "/masks"}])), message="name")
    assert "name" in message


def test_a_source_says_exactly_one_of_drive_run_python():
    # The location rule fires when a row becomes an object -- i.e. in plan(),
    # since read() only checks keys and a bad location needs no listing to spot.
    message = raises(
        lambda: plan(spec(background={"drive": "/a", "run": "abc"}), endpoint=ENDPOINT),
        message="exactly one",
    )
    assert "drive, run" in message  # both named, so the fix is obvious

    raises(
        lambda: plan(
            spec(background={"patterns": ["*.tif"]}), endpoint=ENDPOINT
        ),
        message="exactly one",
    )


def test_a_report_without_cases_is_not_a_report():
    message = raises(lambda: read({"title": "Nothing"}), message="background")
    assert "sessions" in message and "sessions_from" in message


def test_a_bare_folder_is_a_source_too():
    # `background: /path` is the shortest honest form and has to keep working.
    assert read({"title": "T", "background": "/slides"})["background"] == "/slides"


# ── the plan: the artifact worth reading before a build ─────────────────────


def test_the_plan_counts_cases_layers_and_sources():
    resolved = plan(spec(), endpoint=ENDPOINT)
    assert resolved.title == "Dysplasia"
    assert resolved.cases == ["case_001", "case_002"]
    assert resolved.layers == {"case_001": 1, "case_002": 0}
    assert resolved.layer_count == 1
    assert resolved.coverage == {"Tissue": 1}
    # Sources are counted, not dumped: a plan says how big each listing was
    # without becoming a file listing of its own.
    assert sorted(label.split("(")[0] for label in resolved.sources) == ["Drive", "Drive"]
    assert sorted(resolved.sources.values()) == [1, 2]  # 1 mask file, 2 slides


def test_the_plan_warns_about_a_layer_not_on_every_case():
    # Two cases, one mask file: legal, and exactly the thing to say out loud.
    resolved = plan(spec(), endpoint=ENDPOINT)
    assert len(resolved.warnings) == 1
    assert "not on every case" in resolved.warnings[0]
    assert "Tissue 1/2" in resolved.warnings[0]


def test_a_layer_on_every_case_warns_about_nothing():
    resolved = plan(
        {
            "title": "Complete",
            "background": {"drive": str(tree(cases=("a", "b"), masks=("a", "b")) / "slides")},
            "masks": [
                {"name": "M", "drive": str(tree(cases=("a", "b"), masks=("a", "b")) / "masks")}
            ],
        },
        endpoint=ENDPOINT,
    )
    assert resolved.coverage == {"M": 2}
    assert resolved.warnings == []


def test_only_is_the_report_order():
    root = tree(cases=("a", "b", "c"), masks=("a", "b", "c"))
    # Deliberately not alphabetical: the manifest's order is the reader's order.
    resolved = plan(
        {
            "title": "Ordered",
            "background": {"drive": str(root / "slides")},
            "masks": [{"name": "M", "drive": str(root / "masks")}],
            "only": ["c", "a"],
        },
        endpoint=ENDPOINT,
    )
    assert resolved.cases == ["c", "a"]


def test_only_naming_a_case_that_is_not_there_stops_the_build():
    root = tree()
    message = raises(
        lambda: plan(
            {
                "title": "Typo",
                "background": {"drive": str(root / "slides")},
                "masks": [{"name": "M", "drive": str(root / "masks")}],
                "only": ["case_001", "case_999"],
            },
            endpoint=ENDPOINT,
        ),
        message="case_999",
    )
    assert "not in the background" in message


def test_min_layers_drops_thin_cases_and_says_which():
    root = tree(cases=("a", "b"), masks=("a",))
    second = root / "masks_two"
    second.mkdir()
    (second / "a.tif").write_bytes(b"x")
    resolved = plan(
        {
            "title": "Thin",
            "background": {"drive": str(root / "slides")},
            "masks": [
                {"name": "M", "drive": str(root / "masks")},
                {"name": "N", "drive": str(second)},
            ],
            "only": ["a", "b"],
            "min_layers": 1,
        },
        endpoint=ENDPOINT,
    )
    assert resolved.cases == ["a"]
    assert resolved.dropped == {"b": 0}
    assert "dropped by min_layers: b (0)" in resolved.to_text()


def test_min_layers_that_survives_nothing_stops_the_build():
    root = tree(cases=("a", "b"), masks=("a",))
    message = raises(
        lambda: plan(
            {
                "title": "Too strict",
                "background": {"drive": str(root / "slides")},
                "masks": [{"name": "M", "drive": str(root / "masks")}],
                "min_layers": 3,
            },
            endpoint=ENDPOINT,
        ),
        message="masks",
    )
    assert "No case reached" in message


def test_only_reads_a_file_of_case_ids():
    root = tree(cases=("a", "b", "c"), masks=("a", "b"))
    (root / "cases.txt").write_text("# the interesting ones\nb\n\na\n", encoding="utf-8")
    manifest = root / "r.yaml"
    manifest.write_text(
        "title: From file\n"
        f"background: {{drive: {root / 'slides'}}}\n"
        f"masks:\n  - {{name: M, drive: {root / 'masks'}}}\n"
        "only: cases.txt\n",
        encoding="utf-8",
    )
    resolved = plan(read(manifest), endpoint=ENDPOINT)
    assert resolved.cases == ["b", "a"]  # comments and blanks ignored, order kept


def test_only_naming_a_file_that_does_not_exist_says_so():
    root = tree()
    manifest = root / "r.yaml"
    manifest.write_text(
        "title: Missing\n"
        f"background: {{drive: {root / 'slides'}}}\n"
        f"masks:\n  - {{name: M, drive: {root / 'masks'}}}\n"
        "only: cases.txt\n",
        encoding="utf-8",
    )
    message = raises(lambda: plan(read(manifest), endpoint=ENDPOINT), message="cases.txt")
    assert "neither a list of cases nor a file" in message


def test_a_layer_nobody_gets_stops_the_build():
    # A mistyped run id or artifact directory used to produce a report with one
    # fewer column and no indication of it.
    root = tree()
    empty = root / "nothing_here"
    empty.mkdir()
    message = raises(
        lambda: plan(
            {
                "title": "Mistake",
                "background": {"drive": str(root / "slides")},
                "masks": [{"name": "Ghost", "drive": str(empty)}],
            },
            endpoint=ENDPOINT,
        ),
        message="Ghost",
    )
    assert "mistyped run id" in message


def test_a_source_folder_that_is_not_mounted_is_a_manifest_error_not_a_traceback():
    # The CLI turns ManifestError into a message and exit 1; a bare
    # FileNotFoundError would give an agent a traceback and no exit code meaning.
    message = raises(
        lambda: plan(
            {"title": "Gone", "background": {"drive": "/no/such/mount/anywhere"}},
            endpoint=ENDPOINT,
        ),
        message="not on this machine",
    )
    assert "<manifest>" in message or ".yaml" in message  # named where it came from


def test_the_plan_text_carries_the_numbers_and_the_json_the_same_ones():
    resolved = plan(spec(), endpoint=ENDPOINT)
    text = resolved.to_text()
    assert "2 cases" in text
    assert "Tissue: 1/2" in text
    assert "publish  not set" in text

    payload = json.loads(resolved.to_json())
    assert payload["cases"] == resolved.cases
    assert payload["coverage"] == resolved.coverage
    assert payload["sources"] == resolved.sources
    assert payload["warnings"] == resolved.warnings
    # Key order is part of the contract: a CI job diffs these.
    assert list(payload) == sorted(payload)


def test_the_plan_names_where_the_report_will_go_and_whether_it_publishes():
    resolved = plan(spec(publish="run-abc"), endpoint=ENDPOINT)
    assert resolved.publish == "run-abc"
    assert "only with --publish" in resolved.to_text()  # never implied
    assert publish_target(spec(publish={"run_id": "run-xyz"})) == "run-xyz"
    assert publish_target(spec()) is None


# ── out: where the file goes ────────────────────────────────────────────────


def test_out_defaults_beside_the_manifest():
    slides = tree(cases=("one",), masks=()) / "slides"
    root = workspace(**{"r.yaml": f"title: T\nbackground: {{drive: {slides}}}\n"})
    assert plan(read(root / "r.yaml"), endpoint=ENDPOINT).out == root / "r.html"


def test_a_manifest_in_manifests_builds_into_reports():
    slides = tree(cases=("one",), masks=()) / "slides"
    root = workspace(**{"manifests/r.yaml": f"title: T\nbackground: {{drive: {slides}}}\n"})
    resolved = plan(read(root / "manifests" / "r.yaml"), endpoint=ENDPOINT)
    assert resolved.out == root / "reports" / "r.html"


def test_out_can_say_otherwise():
    slides = tree(cases=("one",), masks=()) / "slides"
    root = workspace(
        **{"r.yaml": f"title: T\nout: build/page.html\nbackground: {{drive: {slides}}}\n"}
    )
    assert plan(read(root / "r.yaml"), endpoint=ENDPOINT).out == root / "build/page.html"


# ── build: the same call a human writes ─────────────────────────────────────


def test_build_writes_the_html_the_manifest_describes():
    root = tree()
    out = root / "page.html"
    built = build(
        {
            "title": "Built",
            "subtitle": "two cases",
            "background": {"drive": str(root / "slides")},
            "masks": [{"name": "Tissue", "drive": str(root / "masks")}],
            "intro": "Read the notes.",
            "blocks": [{"prose": "methods here"}, {"heading": "Findings"}],
        },
        out=out,
        check=False,
        endpoint=ENDPOINT,
    )
    html = out.read_text(encoding="utf-8")
    assert built.out == out
    assert "Built" in html and "two cases" in html
    assert "Read the notes." in html and "Findings" in html
    assert built.published is None


def test_the_same_manifest_builds_byte_identical_html_twice():
    # Locked decision 9: no timestamps, no generated code, fixed key order. What
    # differs between two runs has to be a case or a layer, never the serializer.
    root = tree()
    manifest = {
        "title": "Deterministic",
        "background": {"drive": str(root / "slides")},
        "masks": [
            {"name": "Tissue", "drive": str(root / "masks"), "color": "#ffff00"},
            {"name": "Outline", "drive": str(root / "masks"), "color": "#ffffff"},
        ],
        "blocks": [{"prose": "one"}, {"bullets": ["a", "b"]}, {"metrics": {"auc": 0.9}}],
    }
    first = build(manifest, out=root / "first.html", check=False, endpoint=ENDPOINT)
    second = build(manifest, out=root / "second.html", check=False, endpoint=ENDPOINT)
    assert first.out.read_bytes() == second.out.read_bytes()
    assert first.report.to_html() == second.report.to_html()


def test_ids_in_the_page_are_positional_not_random():
    # The specific regression behind the reproducibility rule: a grid numbered
    # itself with a uuid before the report numbered its blocks, so every card id
    # and every viewer link changed between two runs of one manifest.
    root = tree()
    html = build(
        {
            "title": "Ids",
            "background": {"drive": str(root / "slides")},
            "masks": [{"name": "M", "drive": str(root / "masks")}],
        },
        out=False,
        check=False,
        endpoint=ENDPOINT,
    ).report.to_html()
    assert "slide-grid_01_card0" in html
    # Eight hex digits is the shape a uuid4 prefix takes; no id may contain one.
    import re

    assert not re.search(r"[0-9a-f]{8}_[a-z]", html), "a random id reached the page"


def test_out_false_builds_without_writing_anything():
    root = tree()
    built = build(
        {
            "title": "Dry",
            "background": {"drive": str(root / "slides")},
            "masks": [{"name": "M", "drive": str(root / "masks")}],
        },
        out=False,
        check=False,
        endpoint=ENDPOINT,
    )
    assert built.out is None
    assert not (root / "r.html").exists()
    assert not list(root.glob("*.html")), "nothing should have been written"
    assert "Dry" in built.report.to_html()  # built, just not written


def test_publish_is_never_implied_by_the_manifest():
    # `publish:` records where a report belongs. Only publish=True uploads, so a
    # CI build cannot write to MLflow by accident.
    root = tree()
    manifest = {
        "title": "Not yet",
        "background": {"drive": str(root / "slides")},
        "masks": [{"name": "M", "drive": str(root / "masks")}],
        "publish": RUN,
    }
    client = FakeClient()
    built = build(
        manifest,
        out=False,
        check=False,
        flow=flow(client),
        endpoint=ENDPOINT,
    )
    assert built.published is None
    assert client.logged == [] and client.logged_dirs == []

    uploaded = build(
        manifest,
        out=False,
        check=False,
        publish=True,
        flow=flow(client),
        endpoint=ENDPOINT,
    )
    assert uploaded.published.run_id == RUN
    assert [item.artifact_path for item in client.logged] == ["report"]
    assert str(uploaded.published).startswith("report/report.html on run " + RUN)


def test_run_id_overrides_the_manifest_for_one_publish():
    root = tree()
    client = FakeClient()
    built = build(
        {
            "title": "Elsewhere",
            "background": {"drive": str(root / "slides")},
            "masks": [{"name": "M", "drive": str(root / "masks")}],
            "publish": RUN,
        },
        out=False,
        check=False,
        publish=True,
        run_id="dirs-only",
        flow=flow(client),
        endpoint=ENDPOINT,
    )
    assert built.published.run_id == "dirs-only"


class LoggingClient(FakeClient):
    """`FakeClient` that remembers what was inside the directory it was given.

    The manifest and plan are logged from a temporary directory that both
    `manifest._publish` and `Mlflow.publish` clean up on the way out, so a test
    that lists it afterwards is reading a path that no longer exists. Copying the
    listing at `log_artifacts` time is the only honest way to assert on it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.seen: dict = {}

    def log_artifacts(self, run_id, local_dir, artifact_path=None) -> None:
        self.seen[str(local_dir)] = sorted(
            (path.name, path.read_text(encoding="utf-8"))
            for path in Path(local_dir).iterdir()
        )
        super().log_artifacts(run_id, local_dir, artifact_path=artifact_path)


def test_publish_logs_the_declared_manifest_and_the_resolved_plan():
    # The manifest answers "what did someone mean"; the plan answers "what is in
    # this report". Only the second is still true after a case list changes.
    root = tree()
    client = LoggingClient()
    build(
        {
            "title": "Logged",
            "background": {"drive": str(root / "slides")},
            "masks": [{"name": "M", "drive": str(root / "masks")}],
            "publish": RUN,
        },
        out=False,
        check=False,
        publish=True,
        flow=flow(client),
        endpoint=ENDPOINT,
    )
    assert client.logged_dirs, "the manifest and plan travel with the report"
    (_, local_dir, artifact), = client.logged_dirs
    listed = dict(client.seen[local_dir])
    assert sorted(listed) == ["manifest.yaml", "plan.json", "provenance.json"]
    assert artifact == "report/conf"
    # The sidecar travels *with* a manifest, not instead of one: it is the only one
    # of the three that records the endpoint and the viewer stamp the links carry.
    record = json.loads(listed["provenance.json"])
    assert record["kind"] == "manifest"
    # A manifest built from a Python dict has no filename to name; recording the
    # placeholder path manifest.read gives it would send a reader to a file that
    # does not exist, so the field is null and says so.
    assert record["inputs"]["manifest"] is None
    assert record["report"]["cases"] == ["case_001", "case_002"]
    assert record["viewer"]["version"]
    # What is logged is what *ran*: the plan carries the resolved cases, which is
    # the question a run has to keep answering after the case list moves.
    assert json.loads(listed["plan.json"])["cases"] == ["case_001", "case_002"]
    assert "title: Logged" in listed["manifest.yaml"]


def test_publishing_with_nowhere_to_go_says_so_before_uploading():
    root = tree()
    client = FakeClient()
    message = raises(
        lambda: build(
            {
                "title": "Homeless",
                "background": {"drive": str(root / "slides")},
                "masks": [{"name": "M", "drive": str(root / "masks")}],
            },
            out=False,
            check=False,
            publish=True,
            flow=flow(client),
            endpoint=ENDPOINT,
        ),
        message="publish",
    )
    assert "no publish:" in message
    assert client.logged == []  # refused before any byte moved


# ── the three doors ─────────────────────────────────────────────────────────


def test_a_pasted_session_keeps_what_we_do_not_model():
    # The paste path, and the round-trip contract at the manifest layer: not
    # modelling a field is never grounds for dropping it.
    document = {
        "params": {"appBar": False, "branding": {"enabled": False}},
        "data": ["/slides/case_x.tif"],
        "sessionName": "keep-me",
        "background": [{"name": "case", "dataReference": 0, "visualizationIndex": 0}],
        "visualizations": [{"name": "v", "shaders": {}}],
        "plugins": [],
        "something_we_have_never_seen": {"nested": True},
    }
    root = workspace(**{"session.json": json.dumps(document)})
    resolved = plan(
        {"title": "Pasted", "sessions": [{"from_file": str(root / "session.json")}]},
        endpoint=ENDPOINT,
    )
    assert resolved.cases == ["case"]
    exported = resolved.sessions[0].to_config()
    assert exported["something_we_have_never_seen"] == {"nested": True}
    assert exported["sessionName"] == "keep-me"
    # `masks:` would be a contradiction here: a pasted session carries layers.
    raises(
        lambda: plan(
            {
                "title": "Confused",
                "sessions": [{"from_file": str(root / "session.json")}],
                "masks": [{"name": "M", "drive": "/m"}],
            },
            endpoint=ENDPOINT,
        ),
        message="belongs with background",
    )


def test_background_and_sessions_do_not_both_get_to_be_the_cases():
    # The failure mode is the quiet kind: background: wins, plan() never renders
    # the pasted rows, and the report looks like it has fewer cases than the
    # author wrote. Rejecting is the only answer that reaches the author.
    root = workspace(**{"s.json": json.dumps({"data": ["/a.tif"]})})
    message = raises(
        lambda: read(
            {
                "title": "Both",
                "background": {"drive": "/slides"},
                "sessions": [{"from_file": str(root / "s.json")}],
            }
        ),
        message="both name cases",
    )
    assert "sessions:" in message and "background:" in message
    # sessions_from: is the same door from the other side.
    raises(
        lambda: read(
            {"title": "Both", "background": {"drive": "/slides"}, "sessions_from": "p:f"}
        ),
        message="both name cases",
    )
    # And a manifest that picks one side is fine -- this check must not bite there.
    assert read({"title": "One", "sessions": [{"from_file": str(root / "s.json")}]})


def test_a_pasted_session_row_takes_exactly_one_door():
    raises(
        lambda: read({"title": "T", "sessions": [{"from_file": "a.json", "from_url": "u"}]}),
        message="exactly one",
    )
    raises(
        lambda: read({"title": "T", "sessions": [{"from_elsewhere": "x"}]}),
        message="from_config",
    )


def test_sessions_from_calls_a_function_of_your_own():
    # Door three: no limit at all, and still no hand-authored session JSON.
    root = tree(cases=("only_case",), masks=())
    helper = root / "rf_helper_sessions.py"
    helper.write_text(
        "from report_fast import XopatSession\n"
        "def build_sessions():\n"
        f"    return [XopatSession.from_slide({str(root / 'slides' / 'only_case.tif')!r},"
        " name='hand-made')]\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(root))
    try:
        resolved = plan(
            {"title": "Custom", "sessions_from": "rf_helper_sessions:build_sessions"},
            endpoint=ENDPOINT,
        )
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("rf_helper_sessions", None)
    assert resolved.cases == ["hand-made"]


def test_sessions_from_that_returns_non_sessions_says_so():
    root = workspace(**{"rf_helper_bad.py": "def nope():\n    return [3]\n"})
    sys.path.insert(0, str(root))
    try:
        message = raises(
            lambda: plan(
                {"title": "Bad", "sessions_from": "rf_helper_bad:nope"},
                endpoint=ENDPOINT,
            ),
            message="not sessions",
        )
        assert "rf_helper_bad:nope" in message
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("rf_helper_bad", None)


def test_a_source_can_be_a_python_reference():
    root = tree(cases=("x", "y"), masks=())
    helper = root / "rf_helper_source.py"
    helper.write_text(
        "from report_fast import Drive\n" f"source = Drive({str(root / 'slides')!r})\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(root))
    try:
        resolved = plan(
            {
                "title": "Custom source",
                "background": {"python": "rf_helper_source:source"},
            },
            endpoint=ENDPOINT,
        )
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("rf_helper_source", None)
    assert resolved.cases == ["x", "y"]


def test_a_python_reference_that_is_not_a_source_is_refused():
    root = workspace(**{"rf_helper_wrong.py": "source = 5\n"})
    sys.path.insert(0, str(root))
    try:
        message = raises(
            lambda: read(
                {
                    "title": "T",
                    "background": {"python": "rf_helper_wrong:source"},
                }
            )
            and plan(
                {"title": "T", "background": {"python": "rf_helper_wrong:source"}},
                endpoint=ENDPOINT,
            ),
            message="not a MaskSource",
        )
        assert "int" in message
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("rf_helper_wrong", None)


def test_a_python_reference_that_cannot_be_imported_names_the_fix():
    message = raises(
        lambda: plan(
            {"title": "T", "background": {"python": "no_such_pkg:thing"}},
            endpoint=ENDPOINT,
        ),
        message="no_such_pkg",
    )
    assert "on the path of the project" in message

    raises(
        lambda: plan(
            {"title": "T", "background": {"python": "no_colon"}}, endpoint=ENDPOINT
        ),
        message="pkg.module:function",
    )


def test_params_on_a_mask_row_reach_the_layer_verbatim():
    # Door one: any field the viewer declares, modelled here or not.
    root = tree(cases=("one",), masks=("one",))
    built = build(
        {
            "title": "Raw params",
            "background": {"drive": str(root / "slides")},
            "masks": [
                {
                    "name": "M",
                    "drive": str(root / "masks"),
                    "params": {"some_v3_field": 7},
                }
            ],
        },
        out=False,
        check=False,
        endpoint=ENDPOINT,
    )
    shader = built.plan.sessions[0].visualizations[0]["shaders"]
    params = next(iter(shader.values()))["params"]
    assert params["some_v3_field"] == 7


# ── blocks ──────────────────────────────────────────────────────────────────


def test_every_block_type_becomes_a_component():
    root = tree(cases=("one",), masks=())
    figure = root / "fig.png"
    figure.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 60)
    html = build(
        {
            "title": "Blocks",
            "background": {"drive": str(root / "slides")},
            "blocks": [
                {"prose": ["first", "second"]},
                {"heading": "Deep"},
                {"bullets": ["a", "b"]},
                {"links": {"Viewer": "https://viewer.test/v3/"}},
                {"metrics": {"auc": 0.9, "f1": 0.8}},
                {"chart": str(figure)},
                {"raw_html": "<p>raw</p>"},
                {"section": {"title": "Methods", "blocks": [{"prose": "inside"}]}},
            ],
        },
        out=False,
        check=False,
        endpoint=ENDPOINT,
    ).report.to_html()
    for expected in (
        "first",
        "second",
        "Deep",
        "<ul",
        "Viewer",
        "0.9",
        "data:image",
        "<p>raw</p>",
        "Methods",
        "inside",
    ):
        assert expected in html, expected


def test_a_block_row_that_is_not_one_block_is_refused():
    root = tree(cases=("one",), masks=())
    base = {"title": "T", "background": {"drive": str(root / "slides")}}
    raises(lambda: read(dict(base, blocks=["just a string"])), message="one block per item")
    raises(
        lambda: read(dict(base, blocks=[{"prose": "x", "heading": "y"}])),
        message="one block per item",
    )


def test_prose_reads_a_markdown_file_beside_the_manifest():
    # Prose in a `.md` file rewords like prose; in the manifest it rots the page.
    root = tree(cases=("one",), masks=())
    (root / "notes").mkdir()
    (root / "notes" / "methods.md").write_text("We tiled it.\n", encoding="utf-8")
    manifest = root / "r.yaml"
    manifest.write_text(
        f"title: Prose\nbackground: {{drive: {root / 'slides'}}}\n"
        "intro: notes/methods.md\n",
        encoding="utf-8",
    )
    html = build(
        read(manifest), out=False, check=False, endpoint=ENDPOINT
    ).report.to_html()
    assert "We tiled it." in html

    # A string that is not a file is prose, unchanged.
    inline = build(
        {
            "title": "Inline",
            "background": {"drive": str(root / "slides")},
            "intro": "just words",
        },
        out=False,
        check=False,
        endpoint=ENDPOINT,
    ).report.to_html()
    assert "just words" in inline


def test_the_grid_can_be_turned_off_or_tuned():
    root = tree()
    default = build(
        {
            "title": "Grid",
            "background": {"drive": str(root / "slides")},
            "masks": [{"name": "M", "drive": str(root / "masks")}],
        },
        out=False,
        check=False,
        endpoint=ENDPOINT,
    ).report.to_html()
    assert "rf-slide-grid" in default

    one_per_row = build(
        {
            "title": "No grid",
            "grid": False,
            "background": {"drive": str(root / "slides")},
            "masks": [{"name": "M", "drive": str(root / "masks")}],
        },
        out=False,
        check=False,
        endpoint=ENDPOINT,
    ).report.to_html()
    assert "rf-slide-grid" not in one_per_row
    assert "rf-slide-card" in one_per_row


def test_a_manifest_built_from_python_objects_works_too():
    # The interpreter passes real objects straight through, which is how one code
    # path serves both `build_report(...)` in a script and the same thing in YAML.
    root = tree()
    html = build(
        {
            "title": "Objects",
            "background": Drive(root / "slides"),
            "masks": [Mask("Tissue", Drive(root / "masks"), color="#00ff00")],
        },
        out=False,
        check=False,
        endpoint=ENDPOINT,
    ).report.to_html()
    assert "Tissue" in html


# ── run-driven sources, against the double ──────────────────────────────────


def test_masks_can_come_from_a_run():
    root = tree()
    resolved = plan(
        {
            "title": "From a run",
            "background": {"drive": str(root / "slides")},
            "masks": [{"name": "Heatmaps", "run": RUN, "path": "masks"}],
        },
        endpoint=ENDPOINT,
        flow=flow(FakeClient()),
    )
    assert resolved.coverage == {"Heatmaps": 1}
    # The plan names the source as a run, so a reader can chase the right run id.
    assert any(RUN in label for label in resolved.sources)


def test_the_background_too_can_come_from_a_run():
    resolved = plan(
        {
            "title": "Run background",
            "background": {"run": RUN, "path": "slides"},
            "masks": [{"name": "Heatmaps", "run": RUN, "path": "masks"}],
        },
        endpoint=ENDPOINT,
        flow=flow(FakeClient()),
    )
    assert resolved.cases == ["case_001", "case_002"]


def test_a_run_without_a_client_is_a_refusal_not_a_silent_empty_report():
    # The old tool returned an empty table for a missing artifact directory and
    # reported zero slides. Here the absence of a tracking server has to arrive
    # as a failure, never as a report with nothing in it.
    saved = {key: os.environ.pop(key, None) for key in ("MLFLOW_TRACKING_URI",)}
    try:
        try:
            plan(
                {"title": "No client", "background": {"run": RUN, "path": "slides"}},
                endpoint=ENDPOINT,
            )
        except ManifestError as error:
            assert RUN in str(error) or "run" in str(error).lower()
        except Exception as error:
            # A connection failure from a real client is also a refusal.
            assert type(error).__name__ in {"MlflowError", "ConnectionError", "ValueError"}, type(error)
        else:
            raise AssertionError("a run with no way to reach it should not resolve")
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


# ── the committed manifests ─────────────────────────────────────────────────


#: Every YAML file the repo ships as an example or as a real report.
COMMITTED = sorted((Path(__file__).resolve().parents[1] / "manifests").glob("*.yaml"))


def test_the_shipped_manifests_parse():
    # These files are documentation. The moment one of them mentions a key that
    # was renamed, the first thing a reader copies stops working -- and the
    # failure lands on someone with no idea the vocabulary moved. `read()` is the
    # whole key check, and costs no network: paths are not resolved and runs are
    # not listed, so this stays a test rather than becoming a deployment.
    assert COMMITTED, "manifests/ ships the examples; there should be some"
    for path in COMMITTED:
        made = read(path)
        assert made.get("title"), f"{path.name} has no title"


def test_the_shipped_manifests_say_what_they_would_write():
    # `out:` is relative to the manifest, and a manifest in manifests/ defaults to
    # ../reports/. Both are worth pinning on the real files: an example whose
    # output lands in an unexpected place teaches the wrong lesson about the key.
    for path in COMMITTED:
        spec = read(path)
        out = spec.get("out")
        if out:
            assert not Path(str(out)).is_absolute(), f"{path.name}: out: should be relative"
            assert str(out).endswith(".html"), f"{path.name}: out: is not an .html path"


def test_endpoint_flags_override_the_manifest():
    root = tree(cases=("one",), masks=())
    base = {"title": "Endpoint", "background": {"drive": str(root / "slides")}}
    elsewhere = XopatEndpoint(base_url="https://other.test/v3/", mount_root="/data")
    resolved = plan(base, endpoint=elsewhere)
    session = resolved.sessions[0]
    # The DataID is relative to the given mount root, and the link to the given base.
    assert session.url().startswith("https://other.test/v3/")
    assert not str(session.data[0]).startswith("/data")  # /mnt-less root, path as-is


if __name__ == "__main__":
    failures = []
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print(f"ok   {name}")
            except Exception as error:  # noqa: BLE001 - the runner reports everything
                failures.append(name)
                print(f"FAIL {name}: {type(error).__name__}: {error}")
    print(f"\n{len(failures)} failed")
    raise SystemExit(1 if failures else 0)
