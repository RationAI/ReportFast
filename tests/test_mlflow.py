"""Tests for `report_fast.mlflow` -- run in, report out.

None of them need mlflow: each injects a double through `client=`, which is the
same seam a caller with an already-configured client uses.

Run:      cd /home/jovyan/report_fast && python tests/test_mlflow.py
Pytest:   pytest tests/test_mlflow.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_fast import Report, sessions_from_paths  # noqa: E402
from report_fast.mlflow import (  # noqa: E402
    Mlflow,
    MlflowError,
    artifact_data_id,
)

RUN = "run-1"
EXPERIMENT = "111"

ARTIFACTS = {
    RUN: [
        "slides/case_001.tif",
        "slides/case_002.TIFF",
        "slides/metrics.csv",
        "masks/case_001.tif",
        "masks/sub/case_002.tiff",
        "report/report.html",
        "report/conf/base.yaml",
    ],
    "dirs-only": ["a/x.tif", "b/y.tif"],
}


class FileInfo:
    def __init__(self, path: str, is_dir: bool = False) -> None:
        self.path = path
        self.is_dir = is_dir


class RunInfo:
    def __init__(self, run_id: str, experiment_id: str) -> None:
        self.run_id = run_id
        self.experiment_id = experiment_id


class RunData:
    def __init__(self, tags=None, metrics=None) -> None:
        self.tags = tags or {}
        self.metrics = metrics or {}


class Run:
    def __init__(self, run_id, experiment_id, tags=None, metrics=None) -> None:
        self.info = RunInfo(run_id, experiment_id)
        self.data = RunData(tags, metrics)


class Experiment:
    def __init__(self, experiment_id: str, name: str) -> None:
        self.experiment_id = experiment_id
        self.name = name


class Logged:
    def __init__(self, run_id: str, artifact_path, local_path: str) -> None:
        self.run_id = run_id
        self.artifact_path = artifact_path
        self.content = Path(local_path).read_text(encoding="utf-8")


class FakeClient:
    """The slice of `MlflowClient` the module uses, and nothing more."""

    def __init__(self, runs=None, artifacts=None, experiments=None, broken=None):
        self.runs = runs or {
            RUN: Run(RUN, EXPERIMENT, tags={"mlflow.runName": "Level 1 heatmaps"}),
            "dirs-only": Run("dirs-only", EXPERIMENT),
        }
        self.artifacts = artifacts or ARTIFACTS
        self.experiments = experiments or [Experiment(EXPERIMENT, "Dysplasia")]
        self.broken = broken
        self.logged: list[Logged] = []
        self.logged_dirs: list[tuple] = []
        self.created: list[Run] = []
        self.terminated: list[str] = []
        self._next = 0

    def _guard(self, method: str) -> None:
        if self.broken == method:
            raise RuntimeError(f"{method} could not reach the store")

    def get_run(self, run_id: str) -> Run:
        self._guard("get_run")
        return self.runs[run_id]

    def list_artifacts(self, run_id: str, path: str = "") -> list:
        self._guard("list_artifacts")
        prefix = f"{path.rstrip('/')}/" if path else ""
        found, seen = [], set()
        for artifact in self.artifacts.get(run_id, []):
            if not artifact.startswith(prefix):
                continue
            rest = artifact[len(prefix) :]
            if not rest:
                continue
            if "/" in rest:
                directory = prefix + rest.split("/", 1)[0]
                if directory in seen:
                    continue
                seen.add(directory)
                found.append(FileInfo(directory, is_dir=True))
            else:
                found.append(FileInfo(artifact))
        return found

    def log_artifact(self, run_id, local_path, artifact_path=None) -> None:
        self._guard("log_artifact")
        self.logged.append(Logged(run_id, artifact_path, local_path))

    def log_artifacts(self, run_id, local_dir, artifact_path=None) -> None:
        self._guard("log_artifacts")
        self.logged_dirs.append((run_id, str(local_dir), artifact_path))

    def get_experiment_by_name(self, name: str):
        return next((item for item in self.experiments if item.name == name), None)

    def create_run(self, experiment_id, tags=None) -> Run:
        self._guard("create_run")
        self._next += 1
        run = Run(f"new-{self._next}", experiment_id, tags=dict(tags or {}))
        self.created.append(run)
        self.runs[run.info.run_id] = run
        return run

    def update_run_description(self, run_id, description) -> None:
        raise AssertionError("a description is a tag, not an extra call")

    def set_terminated(self, run_id) -> None:
        self.terminated.append(run_id)


def flow(client=None, **kwargs) -> Mlflow:
    client = client if client is not None else FakeClient()
    return Mlflow(
        tracking_uri="http://mlflow.test:5000",
        web_url="https://mlflow.test/",
        client=client,
        **kwargs,
    )


def data_id(name: str) -> str:
    return f"mflow/{EXPERIMENT}/{RUN}/artifacts/{name}"


def test_one_artifact_becomes_the_dataid_the_viewer_addresses():
    assert (
        artifact_data_id("slides/case_001.tif", RUN, EXPERIMENT)
        == "mflow/111/run-1/artifacts/slides/case_001.tif"
    )


def test_the_store_namespace_is_configurable():
    assert (
        flow(client=FakeClient(), artifact_prefix="store")
        .data_ids(RUN, "slides")[0]
        .startswith("store/111/run-1/artifacts/slides/")
    )


def test_files_come_out_sorted_and_directories_stay_out():
    assert flow().data_ids(RUN, "slides") == [
        data_id("slides/case_001.tif"),
        data_id("slides/case_002.TIFF"),
        data_id("slides/metrics.csv"),
    ]


def test_slides_leave_out_what_the_viewer_cannot_open():
    slides = flow().slides(RUN, "slides")
    assert slides == [data_id("slides/case_001.tif"), data_id("slides/case_002.TIFF")]
    assert data_id("slides/metrics.csv") not in slides
    assert len(flow().slides(RUN, "slides", patterns=None)) == 3


def test_masks_can_be_listed_below_the_directory_they_are_in():
    assert flow().slides(RUN, "masks", recursive=True) == [
        data_id("masks/case_001.tif"),
        data_id("masks/sub/case_002.tiff"),
    ]


def test_a_directory_of_only_subdirectories_says_so():
    try:
        flow().data_ids("dirs-only")
    except MlflowError as error:
        assert "a, b" in str(error) and "recursive=True" in str(error)
    else:
        raise AssertionError("an empty listing should raise")
    assert flow().data_ids("dirs-only", recursive=True) == [
        "mflow/111/dirs-only/artifacts/a/x.tif",
        "mflow/111/dirs-only/artifacts/b/y.tif",
    ]


def test_masks_pair_with_slides_by_file_stem():
    layers_for = flow().masks(RUN, "masks", recursive=True)
    assert layers_for(data_id("slides/case_001.tif")) == [
        {"path": data_id("masks/case_001.tif"), "name": "Level 1 heatmaps"}
    ]
    assert layers_for(data_id("slides/case_999.tif")) == []


def test_the_overlay_label_can_be_given():
    layers_for = flow().masks(RUN, "masks", recursive=True, name="HG Dysplasia")
    assert layers_for(data_id("slides/case_001.tif"))[0]["name"] == "HG Dysplasia"


def test_metrics_and_tags_come_off_the_run():
    client = FakeClient(
        runs={RUN: Run(RUN, EXPERIMENT, tags={"user": "me"}, metrics={"dice": 0.81})}
    )
    assert flow(client).metrics(RUN) == {"dice": 0.81}
    assert flow(client).tags(RUN) == {"user": "me"}
    assert flow(client).run_name(RUN) == RUN


def test_the_link_is_the_run_page_not_the_api():
    assert flow().link(RUN) == f"https://mlflow.test/#/experiments/111/runs/{RUN}"
    try:
        Mlflow(web_url=None, client=FakeClient()).link(RUN)
    except MlflowError:
        pass
    else:
        raise AssertionError("link() without a web_url should raise")


def test_publish_lands_where_the_original_tool_put_it():
    client = FakeClient()
    report = Report(title="Dysplasia")
    published = flow(client).publish(report, run_id=RUN)

    assert published.artifact == "report/report.html"
    assert published.run_id == RUN and not published.created_run
    assert published.url.endswith(f"/runs/{RUN}")
    assert len(client.logged) == 1
    assert client.logged[0].artifact_path == "report"
    assert client.logged[0].content == report.to_html()
    assert client.created == []


def test_publish_writes_html_as_well_as_a_report():
    client = FakeClient()
    flow(client).publish("<html><body>hand written</body></html>", run_id=RUN)
    assert "hand written" in client.logged[0].content


def test_publish_can_create_the_run_it_reports_on():
    client = FakeClient()
    published = flow(client).publish(
        Report(title="Dysplasia"),
        run_name="Level 1 heatmaps",
        experiment_name="Dysplasia",
        description="twelve cases",
        user="borisim",
    )
    assert published.created_run and published.run_id == "new-1"
    assert client.created[0].info.experiment_id == EXPERIMENT
    assert client.created[0].data.tags["mlflow.runName"] == "Level 1 heatmaps"
    assert client.created[0].data.tags["mlflow.user"] == "borisim"
    assert client.created[0].data.tags["mlflow.note.content"] == "twelve cases"
    assert client.terminated == ["new-1"]
    assert client.logged[0].run_id == "new-1"


def test_creating_a_run_needs_to_know_where():
    try:
        flow().publish(Report(title="x"))
    except MlflowError as error:
        assert "experiment_name" in str(error)
    else:
        raise AssertionError("a created run needs an experiment")
    try:
        flow().publish(Report(title="x"), experiment_name="nope", user="me")
    except MlflowError as error:
        assert "nope" in str(error)
    else:
        raise AssertionError("an unknown experiment should raise")


def test_a_conf_directory_goes_beside_the_report():
    client = FakeClient()
    flow(client).publish(Report(title="x"), run_id=RUN, extra_dir=Path(__file__).parent)
    assert client.logged_dirs == [(RUN, str(Path(__file__).parent), "report/conf")]


def test_a_store_that_cannot_be_reached_is_reported():
    for method in ("get_run", "list_artifacts", "log_artifact"):
        broken = flow(FakeClient(broken=method))
        try:
            if method == "log_artifact":
                broken.publish(Report(title="x"), run_id=RUN)
            else:
                broken.data_ids(RUN, "slides")
        except MlflowError as error:
            assert method in str(error)
        else:
            raise AssertionError(f"a failing {method} should raise MlflowError")


def test_run_artifacts_reach_a_session_untouched():
    # `sessions_for` is gone with the one-call report; this is the call it made for
    # a list of paths, spelled out. `Mlflow.masks` returns a per-slide function, so
    # it goes in `layers_for`, which is where the surviving signature takes one.
    made = sessions_from_paths(
        flow().slides(RUN, "slides"),
        layers_for=flow().masks(RUN, "masks", recursive=True),
    )
    assert len(made) == 2
    config = made[0].to_config()
    assert config["data"][0] == data_id("slides/case_001.tif")
    assert config["data"][1]["dataID"] == data_id("masks/case_001.tif")
    assert config["background"][0]["dataReference"] == 0
    shaders = config["visualizations"][0]["shaders"]
    assert shaders["layer_shader_0"]["dataReferences"] == [1]
    assert shaders["layer_shader_0"]["name"] == "Level 1 heatmaps"


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
