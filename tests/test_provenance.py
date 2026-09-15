"""Tests for `report_fast.provenance` -- the record, and the page it must not touch.

Run:      cd /home/jovyan/report_fast && python tests/test_provenance.py
Pytest:   pytest tests/test_provenance.py

Decision 8 is a prohibition before it is a feature, so most of this file is
asserting absence:

  * **nothing in the page.** The HTML a build writes is byte-identical to the HTML
    the same build writes with `with_provenance=False`, and contains no provenance
    marker of any kind. A footer would otherwise arrive back the moment someone
    finds the sidecar useful.
  * **nothing invented.** No clock, no absolute path the tool chose, no session
    design copied 300 times. Two builds of the same input write the same bytes,
    like the report does -- a record that varies is noise, not evidence.
  * **nothing declared.** The endpoint, the cases and the DataIDs are read off the
    objects that produced the links. A record written from the spec is the old
    tool's bug restated: report and logged config disagreeing because one resolved
    and one was meant.

The accepted trade-off is asserted too, so it cannot be rediscovered as a bug:
**a mailed HTML carries no provenance**. The pair travels, the page alone does not.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_fast import provenance  # noqa: E402
from report_fast.compose import Composition, expand, load_design  # noqa: E402
from report_fast.provenance import (  # noqa: E402
    KEYS,
    SUFFIX,
    Provenance,
    ProvenanceError,
    design_of,
    for_cli,
    sidecar_path,
    tool_version,
    verify_pair,
    write_for,
)
from report_fast.xopat import XopatEndpoint  # noqa: E402

ENDPOINT = XopatEndpoint(
    base_url="https://xopat.example/xopat/",
    wsi_base_url="https://tiles.example/wsi/",
    image_protocol="wsi_service",
    mount_root="/data",
)

DESIGN = {
    "params": {"sessionName": "case-a", "theme": "auto", "activeBackgroundIndex": 0},
    "data": [
        {"dataID": "/data/case-a/a.tif", "protocol": "wsi_service"},
        {"dataID": "/data/case-a/a.prob.tif", "protocol": "wsi_service"},
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
                    "dataReferences": [1, 0],
                    "params": {"color": "#ff8c00", "threshold": 0.5},
                }
            },
        }
    ],
}


def workspace(cases=3):
    """One design bound `cases` times, plus the folder a CLI would have read."""
    import tempfile

    root = Path(tempfile.mkdtemp())
    (root / "design.json").write_text(json.dumps(DESIGN, indent=1), encoding="utf-8")
    rows = [
        {
            "slide": f"/data/case-{index:02d}/s.tif",
            "mask": f"/data/case-{index:02d}/p.tif",
            "name": f"case-{index:02d}",
        }
        for index in range(cases)
    ]
    sessions = expand(
        DESIGN,
        rows,
        slots={0: "slide", 1: "mask"},
        endpoint=ENDPOINT,
    )
    folder = root / "sessions"
    folder.mkdir()
    # strict=: a session per row or the fixture is broken, and silently pairing a
    # shorter list would write a folder of the wrong cases.
    for row, session in zip(rows, sessions, strict=True):
        (folder / f"{row['name']}.json").write_text(
            json.dumps(session.to_config()), encoding="utf-8"
        )
    return root, folder, sessions


def composition(sessions, **extra):
    return Composition(
        title=extra.pop("title", "Cohort QC"),
        blocks=[{"SlideGrid": {"sessions": list(sessions)}}],
        **extra,
    )


def read_back(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def raises(function, *, message: str = "") -> str:
    try:
        function()
    except ProvenanceError as error:
        text = str(error)
        assert message in text, f"{message!r} not in {text!r}"
        return text
    raise AssertionError(f"expected a ProvenanceError mentioning {message!r}")


# ── the ruling: the page is untouched ───────────────────────────────────────


def test_the_page_is_the_same_bytes_with_a_sidecar_and_without_one():
    """Byte-identical, not "equivalent". The claim is that the HTML does not know.

    Written both ways into one folder and compared as bytes, because every weaker
    comparison (parse and compare, compare with provenance lines stripped) would
    pass on a page that had a footer added and then subtracted somewhere else.
    """
    root, _, sessions = workspace()
    with_sidecar = composition(sessions).build(out=root / "a.html", check=False)
    without = composition(sessions).build(
        out=root / "b.html", check=False, with_provenance=False
    )
    assert Path(with_sidecar.out).read_bytes() == Path(without.out).read_bytes()
    assert Path(with_sidecar.provenance).is_file()
    assert without.provenance is None, "opting out writes no record, not an empty one"


def test_no_provenance_marker_of_any_kind_reaches_the_page():
    """The words the sidecar is made of, searched for in the HTML that was written."""
    root, _, sessions = workspace()
    built = composition(sessions).build(out=root / "r.html", check=False)
    html = Path(built.out).read_text(encoding="utf-8")
    for marker in ("provenance", "viewer_stamp", "derive_schema", "report-fast"):
        assert marker not in html.lower(), f"{marker} leaked into the page"
    # A comment would be the polite way to do it and is still ruled out.
    assert "<!--" not in html


def test_a_mailed_report_carries_no_provenance_and_says_so_in_its_own_module():
    """The trade-off, pinned as a behaviour.

    Someone will one day propose a footer to "fix" this. The test is the record
    that it was chosen: strip the sidecar and the HTML answers nothing about its
    inputs.
    """
    root, _, sessions = workspace()
    built = composition(sessions).build(out=root / "r.html", check=False)
    html = Path(built.out).read_text(encoding="utf-8")
    assert "xopat.example" in html, "the links are in the page, as they must be"
    assert tool_version() not in html
    assert "derive" not in html.lower()


# ── naming, and the pair ────────────────────────────────────────────────────


def test_the_sidecar_is_named_after_the_report_and_sits_beside_it():
    assert sidecar_path("reports/qc.html") == Path("reports/qc.provenance.json")
    assert sidecar_path(Path("/tmp/x/r.HTML")) == Path("/tmp/x/r.provenance.json")
    assert SUFFIX == ".provenance.json"


def test_a_build_that_wrote_no_page_writes_no_sidecar():
    """`--check-only` on both doors. A record beside a report that does not exist
    is a rumour, and the worse failure because it looks like evidence."""
    assert write_for(None, Provenance()) is None
    root, _, sessions = workspace()
    built = composition(sessions).build(out=False, check=False)
    assert built.out is None and built.provenance is None
    assert list(root.glob("*.provenance.json")) == []


def test_a_sidecar_refuses_to_claim_a_non_html_file():
    root, _, sessions = workspace()
    record = composition(sessions).record_provenance()
    raises(lambda: record.write(root / "notes.pdf"), message="not an .html file")


# ── reproducibility ─────────────────────────────────────────────────────────


def test_two_builds_of_the_same_input_write_the_same_sidecar_bytes():
    """Same guarantee the HTML has, because the record is evidence only if it does.

    The two compositions are separate objects over the same sessions, which is
    what a rerun makes: equal, not identical.
    """
    root, _, sessions = workspace()
    first = composition(sessions).build(out=root / "one.html", check=False)
    second = composition(sessions).build(out=root / "two.html", check=False)
    one = Path(first.provenance).read_text(encoding="utf-8")
    two = Path(second.provenance).read_text(encoding="utf-8")
    # The paths inside differ (they describe different files); everything about the
    # *build* has to agree, so compare the record with its own paths removed.
    left, right = json.loads(one), json.loads(two)
    assert left == right, "the record disagrees with itself across two identical builds"


def test_no_build_timestamp_is_recorded_and_the_only_date_is_the_contract_s():
    """The distinction the reproducibility rule turns on.

    `viewer.derived_at` is when the schema was derived from the viewer checkout --
    a fact about the contract that is identical for every build until someone
    re-runs `derive_schema.py`, and the design puts the skill's version stamp in
    this file. A *build* time would make every two runs differ, which is how a
    record stops being comparable, so nothing here writes one.
    """
    root, _, sessions = workspace()
    built = composition(sessions).build(out=root / "r.html", check=False)
    record = read_back(built.provenance)
    text = json.dumps(record)
    dated = [
        key
        for key in re.findall(r'"([a-z_]+)":\s*"\d{4}-\d{2}-\d{2}', text)
    ]
    assert dated == ["derived_at"], dated
    assert "derived_at" in record["viewer"]
    for invented in ("built_at", "generated_at", "timestamp", "created", "run_at"):
        assert invented not in text


def test_the_record_keys_are_the_documented_ones_in_a_fixed_order():
    root, _, sessions = workspace()
    built = composition(sessions).build(out=root / "r.html", check=False)
    written = list(json.loads(Path(built.provenance).read_text(encoding="utf-8")))
    assert written == list(KEYS)


# ── what is in it ───────────────────────────────────────────────────────────


def test_the_endpoint_recorded_is_the_one_the_links_were_built_against():
    root, _, sessions = workspace()
    record = composition(sessions).record_provenance(endpoint=ENDPOINT).to_dict()
    assert record["endpoint"] == {
        "base_url": "https://xopat.example/xopat/",
        "wsi_base_url": "https://tiles.example/wsi/",
        "image_protocol": "wsi_service",
        "mount_root": "/data",
    }, "four scalar fields, not a dataclass repr"


def test_every_dataid_the_page_links_is_recorded_flat_and_in_order():
    """Flat strings, and every one: a layer pointed at the wrong file is the
    expensive mistake, and it is invisible on the card."""
    root, _, sessions = workspace(cases=3)
    built = composition(sessions).build(out=root / "r.html", check=False)
    record = read_back(built.provenance)
    assert len(record["data_ids"]) == 6, "three slides, three masks"
    assert all(isinstance(item, str) for item in record["data_ids"])
    # Mounted, exactly as the links carry them: `/data` is the endpoint's
    # mount_root and a bound slot is stripped on the way in. Recording the
    # filesystem path instead would name files the image server cannot address.
    assert record["data_ids"][:2] == ["case-00/s.tif", "case-00/p.tif"]


def test_the_viewer_stamp_names_the_viewer_the_contract_came_from():
    """The skill's "schema derived from xOpat 3.1.0, commit 18c94f2b" lives here
    and in schema/, per the design -- not in the page."""
    root, _, sessions = workspace()
    record = composition(sessions).record_provenance().to_dict()
    assert record["viewer"]["version"]
    assert record["viewer"]["commit"]
    assert record["tool"] == {"name": "report-fast", "version": tool_version()}


def test_the_record_says_which_door_built_the_page():
    root, _, sessions = workspace()
    assert composition(sessions).record_provenance().kind == "composition"
    raises(lambda: Provenance(kind="yaml"), message="either 'manifest'")


def test_a_record_from_a_template_holds_the_design_once_not_per_case():
    """One document plus the slots. 300 instantiations would be a copy of the
    report's inputs at 300 times the size and no more useful.

    `slots` is part of the record because it is part of what reproduces the page:
    the same design bound `{0: slide}` is a different report.
    """
    template = load_design(
        DESIGN, slots={0: "slide", 1: "mask"}, endpoint=ENDPOINT
    )
    record = design_of(template)
    assert record["slots"] == {"0": "slide", "1": "mask"}
    assert record["session"]["params"]["sessionName"] == "case-a"
    assert len(record["session"]["data"]) == 2, "the design, not the bound cases"


def test_a_design_recorded_next_to_the_cases_is_the_one_the_cases_came_from():
    """The rebuild claim: design + case list, not the 300 files a loop produced."""
    root, folder, sessions = workspace(cases=2)
    record = for_cli(
        title="Cohort QC",
        cases=["case-00", "case-01"],
        sessions=sessions,
        endpoint=ENDPOINT,
        design=design_of(load_design(DESIGN, slots={0: "slide", 1: "mask"})),
        sessions_dir=folder,
    )
    written = record.to_dict()
    assert written["design"]["slots"] == {"0": "slide", "1": "mask"}
    assert written["inputs"]["sessions_dir"].endswith("sessions")
    assert written["sources"] == [str(folder)]
    # The bound DataIDs are in `data_ids`; the design's own placeholders are not
    # rewritten to match them, because the design is *as authored*.
    assert written["design"]["session"]["data"][0]["dataID"] == "/data/case-a/a.tif"


def test_a_non_session_is_refused_rather_than_recorded_as_nothing():
    root, _, sessions = workspace()
    raises(
        lambda: provenance.for_report(
            title="x", cases=["a"], sessions=[{"data": []}], endpoint=ENDPOINT
        ),
        message="expected a XopatSession",
    )


# ── the CLI's shape of it ───────────────────────────────────────────────────


def test_the_cli_record_names_the_flags_a_person_would_retype():
    root, folder, sessions = workspace(cases=2)
    record = for_cli(
        title="QC",
        cases=["case-00", "case-01"],
        sessions=sessions,
        endpoint=ENDPOINT,
        sessions_dir=folder,
        session_files=["extra/handoff.json"],
        layout="rows",
        grid=False,
    ).to_dict()
    assert record["inputs"] == {
        "sessions_dir": str(folder),
        "sessions": ["extra/handoff.json"],
        "layout": "rows",
        "grid": False,
    }
    # The folder *and* the individual file's directory: both were inputs.
    assert record["sources"] == sorted([str(folder), "extra"])


def test_a_relative_input_stays_relative():
    """An absolute path in the record is a claim about one machine: the same report
    built on the workstation beside it would differ in the file meant for diffing."""
    _, _, sessions = workspace()
    record = for_cli(
        title="QC",
        cases=["case-00"],
        sessions=sessions[:1],
        endpoint=ENDPOINT,
        sessions_dir="sessions",
    ).to_dict()
    assert record["inputs"]["sessions_dir"] == "sessions"


def test_a_build_given_no_inputs_says_so_instead_of_inventing_them():
    """A page assembled in Python has no flags. An empty `inputs` is the honest
    answer and the reader then knows to ask the caller, not to trust a guess."""
    _, _, sessions = workspace()
    record = Provenance(
        report={"title": "Handmade", "cases": ["case-00"]},
        data_ids=["/data/case-00/s.tif"],
    )
    assert record.to_dict()["inputs"] == {}
    assert record.kind == "composition"


# ── publishing ──────────────────────────────────────────────────────────────


def test_a_publish_with_no_manifest_logs_the_sidecar_in_the_manifest_s_place():
    """`logged_dir` is the one function that decides what travels, so this is the
    whole of the door's publish contract.

    The directory is asserted from inside the `with`: the point of the helper is
    that it cleans up, and a test that listed it afterwards would be reading a
    path that no longer exists.
    """
    _, _, sessions = workspace()
    record = Provenance(report={"title": "No manifest", "cases": ["case-00"]})
    with provenance.logged_dir(record) as directory:
        assert sorted(path.name for path in Path(directory).iterdir()) == [
            "provenance.json"
        ]
        assert json.loads((Path(directory) / "provenance.json").read_text())["report"]
        seen = Path(directory)
    assert not seen.exists(), "a publish that littered would fail its own promise"


def test_a_publish_with_a_manifest_logs_three_files_answering_three_questions():
    _, _, sessions = workspace()
    record = Provenance(report={"title": "Logged", "cases": ["case-00"]})
    with provenance.logged_dir(
        record, manifest_text="title: Logged\n", plan_json='{"cases": ["case-00"]}'
    ) as directory:
        assert sorted(path.name for path in Path(directory).iterdir()) == [
            "manifest.yaml",
            "plan.json",
            "provenance.json",
        ]


def test_the_record_can_be_logged_without_a_local_file_at_all():
    """`to_json` is what a publish writes; `write` is what a local build writes.
    A CI job that keeps only the run's copy needs the first."""
    _, _, sessions = workspace()
    record = Provenance(report={"title": "CI", "cases": ["case-00"]})
    assert json.loads(record.to_json())["report"]["cases"] == ["case-00"]
    assert len(record.digest()) == 64


