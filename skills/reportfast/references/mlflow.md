# MLflow

Reading a run's artifacts into a report, and — only when asked — writing the page
back. The extra is `report-fast[mlflow]`; everything here is in
`report_fast.mlflow`, whose single class `Mlflow` is the whole surface.

**First ask whether MLflow is needed at all.** A report whose slides and masks come
from folders needs no mlflow: `Drive` is a `Mask` source like `MlflowRun`, and a
`case_matrix` over two `Drive` paths builds and renders in an environment where mlflow
cannot even be imported. Verified on an interpreter with no mlflow: two cases, one mask
row, page rendered. So a project pinned to mlflow 3 that reads its masks off a mount
does not need a separate reporting environment — only a report that *reads a run*'s
artifacts or publishes to one does, and then the cap below applies to that environment
alone.

## Two addresses

| | Address | Reachable from |
| --- | --- | --- |
| Tracking API | `http://mlflow.rationai-mlflow:5000/` | inside the cluster, this pod included |
| Web UI | `https://mlflow.rationai.cloud.trusted.e-infra.cz/` | a browser; the links a report shows |

The tracking server speaks **2.x** and the extra is capped `mlflow>=2.8,<3` —
pinned to the deployment, not chosen. Verified against the live server with an
mlflow 3.16 client: `get_run` and `get_experiment_by_name` still answer, but
`list_artifacts` — which is what `slides()`, `artifacts()` and `data_ids()` walk —
routes through `/mlflow/logged-models/search`, an endpoint this server has never
had, and 404s. So an mlflow 3 environment reads run metadata fine and then reports
that a run holds no files, which reads as an empty run rather than a version
mismatch.

**A project that needs mlflow 3 keeps it and gives reporting its own
environment** — a `uv` project per reporting job, or `uvx`. That is the supported
answer while the server speaks 2.x; the alternative is this library reimplementing
artifact listing over the raw REST endpoint the server does answer
(`/api/2.0/mlflow/artifacts/list`), which is a second protocol to maintain and has
not been ruled in.

`Mlflow.from_env()` reads `MLFLOW_TRACKING_URI`, `MLFLOW_WEB_URL` and
`REPORTFAST_MLFLOW_ARTIFACT_PREFIX`. Credentials go through the environment or
the mlflow config file, **never** into a script, a report, or this conversation:
a token pasted into chat is a rotated token. List variable *names* when
troubleshooting, never values.

## Read the run, do not download it

A report does not need the pixels. An artifact has an address the tile server
resolves on its own — `mflow/<experiment_id>/<run_id>/artifacts/<path>`, built by
`artifact_data_id()` — so a mask goes into a session as a path and is streamed to
the reader's browser. `Mlflow.download()` is the only thing that moves bytes, and
it is for the cases where the artifact is *data*: a predictions table, a
measurements CSV.

The three methods that matter:

- `data_ids(run_id, path)` — every file under that artifact path as DataIDs.
  Raises `MlflowError` when the path holds no files, naming the subdirectories it
  did find: a mistyped artifact directory used to produce a report with no
  slides in it.
- `slides(run_id, path)` — the same, filtered to image suffixes, because a run's
  artifact directory usually holds its metric tables next to its images.
- `masks(run_id, path)` — not a list but a `layers_for` function, pairing
  `case_001.tif` in the mask directory with `case_001.svs` by **file stem**. That
  stem rule is how a mask finds its slide; a run whose masks are renamed away
  from their slides' stems pairs with nothing, silently, and the report shows
  slides with no overlay.

In practice masks come through `case_matrix` with an `MlflowRun` source rather
than any of the above, and the pairing is checked case by case:

```python
from report_fast import Drive, Mask, MlflowRun, Report, SlideGrid
from report_fast import case_matrix, Mlflow

flow = Mlflow.from_env()          # MLFLOW_TRACKING_URI set, or pass tracking_uri=
matrix = case_matrix(
    Drive("/mnt/data/colon/dysplasia"),
    [Mask("Grades", MlflowRun("41d5e1d7d43641ea8f645f9b7945e9f7", "annot_masks"),
          classes=3, palette=["#ffffff", "#ff0000", "#00ff00"])],
    flow=flow,
)
report = Report("Grading coverage", blocks=[SlideGrid(matrix.sessions)])
report.write("report.html")
print(matrix.coverage, matrix.dropped)
```

Run against this deployment's real `annot_masks`, that print is the interesting
part: a count lower than the number of slides means a stem did not pair, which is
either the truth about the run or a wrong artifact directory — and the only way to
tell which is to look. `flow` is passed so one client is reused; without it, each
`MlflowRun` source builds its own from the environment.

## Runs on this deployment that are known to exist

Verified against the tracking API, so a snippet can be run against them rather
than invented. Experiment **111** holds all three.

| Run id | Name | Artifact directory |
| --- | --- | --- |
| `41d5e1d7d43641ea8f645f9b7945e9f7` | ✍ Annotation masks Creation | `annot_masks/` |
| `97084241311949189445f864d42e9d4e` | 🧻 Tissue masks Creation | `tissue_masks/` |
| `e313fc2a62d54057b41b46057577c75d` | 🏁 test_preliminary F1 - virchow2 - 1.55mpp | `report/report.html` |

The first two are the mask sources every example in this repo uses. The third is
a published report — a page an earlier build wrote back into the run that produced
the model, at `report/report.html`, which is what `publish()` produces and what
its default `artifact_dir="report"` is for. Run names carry emoji; treat them as
opaque strings and read them with `run_name()`.

These are fixtures, not a cohort. A report about real cases names whatever run the
reader is asking about.

## Writing back is asked for, never implied

`publish()` is the only write in the library, and the rule around it is that
nothing in a build implies it. A script that renders a page has not published
anything; publishing happens when someone asks for the report to be attached to
the run, and it is a separate call, visible in the script as a separate line:

```python
published = flow.publish(report, run_id="e313fc2a62d54057b41b46057577c75d")
print(published)    # report/report.html on run e313fc2a… -- <web link>
```

- With `run_id` the page is attached to that run; without one a run is created,
  which needs `experiment_name` and takes `run_name`, `description` and `user`
  (defaulting to `$USER`, else `MLFLOW_USER`).
- It returns a `Published(run_id, artifact, url, created_run)`. Print it — the
  run and the artifact path are the whole answer to "where did it go".
- `extra_dir` logs a directory under `report/conf`, the slot the original
  reporting tool used for the Hydra configuration.
- `web_url=None` turns `link()` off, so the report gets no run links rather than
  links to a host its reader cannot reach.

Publishing is outward-facing and hard to undo cleanly — an artifact log is not
something a later build overwrites invisibly. So: never as a side effect of
building, never from a CI job, never because a variable happened to be set. When
the request is ambiguous, build the page and say where it is, then ask.

## What the API does not tell you

`metrics(run_id)` is a flat `{name: value}` of the run's *latest* values, so a
run that logged a curve reports one point of it. `tags(run_id)` is a flat dict
too. Both are reads of metadata; neither says whether the run's artifacts are
slides at all, which is what `slides()` filters for and why an empty result from
it is a normal answer rather than an error.
