"""MLflow in, MLflow out.

The images are never downloaded. An artifact directory of a run becomes a list
of DataIDs the xOpat tile server addresses directly --
`mflow/<experiment_id>/<run_id>/artifacts/<path>` -- which are strings the rest
of this package already accepts as `slides=` and as a layer's `path`. Writing a
report back logs the HTML as an artifact, in the layout the original tool used
(`report/report.html`), so old and new reports land in the same place.

mlflow is optional and imported lazily: everything here works against any
object with the client's methods (pass `client=`), and only the default
constructor needs the package installed::

    from report_fast import Mlflow, MlflowRun, Mask, Report, SlideGrid
    from report_fast import sessions_from_masks

    flow = Mlflow(tracking_uri="http://mlflow.rationai-mlflow:5000/")
    run = "5b72e2a73b3941f0be63e232d8072127"

    sessions = sessions_from_masks(
        flow.slides(run, "tile_masks"),
        [Mask("HG", MlflowRun(run, "tile_masks/test_preliminary/HG Dysplasia"))],
        flow=flow,
    )
    report = Report(
        title="Dysplasia report",
        blocks=[SlideGrid(sessions=sessions)],
    )
    report.write("report.html")
    published = flow.publish(report, run_id=run)
    print(published.url)

`slides()` and `masks()` only read. `publish()` writes to the tracking store,
which is why it is never implicit: no argument implies it, and it is the only
name in this package that uploads.
"""

from __future__ import annotations

import os
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Collection, Dict, List, Optional, Tuple, Union

from .core import Report
from .session import SLIDE_PATTERNS
from .xopat import mount_path

__all__ = [
    "Mlflow",
    "MlflowError",
    "Published",
    "DEFAULT_ARTIFACT_PREFIX",
    "DEFAULT_ARTIFACT_DIR",
    "DEFAULT_WEB_URL",
    "SLIDE_SUFFIXES",
    "artifact_data_id",
]

#: First element of the DataIDs the xOpat image server uses for artifacts. The
#: deployment mounts the artifact store under this name; it is a namespace in a
#: DataID, not a directory on the machine writing the report.
DEFAULT_ARTIFACT_PREFIX = "mflow"

#: Base of the run links put in the report. Separate from the tracking URI
#: because the UI a browser reaches is usually not the host the API is served
#: on -- the cluster URI resolves only inside the cluster.
DEFAULT_WEB_URL = "https://mlflow.rationai.cloud.trusted.e-infra.cz/"

#: Artifact directory a published report lands in: the layout the original tool
#: used, so a run's `report/` holds whichever was written last.
DEFAULT_ARTIFACT_DIR = "report"

RUN_NAME_TAG = "mlflow.runName"
USER_TAG = "mlflow.user"
#: What the UI shows as the run description, and the only way to set it that
#: survives both tracking-API generations: `update_run_description` is gone from
#: recent clients, the tag is read by both.
RUN_NOTE_TAG = "mlflow.note.content"

#: Suffixes treated as images when listing a run's artifacts. Derived from the
#: folder-scan patterns but matched case-insensitively -- artifact names come
#: from other people's pipelines.
SLIDE_SUFFIXES = frozenset(pattern.lstrip("*").lower() for pattern in SLIDE_PATTERNS)


class MlflowError(RuntimeError):
    """Raised when a run, its artifacts or the tracking store cannot be read or written."""


def artifact_data_id(
    artifact_path: Union[str, PurePosixPath],
    run_id: str,
    experiment_id: Union[str, int],
    prefix: str = DEFAULT_ARTIFACT_PREFIX,
) -> str:
    """The DataID xOpat addresses one artifact by.

    `mflow/<experiment_id>/<run_id>/artifacts/<artifact_path>`: the tile server
    resolves the artifact store itself, and `mount_path` leaves the result
    untouched because it is already relative.
    """
    relative = PurePosixPath(str(artifact_path).replace("\\", "/"))
    root = PurePosixPath(prefix, str(experiment_id), run_id, "artifacts")
    return str(root / relative)


@dataclass(frozen=True)
class Published:
    """Where a published report ended up."""

    run_id: str
    artifact: str
    url: str
    created_run: bool = False

    def __str__(self) -> str:
        return f"{self.artifact} on run {self.run_id} -- {self.url}"