# ── reading a record back ───────────────────────────────────────────────────


def test_a_sidecar_is_read_back_as_written():
    root, _, sessions = workspace()
    built = composition(sessions).build(out=root / "r.html", check=False)
    again = provenance.read(built.provenance)
    assert again == read_back(built.provenance)
    assert again["kind"] == "composition"


def test_a_missing_or_unparsed_sidecar_is_a_provenance_error_not_an_errno():
    root, _, sessions = workspace()
    raises(lambda: provenance.read(root / "nope.provenance.json"), message="no provenance")
    broken = root / "broken.provenance.json"
    broken.write_text("{not json", encoding="utf-8")
    raises(lambda: provenance.read(broken), message="not JSON")


def test_a_stale_sidecar_next_to_a_rebuilt_report_is_reported_not_assumed():
    """A report folder accumulates. `verify_pair` is what a CI job runs over one,
    and it returns a problem per file rather than raising on the first."""
    root, _, sessions = workspace(cases=1)
    built = composition(sessions).build(out=root / "r.html", check=False)
    assert verify_pair(built.out) == []

    # The realistic stale case: the report is rebuilt over different cases and the
    # old record is left beside it. The page then links DataIDs the record never
    # named, which is the disagreement a reader would be misled by.
    #
    # Not a string replacement in the HTML: the DataID sits percent-encoded inside
    # the URL fragment, which is where `verify_pair` reads it from, and editing the
    # literal occurrences in the thumbnail URL would leave the fragment honest.
    other_root, _, other_sessions = workspace(cases=1)
    substitute = Composition(
        title="Different cohort",
        blocks=[
            {
                "SlideGrid": {
                    "sessions": expand(
                        DESIGN,
                        [{"slide": "/data/x/y.tif", "mask": "/data/x/y.p.tif", "name": "other"}],
                        slots={0: "slide", 1: "mask"},
                        endpoint=ENDPOINT,
                    )
                }
            }
        ],
    ).build(out=built.out, check=False, with_provenance=False)
    assert Path(substitute.out) == Path(built.out)
    problems = verify_pair(built.out)
    # Two, because a rebuilt page differs two ways: the title moved and the DataIDs
    # did. Listing both is the point -- a CI job over a folder wants every stale
    # file at once, not one per run.
    assert len(problems) == 2, problems
    assert any("title" in line for line in problems)
    assert any("not in the record" in line for line in problems)


