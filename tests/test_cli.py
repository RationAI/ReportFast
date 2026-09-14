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
import sys
import tempfile
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
    assert sent[0][2] == ["manifest.yaml", "plan.json"]
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