class Mlflow:
    """Reads a run's artifacts and metrics; writes reports back to it.

    Args:
        tracking_uri: Where the tracking API answers. `None` takes whatever
            mlflow is already configured with (`MLFLOW_TRACKING_URI`).
        web_url: Base for run links. `None` disables `link()`.
        artifact_prefix: The DataID namespace of the artifact store.
        client: An `MlflowClient` -- or anything with `get_run`,
            `list_artifacts`, `log_artifact`, ... -- instead of building one.
            How to pass a double in tests, or a client configured elsewhere.
    """

    def __init__(
        self,
        tracking_uri: Optional[str] = None,
        web_url: Optional[str] = DEFAULT_WEB_URL,
        artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX,
        client: Any = None,
    ) -> None:
        self.tracking_uri = tracking_uri
        self.web_url = web_url
        self.artifact_prefix = artifact_prefix
        self._client = client

    @classmethod
    def from_env(cls, **overrides: Any) -> "Mlflow":
        """Read `MLFLOW_TRACKING_URI` / `MLFLOW_WEB_URL` / `REPORTFAST_MLFLOW_ARTIFACT_PREFIX`."""
        values: Dict[str, Any] = {
            "tracking_uri": os.environ.get("MLFLOW_TRACKING_URI") or None,
            "web_url": os.environ.get("MLFLOW_WEB_URL") or DEFAULT_WEB_URL,
            "artifact_prefix": os.environ.get(
                "REPORTFAST_MLFLOW_ARTIFACT_PREFIX", DEFAULT_ARTIFACT_PREFIX
            ),
        }
        values.update(
            {key: value for key, value in overrides.items() if value is not None}
        )
        return cls(**values)

    def client(self) -> Any:
        """The tracking client, built on first use.

        mlflow reads the URI off the module rather than off the client it hands
        back (https://github.com/mlflow/mlflow/issues/5852), so it is set
        globally first -- the workaround the original tool needed too.
        """
        if self._client is None:
            try:
                import mlflow
            except ImportError as error:  # pragma: no cover - depends on the venv
                raise MlflowError(
                    "mlflow is not installed -- it is an optional extra. "
                    "`uv sync --extra mlflow`."
                ) from error
            mlflow.set_tracking_uri(self.tracking_uri or mlflow.get_tracking_uri())
            self._client = mlflow.MlflowClient()
        return self._client

    # ------------------------------------------------------------------ reads

    def experiment_id(self, run_id: str) -> str:
        """The experiment a run belongs to, which its DataIDs are named by."""
        return str(self._run(run_id).info.experiment_id)

    def data_ids(
        self, run_id: str, path: str = "", recursive: bool = False
    ) -> List[str]:
        """DataIDs for the files under `path` of `run_id`, sorted by name.

        Raises:
            MlflowError: When `path` holds no files. The original tool returned
                an empty table here and produced a report with no slides; a
                mistyped artifact directory is worth a message instead.
        """
        experiment_id = self.experiment_id(run_id)
        files, subdirectories = self._walk(run_id, path, recursive)
        if not files:
            detail = (
                "Only subdirectories: " + ", ".join(subdirectories)
                if subdirectories
                else "It is empty."
            )
            if subdirectories and not recursive:
                detail += ". Pass recursive=True to search inside them"
            raise MlflowError(
                f"Run {run_id} has no files under 'artifacts/{path or '.'}'. {detail}."
            )
        return [
            artifact_data_id(name, run_id, experiment_id, self.artifact_prefix)
            for name in files
        ]

    def slides(
        self,
        run_id: str,
        path: str = "",
        *,
        patterns: Optional[Collection[str]] = SLIDE_SUFFIXES,
        recursive: bool = False,
    ) -> List[str]:
        """Slide DataIDs under `path`, ready for `slides=`.

        `patterns` filters by suffix -- a run's artifact directory usually holds
        its metrics tables next to the images -- and `None` takes every file.
        """
        data_ids = self.data_ids(run_id, path, recursive=recursive)
        if patterns is None:
            return data_ids
        suffixes = tuple(_suffix(pattern) for pattern in patterns)
        return [data_id for data_id in data_ids if data_id.lower().endswith(suffixes)]

    def masks(
        self,
        run_id: str,
        path: str = "",
        *,
        name: Optional[str] = None,
        recursive: bool = False,
    ) -> Callable[[Any], List[Dict[str, Any]]]:
        """A `layers_for` function pairing masks with slides by file stem.

        The rule the original tool used: `case_001.tif` as the slide and
        `case_001.tif` (or `.tiff`, `.png`) in the mask directory are the same
        case. `name` labels the overlay and defaults to the run's name.
        """
        by_stem = {
            PurePosixPath(data_id.rsplit("/", 1)[-1]).stem: data_id
            for data_id in self.slides(run_id, path, recursive=recursive)
        }
        label = name or self.run_name(run_id)

        def layers_for(slide: Any) -> List[Dict[str, Any]]:
            stem = PurePosixPath(mount_path(slide)).stem
            mask = by_stem.get(stem)
            return [{"path": mask, "name": label}] if mask else []

        return layers_for

    def run_name(self, run_id: str) -> str:
        """The run's `mlflow.runName` tag, or its id when it has none."""
        return self.tags(run_id).get(RUN_NAME_TAG) or run_id

    def tags(self, run_id: str) -> Dict[str, str]:
        """The run's tags."""
        return dict(self._run(run_id).data.tags or {})

    def metrics(self, run_id: str) -> Dict[str, float]:
        """The run's latest metrics, as a plain dict of name -> value."""
        return dict(self._run(run_id).data.metrics or {})

    def link(self, run_id: str, experiment_id: Optional[Union[str, int]] = None) -> str:
        """The run's page in the MLflow UI."""
        if not self.web_url:
            raise MlflowError("This Mlflow has no web_url, so it cannot link a run.")
        experiment = (
            self.experiment_id(run_id) if experiment_id is None else experiment_id
        )
        return (
            f"{self.web_url.rstrip('/')}/#/experiments/"
            f"{urllib.parse.quote(str(experiment))}/runs/{urllib.parse.quote(run_id)}"
        )

    def download(
        self, run_id: str, path: str, out: Optional[Union[str, os.PathLike]] = None
    ) -> str:
        """Fetch one artifact to disk, returning the local path.

        The place bytes do move: an artifact read as data rather than shown as
        an image, a predictions table or a measurements CSV.
        """
        return self._call("download_artifacts", run_id=run_id, path=path, dst_path=out)

    # ---------------------------------------------------------------- writes

    def publish(
        self,
        report: Union[Report, str],
        run_id: Optional[str] = None,
        *,
        artifact_dir: str = DEFAULT_ARTIFACT_DIR,
        filename: str = "report.html",
        run_name: str = "",
        experiment_name: str = "",
        description: str = "",
        user: Optional[str] = None,
        extra_dir: Optional[Union[str, os.PathLike]] = None,
    ) -> Published:
        """Upload the report to a run as `report/report.html`.

        With `run_id` the report is attached to it, as the original tool's
        `RunIDMLFlowReportAttacher` did. Without one, a run is created in
        `experiment_name` carrying the name, description and user
        `MLFlowReportStorer` used to set, and the report goes there.

        Args:
            report: A `Report`, or HTML already rendered.
            run_id: Existing run to attach to. `None` creates one.
            artifact_dir: Artifact directory inside the run.
            filename: Name under it.
            run_name: Name for a created run.
            experiment_name: Experiment for a created run; required then.
            description: Description for a created run.
            user: Owner tag for a created run; defaults to `$USER`.
            extra_dir: A directory logged under `<artifact_dir>/conf`, the slot
                the original tool used for the Hydra configuration.

        Returns:
            A :class:`Published` with the run, the artifact path and the link.
        """
        html = report.to_html() if isinstance(report, Report) else str(report)
        client = self.client()
        with tempfile.TemporaryDirectory(prefix="reportfast-") as tmp:
            local = Path(tmp) / filename
            local.write_text(html, encoding="utf-8")
            created = run_id is None
            if created:
                run_id = self._create_run(run_name, experiment_name, description, user)
            try:
                client.log_artifact(
                    run_id=run_id, local_path=str(local), artifact_path=artifact_dir
                )
                if extra_dir is not None:
                    client.log_artifacts(
                        run_id=run_id,
                        local_dir=str(extra_dir),
                        artifact_path=f"{artifact_dir.rstrip('/')}/conf",
                    )
            except Exception as error:
                raise MlflowError(
                    f"Could not log the report to run {run_id}: {error}"
                ) from error
        return Published(
            run_id=run_id,
            artifact=f"{artifact_dir.rstrip('/')}/{filename}",
            url=self.link(run_id),
            created_run=created,
        )

    def _create_run(
        self,
        run_name: str,
        experiment_name: str,
        description: str,
        user: Optional[str],
    ) -> str:
        if not experiment_name:
            raise MlflowError(
                "publish() without a run_id needs experiment_name= to create one."
            )
        experiment = self.client().get_experiment_by_name(experiment_name)
        if experiment is None:
            raise MlflowError(f"There is no experiment named {experiment_name!r}.")
        tags = {RUN_NAME_TAG: run_name} if run_name else {}
        if description:
            tags[RUN_NOTE_TAG] = description
        owner = user or os.environ.get("USER") or os.environ.get("MLFLOW_USER")
        if owner:
            tags[USER_TAG] = owner
        run = self._call(
            "create_run", experiment_id=str(experiment.experiment_id), tags=tags
        )
        run_id = run.info.run_id
        self._call("set_terminated", run_id=run_id)
        return run_id

    # --------------------------------------------------------------- plumbing

    def _run(self, run_id: str) -> Any:
        try:
            return self.client().get_run(run_id=run_id)
        except Exception as error:
            raise MlflowError(
                f"Could not read run {run_id} (get_run) from "
                f"{self.tracking_uri or 'the tracking store'}: {error}"
            ) from error

    def _walk(
        self, run_id: str, path: str, recursive: bool
    ) -> Tuple[List[str], List[str]]:
        """Files (and subdirectory names) under `path`, in artifact-relative form."""
        files: List[str] = []
        subdirectories: List[str] = []
        for item in self._call("list_artifacts", run_id=run_id, path=path):
            if getattr(item, "is_dir", False):
                subdirectories.append(item.path)
                if recursive:
                    deeper, _ = self._walk(run_id, item.path, True)
                    files.extend(deeper)
                continue
            files.append(item.path)
        return sorted(files), subdirectories

    def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            return getattr(self.client(), method)(**kwargs)
        except Exception as error:
            arguments = ", ".join(f"{key}={value!r}" for key, value in kwargs.items())
            raise MlflowError(
                f"MLflow {method}({arguments}) failed: {error}"
            ) from error


def _suffix(pattern: str) -> str:
    pattern = pattern.strip().lower()
    return pattern if pattern.startswith(".") else f".{pattern}"