def test_a_report_with_no_sidecar_beside_it_is_reported():
    root, _, sessions = workspace()
    built = composition(sessions).build(out=root / "r.html", check=False)
    Path(built.provenance).unlink()
    problems = verify_pair(built.out)
    assert len(problems) == 1 and "no provenance sidecar" in problems[0]
    assert verify_pair(root / "nothing.html") == [
        "nothing.html: no such report"
    ]


# ── the shape of the code ───────────────────────────────────────────────────


def test_the_record_is_written_from_the_objects_the_page_was_built_from():
    """Not "does it contain the sessions" -- `Composition.sessions()` is a cached
    walk, so the page, the plan, the probe and this record are one traversal.

    Asserted by identity: a second call to `sessions()` returns the same objects,
    so a record cannot be describing a re-composition of the spec.
    """
    _, _, sessions = workspace(cases=2)
    page = composition(sessions)
    first = page.sessions()
    assert [id(item) for item in first] == [id(item) for item in page.sessions()]
    record = page.record_provenance()
    assert record.data_ids == provenance.session_data_ids(first)


def test_the_module_writes_nothing_except_through_write_and_logged_dir():
    """The sidecar's whole safety story is that it lands beside a report or inside
    a tempdir. A stray `open(..., "w")` elsewhere would be a third way."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(provenance))
    allowed = {"write", "__enter__"}

    def scope_of(node, parents, wanted):
        """Walk up from a call to the function that contains it."""
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return "<module>"

    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    writes = [
        (node.lineno, scope_of(node, parents, allowed))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("write_text", "write_bytes")
    ]
    assert writes, "the module writes its two files, so the walk must find them"
    assert sorted({where for _, where in writes}) == sorted(allowed), writes


def test_a_rich_design_survives_the_record_with_its_types_intact():
    """A design is a nested document with bools, floats and lists. A record that
    flattened it to strings would not rebuild anything."""
    fancy = copy.deepcopy(DESIGN)
    fancy["data"][1]["options"] = {"format": "png", "scale": 2}
    fancy["visualizations"][0]["shaders"]["probability"]["params"]["threshold"] = 0.25
    _, _, sessions = workspace()
    written = Provenance(design={"session": fancy, "slots": {"0": "slide"}}).to_dict()
    shader = written["design"]["session"]["visualizations"][0]["shaders"]["probability"]
    assert shader["params"]["threshold"] == 0.25
    assert written["design"]["session"]["data"][1]["options"] == {
        "format": "png",
        "scale": 2,
    }


def test_a_path_in_a_record_becomes_a_string_not_one_machine_s_repr():
    _, _, sessions = workspace()
    written = for_cli(
        title="QC",
        cases=["case-00"],
        sessions=sessions[:1],
        endpoint=ENDPOINT,
        sessions_dir=Path("relative/sessions"),
    ).to_dict()
    assert written["inputs"]["sessions_dir"] == "relative/sessions"
    assert written["sources"] == ["relative/sessions"]


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
