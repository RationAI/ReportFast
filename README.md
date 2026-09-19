# ReportFast

ReportFast builds static HTML reports of pathology slides. A report is one file of
cards; every card links into the [xOpat v3 viewer](https://xopat.org/) with the
whole session carried in the URL fragment. Nothing is served, no JavaScript runs in
the page, and the file can be mailed as it is.

The point is to remove the failure nobody notices. A link pointing at data the tile
server cannot open still loads the viewer, still renders a card, and shows it black
with no error anywhere. The library exists to make that pair of mistakes — a wrong
reference, a wrong index — impossible to make quietly.

## The intended way to use it

One command installs the library; the skill comes with it:

```bash
uv add "report-fast[mlflow] @ git+https://github.com/RationAI/reporting.git"
```

The **skill** — the procedure an agent follows — ships inside the package, and
`reportfast skill show` prints it. Nothing installs anything. A project that wants
Claude Code to load the skill as its own commits a `skills/` directory, or installs
it through Claude Code's own mechanism; either way the library's job ends at
carrying the file.

Then describe the report: which cases, which slides, which overlays, what colours.
**The agent writes a Python script**, the library builds the sessions and renders the
page. There is no build command — a `build` would be a second, worse way to write the
same loop over files. The script is the report's record: it names the folders, the
colours, the order and the layout, and unlike a spec file it can be re-run.

Everything below is for the cases where the agent is not doing it.

## What the library provides

- **The session object.** `XopatSession` holds the document the viewer boots from and
  turns it into a URL. Build it from a slide and overlays, or read back a session
  someone else authored — a dict, JSON text, a file, a pasted viewer link. Fields the
  library has never heard of survive untouched, so its vocabulary is never the
  viewer's ceiling.
- **Sources.** Slides and overlays from a mounted folder or from an MLflow run's
  artifacts, matched to each other by file stem. `case_matrix` reports what paired and
  what dropped, which is the difference between a finding and a wrong folder.
- **Two page components.** `SlideCard` and `SlideGrid`, plus `Section` for grouping
  and `Report`'s title, subtitle and preamble as the page's whole prose budget. Custom
  blocks are `BaseComponent` subclasses.
- **MLflow integration.** Read a run's artifacts as DataIDs without downloading them,
  and publish a report back to a run — which happens only when `publish()` is called.
- **Reproducible output.** The same report renders byte-identical HTML: no timestamps,
  ids derived from position rather than chance.

What it deliberately does not have: a schema, a manifest format, a CLI that composes a
report, a probe that runs at build time, a copy of the viewer's feature list. Each was
built, and each was deleted; [DESIGN.md](DESIGN.md) has the reasons.

## Install

Not on PyPI.

```bash
uv add "report-fast @ git+https://github.com/RationAI/reporting.git"
uv add "report-fast[mlflow] @ git+https://github.com/RationAI/reporting.git"  # + MLflow
uv add --editable /path/to/reporting                                # a checkout
```

One extra, `mlflow`, capped `>=2.8,<3` because the deployment runs a 2.x tracking
server and a 3.x client 404s listing artifacts. Everything else needs only
`python-fasthtml`, and `import report_fast` works without the extra. Python 3.10+.

## Python

```python
from pathlib import Path

from report_fast import Report, SlideGrid, XopatSession

sessions = [
    XopatSession.from_slide(path, name=path.stem)
    for path in sorted(Path("/mnt/slides").glob("*.tif"))
]
Report(title="Cohort QC", subtitle=f"{len(sessions)} slides").add(
    SlideGrid(sessions=sessions)
).write("report.html")
```

Masks over slides, matched by stem — `case_001.svs` and `case_001.tiff` are one case:

```python
from report_fast import Drive, Mask, MlflowRun, Report, SlideGrid, case_matrix

matrix = case_matrix(
    Drive("/mnt/data/colon/dysplasia"),
    [
        Mask("Tissue", Drive("/mnt/data/tissue_masks"), color="#ffff00", opacity=0.5),
        Mask("Grades", MlflowRun("41d5e1d7d43641ea8f645f9b7945e9f7", "annot_masks"),
             classes=3, palette=["#ffffff", "#ff0000", "#00ff00"]),
    ],
    only=["1094_18_HE_0", "8625_13_HE_A"],   # the cohort, in report order
    min_layers=2,
)
Report(title="Dysplasia QC", blocks=[SlideGrid(sessions=matrix.sessions)]).write("report.html")
print(matrix.coverage, matrix.dropped)       # what paired; what got filtered
```

Nothing is written unless a path is named: `write()` returns the path, `to_html()`
returns the string and touches no disk. Other entry points are
`XopatSession.from_url` / `from_file` / `from_config` for sessions that already exist,
`sessions_from_folder`, `Mlflow` for runs, and `XopatEndpoint` to aim one call at
another deployment.

## Publishing is asked for

`Mlflow.publish()` is the only name in the library that uploads, and it is a separate
line in a script rather than a flag that could be set. Nothing implies it — not a
config key, not a CI job, not an obvious-looking request: the workflow file has no
publish step and a test asserts that it never gains one. An artifact logged to
somebody's run is in the record, and there is no clean way to take it back out.

Credentials go through the environment, never into a script or a report.

## Configuration

`XOPAT_BASE_URL` (viewer root), `XOPAT_WSI_BASE_URL` (tile server — its own mount, not
under the viewer's), `XOPAT_IMAGE_PROTOCOL`, `XOPAT_MOUNT_ROOT` (the prefix stripped to
form a DataID), `MLFLOW_TRACKING_URI`, `MLFLOW_WEB_URL`. Each can also be passed per
call. `references/deployment.md` lists every one with its default and where it is read.

Two facts that cost time when unknown:

- **The path selects the viewer version** — this host serves v2 at `/xopat/` and v3 at
  `/v3/`, and a v3 session opened by v2 looks like it loaded.
- **Slide paths are shared state.** A session names slides by path, never by bytes, so
  the machine building a report and the tile server must see the same file under the
  same root. Nothing checks this; it surfaces when someone opens the page.

## The CLI is the skill

`reportfast` reads: it prints the procedure an agent follows, and writes nothing.

| | |
| --- | --- |
| `skill show` | print `SKILL.md`; `--reference references/deployment.md` prints a bundled file, including the `examples/*.json` session fixtures |

Exit codes: `0` fine, `1` the file asked for is not in the bundle, `4` used wrongly.
There used to be `skill install` and `skill where`; see *Reversals* in DESIGN.md.

## Where the detail lives

| Looking for | Read |
| --- | --- |
| The procedure an agent follows | `reportfast skill show`, or [skills/reportfast/SKILL.md](skills/reportfast/SKILL.md) |
| Session shape, `params`, the black-card checklist | `reportfast skill show --reference references/xopat-v3.md` |
| Deployment coordinates, DataID construction, environment | `reportfast skill show --reference references/deployment.md` |
| MLflow: artifacts, addresses, publishing | `reportfast skill show --reference references/mlflow.md` |
| A v2 Hydra config mapped onto this library | `reportfast skill show --reference references/hydra-v2-to-v3.md` |
| A real session to imitate | `reportfast skill show --reference examples/dysplasia_case.json` |
| Why it is built this way, and what was ruled out | [DESIGN.md](DESIGN.md) |

## Repository layout and tests

`report_fast/` splits into the wire format (`xopat.py`, `session.py`, `layer.py`,
`config.py`), rendering (`core.py`, `components/`), and the edges (`masks.py`,
`mlflow.py`, `skill.py`, `__main__.py`). Nothing here copies the viewer's vocabulary:
`layer.py` is the one file naming a shader type, and everything else about the viewer
comes from its own source — see `test_there_is_no_copy_of_the_viewer_vocabulary`.

```bash
uv run pytest -q          # no server, no mount, no slide opened, no network
uv run ruff check .
```

Each test file also runs standalone (`uv run python tests/test_cli.py`). CI runs both
on Python 3.10 and 3.12 and has no publish step. Three properties are asserted rather
than assumed: output is byte-identical for the same inputs, no workflow publishes, and
every API name the skill's prose offers actually exists.
