"""Tests for the CLI: ``reportfast plan | build | find``.

Everything runs ``main(argv)`` in-process against a temporary workspace of fake
images -- no mount, no tile server, no tracking server. The probe is switched off
where a build is under test and stubbed where the probe is; exit codes are the
contract an agent or a CI job reads, so they are asserted as strictly as the
output.

Run:      cd /home/jovyan/report_fast && python tests/test_cli.py
Pytest:   pytest tests/test_cli.py
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_fast import manifest  # noqa: E402
from report_fast import mlflow as mlflow_module  # noqa: E402
from report_fast import verify as verify_module  # noqa: E402
from report_fast.__main__ import main  # noqa: E402
from report_fast.mlflow import MlflowError, Published  # noqa: E402
from report_fast.verify import Check  # noqa: E402


def workspace(cases=("case_001", "case_002"), masks=("case_001",)) -> Path:
    """A manifest beside a background folder and a mask folder."""
    root = Path(tempfile.mkdtemp())
    for names, which in ((cases, "slides"), (masks, "masks")):
        directory = root / which
        directory.mkdir(parents=True)
        for stem in names:
            (directory / f"{stem}.tif").write_bytes(b"not really an image")
    (root / "r.yaml").write_text(
        "title: CLI report\n"
        f"background: {{drive: {root / 'slides'}}}\n"
        f"masks:\n  - {{name: Tissue, drive: {root / 'masks'}, color: '#ffff00'}}\n",
        encoding="utf-8",
    )
    return root


def run(*argv) -> tuple:
    """``main(argv)`` with stdout/stderr captured; returns ``(code, out, err)``."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def session_root(cases=("case-01", "case-02"), *, broken=None) -> Path:
    """A folder of authored session JSON, the way `--sessions-dir` reads it.

    Written out by hand rather than via `temp_dir_sessions` so the filenames are
    the case names: the helper numbers its files, which is right for a round trip
    and wrong for a test about card order.
    """
    from report_fast.compose import load_design
    from report_fast.xopat import XopatEndpoint

    endpoint = XopatEndpoint(
        base_url="https://xopat.test/xopat/",
        wsi_base_url="https://tiles.test/wsi/",
        image_protocol="wsi_service",
        mount_root="/data",
    )
    design = {
        "params": {"sessionName": "design"},
        "data": [
            {"dataID": "/data/x/a.tif", "protocol": "wsi_service"},
            {"dataID": "/data/x/a.prob.tif", "protocol": "wsi_service"},
        ],
        "background": [
            {"id": "s", "name": "design", "dataReference": 0, "visualizationIndex": 0}
        ],
        "visualizations": [
            {
                "name": "design",
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
    root = Path(tempfile.mkdtemp())
    sessions = root / "sessions"
    sessions.mkdir()
    template = load_design(design, slots={0: "slide", 1: "mask"}, endpoint=endpoint)
    for name in cases:
        session = template.bind(
            slide=f"/data/{name}/s.tif", mask=f"/data/{name}/p.tif", name=name
        )
        (sessions / f"{name}.json").write_text(
            json.dumps(session.to_config(), indent=1), encoding="utf-8"
        )
    if broken:
        document = json.loads((sessions / f"{cases[0]}.json").read_text(encoding="utf-8"))
        document["params"][broken] = 0.5
        (sessions / f"zz-{broken}.json").write_text(
            json.dumps(document), encoding="utf-8"
        )
    (root / "design.json").write_text(json.dumps(design, indent=1), encoding="utf-8")
    return root


def stub_compose_probe(answers):
    """Patch `verify.probe` -- the door imports it inside `build`, by name.

    Not the same as `stub_probe` above, and the difference is the reason both
    exist: `manifest` binds `probe` at import, `compose` binds it at call. Patching
    the wrong one tests the network.
    """
    real = verify_module.probe
    verify_module.probe = lambda ids, *a, **k: answers(list(ids))
    return lambda: setattr(verify_module, "probe", real)


# ── the manifest-less door: --sessions-dir / --session / --design ───────────


def stub_probe(answers):
    """Replace the probe manifest calls, returning the restorer.

    Patched on ``report_fast.manifest``, not ``verify``: manifest imports the
    function by name at module load, so a patch on ``verify`` would leave the
    build path calling the real network.
    """
    real = manifest.probe
    manifest.probe = lambda ids, *a, **k: answers(list(ids))
    return lambda: setattr(manifest, "probe", real)


# ── plan: the command that cannot write ─────────────────────────────────────


def test_plan_prints_the_numbers_and_writes_nothing():
    root = workspace()
    code, out, _ = run("plan", str(root / "r.yaml"))
    assert code == 0
    assert "CLI report: 2 cases, up to 1 overlays" in out
    assert "Tissue: 1/2" in out  # the coverage number a reader checks first
    assert "publish  not set" in out
    assert not list(root.glob("*.html")), "plan must not write anything"


def test_plan_json_is_machine_readable():
    root = workspace()
    code, out, _ = run("plan", str(root / "r.yaml"), "--json")
    assert code == 0
    payload = json.loads(out)  # stdout is pure JSON; warnings belong to stderr
    assert payload["cases"] == ["case_001", "case_002"]
    assert payload["coverage"] == {"Tissue": 1}
    assert payload["warnings"]  # Tissue is on one case of two, and says so


def test_a_manifest_error_is_exit_1_said_in_manifest_terms():
    root = workspace()
    (root / "bad.yaml").write_text(
        f"title: Bad\nmin_layer: 3\nbackground: {{drive: {root / 'slides'}}}\n",
        encoding="utf-8",
    )
    code, _, err = run("plan", str(root / "bad.yaml"))
    assert code == 1
    assert "min_layer" in err and "min_layers" in err  # rejected, and corrected
    assert "Traceback" not in err


def test_a_layer_nobody_gets_is_exit_1_not_a_silently_smaller_report():
    # The expensive failure this gate exists for: a mask whose run id is one
    # character off would otherwise produce a report that looks finished and is
    # missing a layer on every single case.
    root = workspace()
    (root / "nowhere").mkdir()  # exists and is empty: the source is fine, the layer is not
    (root / "ghost.yaml").write_text(
        "title: Ghost\n"
        f"background: {{drive: {root / 'slides'}}}\n"
        f"masks:\n  - {{name: GhostLayer, drive: {root / 'nowhere'}}}\n",
        encoding="utf-8",
    )
    code, _, err = run("plan", str(root / "ghost.yaml"))
    assert code == 1
    assert "GhostLayer" in err and "Not one of these cases" in err
    assert not list(root.glob("*.html"))


def test_a_source_that_is_not_on_the_machine_is_exit_1_with_the_path():
    root = workspace()
    (root / "ghost.yaml").write_text(
        "title: Ghost\n"
        f"background: {{drive: {root / 'slides'}}}\n"
        f"masks:\n  - {{name: Ghost, drive: {root / 'masks'}/nowhere}}\n",
        encoding="utf-8",
    )
    code, _, err = run("plan", str(root / "ghost.yaml"))
    assert code == 1
    assert "nowhere" in err and "not on this machine" in err


def test_a_missing_manifest_is_exit_4_not_a_traceback():
    code, _, err = run("plan", "/no/such/r.yaml")
    assert code == 4
    assert "No manifest" in err and "Traceback" not in err


def test_a_bad_flag_is_exit_4_not_argparse_default_2():
    # 2 is reserved for "built, and a DataID did not resolve". Two failures that
    # want different fixes must not share an exit code.
    code, _, err = run("plan", "--nope", "r.yaml")
    assert code == 4
    assert "--nope" in err and "Traceback" not in err


def test_no_command_prints_help_and_exits_4():
    code, out, _ = run()
    assert code == 4
    assert "usage:" in out
    for command in ("plan", "build", "find"):
        assert command in out


def test_endpoint_flags_reach_the_report():
    root = workspace()
    target = root / "page.html"
    code, _, _ = run(
        "build",
        str(root / "r.yaml"),
        "--no-check",
        "-o",
        str(target),
        "--base-url",
        "https://other.test/v3/",
        "--mount-root",
        "",
    )
    assert code == 0
    html = target.read_text(encoding="utf-8")
    assert "https://other.test/v3/" in html
    # With no mount root the DataIDs are the paths as written -- the form for a
    # deployment whose protocol template interpolates a whole server-side path.
    assert str(root / "slides" / "case_001.tif") in html


# ── build: one file, then the probe ─────────────────────────────────────────


def test_build_writes_the_html_and_reports_where():
    root = workspace()
    code, out, _ = run("build", str(root / "r.yaml"), "--no-check")
    assert code == 0
    written = root / "r.html"  # default: beside the manifest
    assert written.exists()
    assert "wrote" in out and str(written) in out
    assert "CLI report" in written.read_text(encoding="utf-8")


def test_build_out_flag_moves_the_file_and_the_plan_says_so():
    root = workspace()
    target = root / "dist" / "page.html"
    code, out, _ = run("build", str(root / "r.yaml"), "--no-check", "-o", str(target))
    assert code == 0
    assert target.exists()
    # The printed plan names the file that was actually written, not the
    # manifest's guess: a plan pointing at a file nobody wrote is untrustworthy,
    # and this is the same text logged beside a published report.
    assert str(target) in out
    assert not (root / "r.html").exists()


def test_build_check_only_writes_no_file():
    root = workspace()
    restore = stub_probe(lambda ids: [Check(item, 200, "openable") for item in ids])
    try:
        code, out, _ = run("build", str(root / "r.yaml"), "--check-only")
    finally:
        restore()
    assert code == 0
    assert not list(root.glob("*.html")), "--check-only must not write"
    assert "nothing" in out


def test_a_refused_dataid_is_exit_2():
    root = workspace()
    restore = stub_probe(
        lambda ids: [
            Check(ids[0], 200, "openable"),
            Check("ghost/file.tif", 404, "not found"),
        ]
    )
    try:
        code, out, err = run("build", str(root / "r.yaml"))
    finally:
        restore()
    assert code == 2  # the HTML exists and some of it would not open
    assert "404" in out and "ghost/file.tif" in out
    assert "black card" in err


def test_every_link_opening_is_exit_0():
    root = workspace()
    restore = stub_probe(lambda ids: [Check(item, 200, "openable") for item in ids])
    try:
        code, out, _ = run("build", str(root / "r.yaml"))
    finally:
        restore()
    assert code == 0
    assert f"{len(workspace_data_ids(root))}/{len(workspace_data_ids(root))} DataIDs open" in out


def workspace_data_ids(root) -> list:
    """The DataIDs the built report links to, for the count in the assertion."""
    html = (root / "r.html").read_text(encoding="utf-8")
    return verify_module.data_ids_from_html(html)


def test_being_offline_is_not_reported_as_a_broken_report():
    # A probe the network could not answer is not a verdict on the DataIDs.
    # Exiting 2 for being offline would fail CI on a Tuesday for no fault of the
    # manifest.
    root = workspace()
    restore = stub_probe(lambda ids: [Check(item, None, "connection refused") for item in ids])
    try:
        code, out, _ = run("build", str(root / "r.yaml"))
    finally:
        restore()
    assert code == 0
    assert "unreachable" in out


# ── publishing: asked for, out loud ─────────────────────────────────────────


def test_no_workflow_publishes():
    """Publishing is never a side effect, and CI is where an implied one would
    arrive: someone adds `&& reportfast build --publish` to a green pipeline and
    every push starts uploading reports.

    So the rule is checked against the workflows themselves rather than left to
    review. A commented line is allowed -- this repository documents the rule in
    its workflow header precisely so it is not rediscovered -- but no executed step
    may name the flag, and none may carry tracking credentials.
    """
    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    if not root.is_dir():
        return  # no CI yet; the rule is that the day there is, it does not publish
    for path in sorted(root.glob("*.yml")) + sorted(root.glob("*.yaml")):
        # Parsed, not grepped. A workflow with a syntax error never runs at all, so
        # a guard that only reads lines can wave through a file whose steps it is
        # failing to read -- which is the exact way this rule comes back.
        #
        # Imported here rather than at the top of the file: YAML is an optional
        # extra, and a test about CI should not be the thing that makes the suite
        # unrunnable without it.
        try:
            import yaml
        except ImportError:  # pragma: no cover - only without the manifest extra
            return

        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise AssertionError(f"{path.name} is not valid YAML: {error}") from error
        executed = [
            step.get("run", "")
            for job in (loaded.get("jobs") or {}).values()
            for step in (job.get("steps") or [])
        ]
        assert executed, f"{path.name} runs nothing; a green check that runs nothing is worse than none"
        for line in executed:
            assert "--publish" not in line, f"{path.name} would publish: {line.strip()}"
            assert "MLFLOW_TRACKING" not in line, f"{path.name} holds credentials: {line}"


def test_publish_without_a_target_refuses_before_uploading():
    root = workspace()
    code, _, err = run("build", str(root / "r.yaml"), "--no-check", "--publish")
    assert code == 1
    assert "no publish:" in err
    assert "publish   no run" in err  # said on stderr before the attempt as well
    assert (root / "r.html").exists()  # the build itself still happened


def test_publish_names_the_run_before_it_uploads():
    # The run id has to end up on the record. Not a prompt: a prompt is
    # unreadable in a log and auto-answered by anything non-interactive.
    root = workspace()
    (root / "r.yaml").write_text(
        (root / "r.yaml").read_text(encoding="utf-8") + "publish: run-abc123\n",
        encoding="utf-8",
    )
    calls = []

    def refuse(self, *args, **kwargs):
        calls.append(args)
        raise MlflowError("test: no upload")

    real = mlflow_module.Mlflow.publish
    mlflow_module.Mlflow.publish = refuse
    try:
        code, _, err = run("build", str(root / "r.yaml"), "--no-check", "--publish")
    finally:
        mlflow_module.Mlflow.publish = real
    assert "publish   -> run run-abc123" in err
    assert code == 4  # a tracking failure is usage-shaped, not a manifest bug
    assert "no upload" in err and "Traceback" not in err
    assert calls, "the announcement should precede the upload attempt"


def test_check_only_and_publish_are_opposites():
    # --check-only is the flag you type precisely so nothing changes. That it also
    # had a --publish on it is the kind of thing a shell history repeats, and the
    # upload must not ride along on a flag whose whole purpose is avoiding writes.
    # The refusal is printed before any `publish ->` line, so the log never reads
    # as though an upload was attempted.
    root = workspace()
    (root / "r.yaml").write_text(
        (root / "r.yaml").read_text(encoding="utf-8") + "publish: run-abc123\n",
        encoding="utf-8",
    )
    uploaded = []
    real = mlflow_module.Mlflow.publish
    mlflow_module.Mlflow.publish = lambda self, *a, **k: uploaded.append(a)
    try:
        code, _, err = run("build", str(root / "r.yaml"), "--check-only", "--publish")
    finally:
        mlflow_module.Mlflow.publish = real
    assert code == 4
    assert "contradict" in err
    assert "publish   ->" not in err
    assert not uploaded
    assert not list(root.glob("*.html"))


def test_the_library_allows_publishing_without_writing_locally():
    # The asymmetry is deliberate. The CLI refuses `--check-only --publish`
    # because those flags can come from a shell history and --check-only promises
    # nothing changes; a caller who writes out=False + publish=True by hand has
    # asked for both, and "build in memory, keep only the run's copy" is what CI
    # wants. The report travels even though no file was ever written here.
    from report_fast.manifest import build as library_build

    root = workspace()
    sent = []

    def capture(self, report, run_id=None, extra_dir=None):
        # Read the staging directory from inside the call: it is a tempdir and the
        # publish call is the only moment it exists.
        staged = sorted(p.name for p in Path(extra_dir).iterdir()) if extra_dir else []
        sent.append((report.to_html(), run_id, staged, Path(extra_dir)))
        return Published(run_id=run_id, artifact="report/report.html", url="u")

    real = mlflow_module.Mlflow.publish
    mlflow_module.Mlflow.publish = capture
    try:
        built = library_build(str(root / "r.yaml"), out=False, publish=True, run_id="run-x")
    finally:
        mlflow_module.Mlflow.publish = real

    assert built.out is None
    assert not list(root.glob("*.html")), "out=False must not write, publish or not"
    assert len(sent) == 1 and sent[0][1] == "run-x"
    assert "CLI report" in sent[0][0]  # the report itself went up
    # The declared manifest and the resolved plan travel with it, so the run can
    # answer what it contains years later -- same contract as a normal publish.
    # Three, not two: the manifest is what was meant, plan.json is what resolved,
    # and provenance.json names the endpoint, viewer stamp and DataIDs the links
    # carry. `out=False` means none of them exists as a local file.
    assert sent[0][2] == ["manifest.yaml", "plan.json", "provenance.json"]
    assert not sent[0][3].exists(), "the staging directory is a tempdir; it must not leak"


def test_a_check_only_build_still_probes_without_writing():
    # The half of the pair that is allowed: --check-only on its own builds in
    # memory, asks the tile server, and leaves the directory exactly as it was.
    root = workspace()
    restore = stub_probe(lambda ids: [Check(item, 200, "openable") for item in ids])
    try:
        code, out, _ = run("build", str(root / "r.yaml"), "--check-only")
    finally:
        restore()
    assert code == 0 and not list(root.glob("*.html"))
    assert "wrote     nothing (--check-only)" in out


def test_run_flag_overrides_the_manifest_and_says_which_was_used():
    root = workspace()
    (root / "r.yaml").write_text(
        (root / "r.yaml").read_text(encoding="utf-8") + "publish: run-from-manifest\n",
        encoding="utf-8",
    )
    real = mlflow_module.Mlflow.publish
    mlflow_module.Mlflow.publish = lambda self, *a, **k: (_ for _ in ()).throw(
        MlflowError("stop")
    )
    try:
        code, _, err = run(
            "build", str(root / "r.yaml"), "--no-check", "--publish", "--run", "run-flag"
        )
    finally:
        mlflow_module.Mlflow.publish = real
    assert "publish   -> run run-flag" in err
    assert "run-from-manifest" not in err  # the flag won, and says so
    assert code == 4


# ── --version: the line that goes in a bug report ───────────────────────────


def test_version_names_the_tool_and_the_viewer_it_was_verified_against():
    """Two answers to "which version", and a report needs both.

    A session that gates cleanly against viewer 3.1.0 can still be refused by a
    deployment on a newer commit -- the contract is derived from a viewer checkout,
    so the tool version alone does not describe what a page was verified against.
    """
    code, out, _ = run("--version")
    assert code == 0, out
    assert out.startswith("report-fast "), out
    assert "viewer 3.1.0 @" in out, out


def test_version_survives_an_unreadable_contract():
    """`--version` is what someone types when nothing else works.

    It must not be the one command that dies on a missing contract file, so the
    stamp is read inside a try and the tool version is printed regardless.
    """
    from report_fast import contract

    real = contract.viewer_stamp
    contract.viewer_stamp = lambda: (_ for _ in ()).throw(FileNotFoundError("gone"))
    try:
        code, out, _ = run("--version")
    finally:
        contract.viewer_stamp = real
    assert code == 0, out
    assert "report-fast " in out
    assert "unreadable" in out, out


# ── find: listing, not discovery ────────────────────────────────────────────


def test_find_admits_it_cannot_search():
    # Honest limits over an implied promise: there is no search_runs behind this,
    # so the help text says what it does instead of sounding like discovery.
    code, out, _ = run("find", "--help")
    assert code == 0
    assert "Not run discovery" in out


def test_find_without_a_run_id_is_a_usage_error():
    code, _, err = run("find")
    assert code == 4
    assert "RUN_ID" in err or "run_id" in err


class _StubFlow:
    """The two calls `find` makes, recorded instead of sent."""

    calls: list = []
    files = [
        "mflow/111/run-1/artifacts/tissue_masks/case_001.tif",
        "mflow/111/run-1/artifacts/tissue_masks/case_002.tif",
    ]

    @classmethod
    def from_env(cls, **kwargs):
        cls.calls = [("from_env", kwargs)]
        return cls()

    @classmethod
    def data_ids(cls, run_id, path="", recursive=False):
        cls.calls.append(("data_ids", run_id, path, recursive))
        return list(cls.files)

    @classmethod
    def slides(cls, run_id, path="", *, patterns=None, recursive=False):
        cls.calls.append(("slides", run_id, path, recursive))
        return list(cls.files)[:1]

    @classmethod
    def link(cls, run_id, experiment_id=None):
        return f"https://mlflow.test/#/runs/{run_id}"


def test_find_lists_a_run_and_prints_data_ids():
    real = mlflow_module.Mlflow
    mlflow_module.Mlflow = _StubFlow
    try:
        code, out, _ = run("find", "run-1", "--path", "tissue_masks")
        assert code == 0
        assert ("data_ids", "run-1", "tissue_masks", False) in _StubFlow.calls
        assert "case_001.tif" in out and "case_002.tif" in out
        assert "https://mlflow.test/#/runs/run-1" in out

        code, out, _ = run("find", "run-1", "--data-ids")
        assert code == 0
        # Pure lines, for piping into a manifest row or a shell loop.
        assert out == "\n".join(_StubFlow.files) + "\n"

        code, out, _ = run("find", "run-1", "--slides", "-r")
        assert code == 0
        assert ("slides", "run-1", "", True) in _StubFlow.calls
    finally:
        mlflow_module.Mlflow = real


def test_a_tracking_failure_is_exit_4_not_a_manifest_error():
    # One MlflowError type covers "the extra is missing" and "that run does not
    # exist"; only the first is fixed by installing something.
    def refuse(self, *args, **kwargs):
        raise MlflowError("Could not read run abc (get_run): RESOURCE_DOES_NOT_EXIST")

    real = mlflow_module.Mlflow.client
    mlflow_module.Mlflow.client = refuse
    try:
        code, _, err = run("find", "abc", "--tracking-uri", "http://mlflow.test:5000/")
    finally:
        mlflow_module.Mlflow.client = real
    assert code == 4
    assert "RESOURCE_DOES_NOT_EXIST" in err
    assert "uv sync" not in err  # do not tell someone to install things


# ── missing optional dependencies ───────────────────────────────────────────


def test_a_missing_pyyaml_says_which_extra_to_sync():
    real = manifest._yaml

    def without_yaml():
        raise manifest.ManifestNoYaml("Manifests are YAML and PyYAML is not installed.")

    manifest._yaml = without_yaml
    try:
        root = workspace()
        code, _, err = run("plan", str(root / "r.yaml"))
    finally:
        manifest._yaml = real
    assert code == 3
    assert "PyYAML" in err and "uv sync --extra manifest" in err


def test_a_missing_mlflow_says_which_extra_to_sync():
    def refuse(self):
        raise MlflowError("mlflow is not installed -- it is an optional extra.")

    real = mlflow_module.Mlflow.client
    mlflow_module.Mlflow.client = refuse
    try:
        code, _, err = run("find", "abc", "--tracking-uri", "http://mlflow.test:5000/")
    finally:
        mlflow_module.Mlflow.client = real
    assert code == 3
    assert "uv sync --extra mlflow" in err


# ── the manifest-less door ──────────────────────────────────────────────────


def test_build_from_a_sessions_dir_writes_one_file_and_nothing_else():
    root = session_root()
    out = root / "report.html"
    code, printed, _ = run(
        "build", "--sessions-dir", str(root / "sessions"),
        "--title", "Cohort QC", "-o", str(out), "--no-check",
    )
    assert code == 0, printed
    html = out.read_text(encoding="utf-8")
    assert "Cohort QC" in html and "case-01" in html and "case-02" in html
    # "nothing is persisted except the HTML and its provenance sidecar" is the
    # ruling; assert the nothing too. No manifest, no plan file, no session copy.
    assert sorted(p.name for p in root.iterdir()) == [
        "design.json",
        "report.html",
        "report.provenance.json",
        "sessions",
    ]
    # The sidecar is a *record*, not a second copy of the inputs: it names the
    # folder it read, it does not re-state the sessions inside it.
    record = json.loads((root / "report.provenance.json").read_text(encoding="utf-8"))
    assert record["kind"] == "composition"
    assert record["inputs"] == {"sessions_dir": str(root / "sessions"), "layout": "grid"}
    assert record["sources"] == [str(root / "sessions")]
    # Flat strings, not the session documents: the question a reader brings is
    # "which files does this report claim exist", and `data` entries carry a dozen
    # fields that say nothing about that.
    assert all(isinstance(item, str) for item in record["data_ids"])
    assert len(record["data_ids"]) == 4, "two cases, one slide and one mask each"
    assert record["viewer"]["version"]


def test_the_door_reads_a_session_argument_as_well_as_a_folder():
    root = session_root(cases=("case-01",))
    extra = root / "solo.json"
    extra.write_text((root / "sessions/case-01.json").read_text(encoding="utf-8"))
    code, printed, _ = run(
        "build", "--session", str(extra),
        "--sessions-dir", str(root / "sessions"),
        "-o", str(root / "r.html"), "--no-check",
    )
    assert code == 0, printed
    # The explicit one first, then the folder: the grid order is the report order.
    assert "2 cases" in printed


def test_a_session_argument_accepts_inline_json():
    root = session_root()
    document = json.loads((root / "sessions/case-01.json").read_text(encoding="utf-8"))
    code, printed, _ = run(
        "build", "--session", json.dumps(document), "-o", str(root / "r.html"), "--no-check"
    )
    assert code == 0, printed
    assert "1 cases" in printed, printed


def test_the_door_probes_through_the_same_probe_and_reports_it():
    root = session_root(cases=("case-01", "case-02"))
    asked = []

    def answers(ids):
        asked.extend(ids)
        return [Check(item, 200, "openable") for item in ids]

    restore = stub_compose_probe(answers)
    try:
        code, printed, _ = run(
            "build", "--sessions-dir", str(root / "sessions"), "-o", str(root / "r.html")
        )
    finally:
        restore()
    assert code == 0, printed
    assert "probe" in printed
    # Every data[] entry of every session, which is what `session_data_ids` means.
    assert len(asked) == 4, asked


def test_a_refused_dataid_is_exit_2_from_this_door_too():
    root = session_root(cases=("case-01",))
    restore = stub_compose_probe(lambda ids: [Check(item, 404, "no such file") for item in ids])
    try:
        code, _, err = run(
            "build", "--sessions-dir", str(root / "sessions"), "-o", str(root / "r.html")
        )
    finally:
        restore()
    assert code == 2
    assert "would not open" in err


def test_a_check_only_door_build_writes_no_file_anywhere():
    """Pinned because it did write one.

    `Composition.build` was coalescing a falsy `out` to `report.html`, and the CLI
    passes `out=False` for `--check-only` -- so the run printed "wrote nothing"
    beside a file it had just made in the caller's directory. The same trap the
    manifest path documents at its own `out is not False` check.
    """
    root = session_root()
    inside = root / "cwd"
    inside.mkdir()
    import os

    where = os.getcwd()
    os.chdir(inside)
    try:
        code, printed, _ = run(
            "build", "--sessions-dir", str(root / "sessions"), "--check-only", "--no-check"
        )
    finally:
        os.chdir(where)
    assert code == 0, printed
    assert "wrote     nothing (--check-only)" in printed
    assert list(inside.iterdir()) == [], list(inside.iterdir())


def test_no_grid_lays_one_card_per_row():
    root = session_root()
    for flag, want in (("--layout", "rows"), ("--no-grid", None)):
        out = root / f"{flag.strip('-')}.html"
        argv = ["build", "--sessions-dir", str(root / "sessions"), "-o", str(out), "--no-check"]
        code, _, err = run(*(argv + ([flag, "rows"] if want else [flag])))
        assert code == 0, err
        # The grid class is the grid's; a row of cards does not have it.
        assert "rf-slide-grid" not in out.read_text(encoding="utf-8")
    assert sorted(p.name for p in root.iterdir()) == [
        "design.json",
        "layout.html",
        "layout.provenance.json",
        "no-grid.html",
        "no-grid.provenance.json",
        "sessions",
    ]


def test_a_bad_session_in_the_folder_is_exit_1_naming_the_file_and_the_path():
    # The exit-1/exit-4 split, which is the whole point of two exception types:
    # one file among two is an errand about that file's contents.
    root = session_root(broken="threshhold")
    code, _, err = run(
        "build", "--sessions-dir", str(root / "sessions"), "-o", str(root / "r.html"), "--no-check"
    )
    assert code == 1
    assert "zz-threshhold.json" in err, err
    assert "params.threshhold" in err, err
    assert not (root / "r.html").exists(), "a refused build writes no report"


def test_a_missing_folder_or_file_is_exit_4_and_not_a_spec_error():
    root = session_root()
    code, _, err = run("build", "--sessions-dir", str(root / "nope"), "--no-check")
    assert code == 4 and "not a directory" in err
    code, _, err = run("build", "--session", str(root / "nope.json"), "--no-check")
    assert code == 4

    empty = root / "empty"
    empty.mkdir()
    code, _, err = run("build", "--sessions-dir", str(empty), "--no-check")
    assert code == 4 and "holds no" in err


def test_publishing_from_the_door_uploads_the_record_in_the_manifest_s_place():
    """There is no manifest to log, so the sidecar is the run's configuration.

    This is the half of decision 8 that step 5 was waiting for. Asserted on what
    the fake client *saw*, read from inside the call: the staging directory is a
    tempdir and `logged_dir` removes it on the way out.
    """
    root = session_root()
    sent = []

    def capture(self, report, run_id=None, extra_dir=None):
        staged = sorted(path.name for path in Path(extra_dir).iterdir()) if extra_dir else []
        inside = (
            {path.name: path.read_text(encoding="utf-8") for path in Path(extra_dir).iterdir()}
            if extra_dir
            else {}
        )
        sent.append((run_id, staged, inside, str(extra_dir)))
        return Published(run_id=run_id, artifact="report/report.html", url="u")

    real = mlflow_module.Mlflow.publish
    mlflow_module.Mlflow.publish = capture
    try:
        code, printed, err = run(
            "build", "--sessions-dir", str(root / "sessions"),
            "-o", str(root / "r.html"), "--no-check", "--publish", "--run", "run-door",
        )
    finally:
        mlflow_module.Mlflow.publish = real
    assert code == 0, printed
    (run_id, staged, inside, staging), = sent
    assert run_id == "run-door"
    # One file, not the manifest pair: nothing was declared here to log.
    assert staged == ["provenance.json"]
    record = json.loads(inside["provenance.json"])
    assert record["kind"] == "composition"
    assert record["inputs"]["sessions_dir"] == str(root / "sessions")
    assert not (Path(staging).exists()), "the staging directory is not left behind"
    assert "-> run run-door" in err and "never implied" in err
    assert "published report/report.html on run run-door" in printed


def test_publish_from_the_door_names_the_run_it_writes_to_or_refuses():
    """Two ways to be short of an instruction, and neither uploads anything.

    `--run` without `--publish` names a destination and implies a write, which is
    the one implication the publishing rule is about; `--publish` without a run has
    no manifest `publish:` to fall back to, so there is nothing to guess from.
    """
    root = session_root()
    sent = []
    real = mlflow_module.Mlflow.publish
    mlflow_module.Mlflow.publish = lambda *a, **k: sent.append(a)
    try:
        code, _, err = run(
            "build", "--sessions-dir", str(root / "sessions"),
            "-o", str(root / "a.html"), "--no-check", "--run", "abc123",
        )
        assert code == 4 and "never implied" in err, err
        code, _, err = run(
            "build", "--sessions-dir", str(root / "sessions"),
            "-o", str(root / "b.html"), "--no-check", "--publish",
        )
        assert code == 4 and "needs a run" in err, err
    finally:
        mlflow_module.Mlflow.publish = real
    assert sent == [], "a refused publish moved nothing"
    assert not (root / "a.html").exists() and not (root / "b.html").exists(), (
        "the refusal comes before the build, so no file either"
    )


def test_a_door_publish_refuses_to_promise_nothing_and_upload_something():
    """`--check-only --publish` is refused for the same reason on both doors: one
    flag promises nothing is written and the other writes to a server."""
    root = session_root()
    code, _, err = run(
        "build", "--sessions-dir", str(root / "sessions"),
        "--check-only", "--no-check", "--publish", "--run", "run-x",
    )
    assert code == 4 and "--check-only" in err, err


def test_a_door_publish_without_o_goes_to_the_run_and_stays_there():
    """`--run` named the destination; a local copy has to be asked for.

    Run from an empty directory of its own, because the whole point is what is not
    left in it. The report that leaked this way landed as `report.html` beside
    whatever command preceded it, which is a file the caller never named.
    """
    root = session_root()
    elsewhere = Path(tempfile.mkdtemp())
    sent = []

    def capture(self, report, run_id=None, extra_dir=None):
        sent.append((run_id, report.to_html(), sorted(
            path.name for path in Path(extra_dir).iterdir()
        ) if extra_dir else []))
        return Published(run_id=run_id, artifact="report/report.html", url="u")

    real = mlflow_module.Mlflow.publish
    mlflow_module.Mlflow.publish = capture
    before = set(elsewhere.iterdir())
    cwd = Path.cwd()
    try:
        os.chdir(elsewhere)
        code, printed, _ = run(
            "build", "--sessions-dir", str(root / "sessions"),
            "--no-check", "--publish", "--run", "run-remote",
        )
    finally:
        mlflow_module.Mlflow.publish = real
        os.chdir(cwd)
    assert code == 0, printed
    (run_id, html, staged), = sent
    assert run_id == "run-remote" and "<html" in html.lower()
    assert staged == ["provenance.json"], "the run still gets its record"
    assert set(elsewhere.iterdir()) == before, (
        f"a publish wrote into the working directory: {set(elsewhere.iterdir()) - before}"
    )
    assert "wrote     the run only" in printed and "-o FILE" in printed
    assert "--check-only" not in printed, (
        "the line must not claim a flag that was never passed"
    )
    assert "published report/report.html on run run-remote" in printed


def test_a_door_publish_with_o_keeps_the_local_copy_and_its_sidecar():
    """`-o` is still the way to ask for both: the page and its sidecar here, the
    page and its record there."""
    root = session_root()
    real = mlflow_module.Mlflow.publish
    mlflow_module.Mlflow.publish = lambda self, report, run_id=None, extra_dir=None: (
        Published(run_id=run_id, artifact="report/report.html", url="u")
    )
    try:
        code, printed, _ = run(
            "build", "--sessions-dir", str(root / "sessions"),
            "-o", str(root / "both.html"), "--no-check", "--publish", "--run", "run-two",
        )
    finally:
        mlflow_module.Mlflow.publish = real
    assert code == 0, printed
    assert (root / "both.html").exists() and (root / "both.provenance.json").exists()
    assert f"wrote     {root / 'both.html'}" in printed
    assert "the run only" not in printed


def test_design_and_sessions_dir_are_two_questions_not_one_command():
    root = session_root()
    code, _, err = run(
        "plan", "--design", str(root / "design.json"),
        "--sessions-dir", str(root / "sessions"),
    )
    assert code == 4
    assert "two commands" in err, err


def test_plan_design_validates_one_design_and_shows_its_slots():
    root = session_root()
    code, printed, _ = run(
        "plan", "--design", str(root / "design.json"),
        "--slot", "0=slide", "--slot", "1=mask",
    )
    assert code == 0, printed
    assert "data[0] = slide" in printed and "data[1] = mask" in printed
    assert "design.json" in printed


def test_plan_design_shows_an_unslotable_index_as_fixed_not_as_a_mistake():
    """A design with a shared reference atlas at index 2 is intentional. The first
    reading of it is usually "the caller forgot a slot", so the output has to say
    which of the two it thinks it is looking at."""
    root = session_root()
    path = root / "atlas.json"
    document = json.loads((root / "design.json").read_text(encoding="utf-8"))
    document["data"].append({"dataID": "/data/atlas/reference.tif", "protocol": "wsi_service"})
    path.write_text(json.dumps(document), encoding="utf-8")
    code, printed, _ = run("plan", "--design", str(path), "--slot", "0=slide", "--slot", "1=mask")
    assert code == 0, printed
    assert "fixed" in printed and "intentional" in printed, printed


def test_plan_design_offers_the_python_that_binds_it():
    """The CLI validates the design; the loop lives in Python. Pointing at it is
    the difference between a tool that stops and a tool that dead-ends."""
    root = session_root()
    code, printed, _ = run("plan", "--design", str(root / "design.json"))
    assert code == 0, printed
    assert "load_design" in printed and "design.bind" in printed, printed
    assert "0: 'slide'" in printed, printed  # the slots to paste, default included


def test_plan_design_says_the_build_can_record_the_design():
    """The hint is the only route from this command to the next one. A caller who
    binds in Python and builds without knowing `--design` exists gets `design:
    null` and no reason to suspect they lost something."""
    root = session_root()
    code, printed, _ = run("plan", "--design", str(root / "design.json"))
    assert code == 0, printed
    assert "--design" in printed.split("bind it in Python")[1], printed


def test_build_refuses_a_design_because_binding_it_is_not_a_command_line():
    root = session_root()
    code, _, err = run(
        "build", "--design", str(root / "design.json"), "-o", str(root / "r.html")
    )
    # `--design` is now a build flag too, so the refusal comes from `_build` and
    # not from argparse -- which is why the message has to carry the way forward
    # rather than just the rule. The library-level refusal is asserted in
    # `test_the_door_still_refuses_a_design_when_handcoded`.
    assert code == 4
    assert "--design" in err
    assert "--sessions-dir" in err, "a refusal that dead-ends is a dead end"
    assert "binds nothing" in err


def test_build_records_the_design_the_sessions_were_bound_from():
    """`--design` beside `--sessions-dir` answers a question the sidecar could not
    otherwise answer: which one document 300 session files came from.

    The flag is a claim about the caller's own loop -- a bound session carries its
    DataIDs and no note of its parent -- so what is under test is that the claim is
    recorded faithfully: the design as authored, and the slots it was claimed with.
    """
    root = session_root()
    out = root / "designed.html"
    code, printed, err = run(
        "build", "--sessions-dir", str(root / "sessions"), "--design",
        str(root / "design.json"), "--slot", "0=slide", "--slot", "1=mask",
        "-o", str(out), "--no-check",
    )
    assert code == 0, (printed, err)
    record = json.loads((root / "designed.provenance.json").read_text(encoding="utf-8"))
    assert record["design"] is not None, "the design was silently dropped"
    assert record["design"]["slots"] == {"0": "slide", "1": "mask"}
    # As authored: the design's own placeholder DataID, not the bound case's.
    assert record["design"]["session"]["data"][0]["dataID"] == "/data/x/a.tif"


def test_build_without_a_design_records_none_rather_than_inventing_one():
    """The absence is the answer. A build that was handed finished sessions has no
    way to know where they came from, and a sidecar that guessed would vouch for a
    provenance nobody claimed."""
    root = session_root()
    code, _, err = run(
        "build", "--sessions-dir", str(root / "sessions"), "-o",
        str(root / "plain.html"), "--no-check",
    )
    assert code == 0, err
    record = json.loads((root / "plain.provenance.json").read_text(encoding="utf-8"))
    assert record["design"] is None


def test_build_gates_the_design_it_is_asked_to_record():
    """A design that would not boot is refused, not written into a record.

    The flag does not gate the page -- the sessions do -- so without this the
    sidecar would certify a design the viewer cannot open, which is the one thing
    a record of provenance must not do.
    """
    root = session_root()
    path = root / "hollow.json"
    path.write_text(json.dumps({"params": {}, "data": [{}]}), encoding="utf-8")
    code, _, err = run(
        "build", "--sessions-dir", str(root / "sessions"), "--design", str(path),
        "-o", str(root / "h.html"), "--no-check",
    )
    assert code == 1, err
    assert "dataID" in err, err
    assert not (root / "h.html").exists(), "a refused design still wrote a page"


def test_the_design_a_build_records_does_not_rewrite_the_sessions():
    """`--design` is a record and not an input: the page links the folder's
    sessions, whatever the design's own slots happen to hold."""
    root = session_root()
    out = root / "keep.html"
    code, _, err = run(
        "build", "--sessions-dir", str(root / "sessions"), "--design",
        str(root / "design.json"), "-o", str(out), "--no-check",
    )
    assert code == 0, err
    page = out.read_text(encoding="utf-8")
    for name in ("case-01", "case-02"):
        assert name in page, f"{name} left the page once --design was named"
    # The links carry the bound cases' DataIDs -- mount root stripped, as a DataID
    # is defined -- and not the design's own placeholder. That is the whole claim:
    # naming a design must not change what the page points at.
    decoded = [
        urllib.parse.unquote(url.split("#", 1)[1])
        for url in re.findall(r'href="([^"]*)"', page)
        if "/v3/#" in url
    ]
    assert decoded, "the page links no sessions"
    assert any("case-01/s.tif" in payload for payload in decoded), decoded[0][:120]
    assert not any("/data/x/a.tif" in payload for payload in decoded), (
        "the design's placeholder reached the page; --design is a record, not an input"
    )


def test_a_slot_the_design_lacks_is_refused_by_the_build_that_records_it():
    """`--slot` is part of the claim on this path, so a slot naming an index the
    design does not have is a wrong spec -- exit 1, the same answer `plan` gives.

    Without this the record would claim `{5: nope}` had produced the page.
    """
    root = session_root()
    code, _, err = run(
        "build", "--sessions-dir", str(root / "sessions"), "--design",
        str(root / "design.json"), "--slot", "5=nope", "-o", str(root / "s.html"),
        "--no-check",
    )
    assert code == 1, err
    assert "data[5]" in err and "2 data entries" in err, err


def test_the_door_still_refuses_a_design_when_handcoded():
    """The same refusal through the library door, where the flag exists but means
    nothing: `_build` is reached with a namespace that has `design` set."""
    import argparse

    from report_fast.__main__ import UsageError, _build

    args = argparse.Namespace(
        design="x.json", sessions_dir=None, sessions=[], manifest=None,
        check_only=False, publish=False, run=None, no_check=True, out=None,
        emit_manifest=False, layout="grid", title="T", subtitle="",
        slots=[], tracking_uri=None,
    )
    try:
        _build(args)
    except UsageError as error:
        assert "binds nothing" in str(error)
    else:
        raise AssertionError("a design cannot build a report; nothing bound it")


def test_emit_manifest_prints_and_writes_nothing():
    root = session_root()
    code, printed, _ = run(
        "build", "--sessions-dir", str(root / "sessions"), "--emit-manifest"
    )
    assert code == 0, printed
    assert "sessions:" in printed and "from_config" in printed
    assert sorted(p.name for p in root.iterdir()) == ["design.json", "sessions"], (
        "--emit-manifest is an offer on stdout, never a file the tool chose to keep"
    )


def test_a_command_line_with_no_input_at_all_is_a_usage_error():
    code, _, err = run("build")
    assert code == 4 and "needs a manifest" in err
    code, _, err = run("plan")
    assert code == 4 and "needs a manifest" in err


def test_a_malformed_slot_is_explained_as_a_slot():
    root = session_root()
    for bad in ("slide", "0", "0="):
        code, _, err = run("plan", "--design", str(root / "design.json"), "--slot", bad)
        assert code == 4, bad
        assert "INDEX=NAME" in err or "must be" in err, (bad, err)


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
