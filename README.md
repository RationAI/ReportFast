# ReportFast

ReportFast builds static HTML reports of pathology slides. The report is one file
of cards and tables; every card links into the [xOpat v3
viewer](https://xopat.rationai.cloud.trusted.e-infra.cz/v3/) with the whole session
carried in the URL fragment. Nothing is served, no JavaScript runs in the page, and
the file can be mailed as it is.

The point is to remove the failure nobody notices: a link pointing at data the tile
server cannot open. The viewer still loads, the card renders black, and no component
reports an error. A build therefore validates every session against the pinned
viewer's own rules and probes every image before the report counts as finished.

## The intended way to use it: install it, install the skill, ask the agent

Two commands — the first once per project, the second once per machine — and every
report after that is a sentence away:

```bash
uv add "report-fast[all] @ git+https://github.com/RationAI/reporting.git"
uv run reportfast skill install
```

Then describe the report in plain words: which cases, which slides, which overlays,
what colours. The agent writes the sessions, checks them, composes the page, runs the
probe, and reports what came back. The checks are the same ones described under "What
the library provides", so an agent-produced report is not a less-verified report.

`skill install` is not decoration. Installing places the library in a venv and may not
write anywhere else, so it cannot put the **skill** — the procedure an agent follows —
where an agent looks for it. This one command does: `~/.claude/skills`, or
`./.claude/skills` with `--project`. Without it the library works exactly as documented
and no agent knows the procedure exists. `skill where` reports where it landed and
whether an agent would find it; `skill show` prints it.

Everything below is for the cases where the agent is not doing it: a report worth
writing down as a file, a script, or a CI job.

## What the library provides

- **The session object.** `XopatSession` holds the document the viewer boots from and
  turns it into a URL. Build it from a slide and overlays, read it from a pasted link
  or file, or import a config containing fields the library has never heard of — those
  survive untouched.
- **A validation gate.** Every rule was parsed out of the pinned viewer source, not a
  hand-maintained list: the `params` allowlist, layer types and their fields, plural
  `dataReferences`. A key the viewer would silently drop is refused here, with the JSON
  path named. `--version` prints the viewer commit the rules came from.
- **A probe.** Before a build is called done, every DataID the page links is asked of
  the tile server. Three answers: open, refused, unreachable from here.
- **Ten page components.** `Prose`, `Heading`, `Bullets`, `LinkList`, `SlideCard`,
  `SlideGrid`, `MetricTable`, `Chart`, `Section`, `Report` — plain data in, HTML out.
  No database, no metric store, no slide reader. Custom components are subclasses with
  `render()` and optional `css()`.
- **Session templates.** One session document with slots for the paths, bound once per
  case. Handy for 300 cases, and the shape an agent works in.
- **Sources.** Slides and overlays from a mounted folder or from an MLflow run's
  artifacts, matched to each other by file stem. Artifacts are addressed, never
  downloaded.
- **YAML manifests.** The same build expressed as a file: sources, `only:` and
  `min_layers:` filters, colours, class maps, prose blocks. Unknown keys stop the build
  rather than being ignored.
- **MLflow integration.** Read a run's artifacts as DataIDs, write metrics, and publish
  the report back to a run — which only ever happens when `--publish` is typed.
- **A provenance sidecar.** Each build writes `report.provenance.json`: endpoint, viewer
  version and commit, tool version, inputs, every DataID linked, and the design as
  authored when templates were used. Nothing is stamped into the page itself, so a
  mailed HTML carries no provenance — deliver the pair if that matters.
- **Reproducible output.** The same sessions and composition produce byte-identical HTML
  and sidecar: no timestamps, no generated code, ids derived from position.
- **A CLI with machine-readable results.** `plan`, `build`, `find`, `skill`; exit codes
  0–4 distinguish "fine", "the spec is wrong", "an image will not open", "an extra is
  missing" and "nothing to work on". `plan --json` emits the resolved plan for a script.

## Install

Not on PyPI:

```bash
uv add "report-fast @ git+https://github.com/RationAI/reporting.git"   # over git
uv add "report-fast[all] @ git+https://github.com/RationAI/reporting.git"  # + YAML, MLflow
uv add --editable /path/to/reporting      # a checkout under development
uv run reportfast skill install           # for agent use
```

The extras are `manifest` (YAML specs) and `mlflow` (runs). Both are imported lazily;
everything else works without them, and a command needing a missing extra exits 3 and
names it. If `reportfast` is not on `PATH` — the normal state of a `uv` project —
prefix commands with `uv run`.

Worth checking after an install:

```bash
reportfast --version    # report-fast 0.1.0 (viewer 3.1.0 @ 18c94f2b)
reportfast skill where
```

The second half of the version line is the viewer commit the validation rules were
derived from. Anything else there — `contract unreadable: …` — means the install has no
schema artifacts and the gates cannot answer; treat it as a broken install.

## Doing it by hand

Sessions authored here, page rendered by the library. Nothing persists but the report
and its sidecar.

```bash
mkdir -p myreport/sessions && cd myreport
# one session JSON per case; start from a real one:
reportfast skill show --reference examples/dysplasia_case.json > sessions/case-01.json
```

```bash
reportfast plan  --sessions-dir sessions --title "QC pilot"   # resolve, write nothing
reportfast build --sessions-dir sessions --title "QC pilot" -o report.html
```

```
QC pilot: 1 cases, up to 1 overlays
out      report.html
publish  not set

wrote     report.html
probe     3/3 DataIDs open; 0 refused
```

A misspelled key is caught before the build:

```
reportfast: case-02.json: will not load in xOpat v3 as authored -- params.threshhold:
not in the viewer's params allowlist; `sanitizeAgainst` (src/app.ts) drops it and logs
to the console, so the viewer boots as if you had not set it. Fix the JSON at those
paths. ...
```

Other routes in, all passing the same gates:

```bash
reportfast plan  manifests/dysplasia.yaml                     # a YAML manifest
reportfast build manifests/dysplasia.yaml                     # writes, then probes
reportfast plan  --design design.json --slot 0=slide --slot 1=mask   # one design, N cases
reportfast find  <run-id> --path tile_masks                   # what a run holds
```

For the design route, `plan --design` prints the binding code: `load_design(...)` once,
`design.bind(slide=…, mask=…, name=…)` per case. Binding is Python because a loop over
300 cases does not fit in a flag.

### Reading the probe

`0 refused` is the distinction to make. A *refused* DataID is the tile server answering
no — a wrong `--mount-root`, protocol or run id. `unreachable from here` means it never
answered, which is a fact about the machine running the build, not the report; exit code
0 either way, so state which one applies rather than saying "it works". A proxy causes
both directions of this: cluster hostnames usually need `NO_PROXY='*'`, GitHub needs the
proxy, and the two failures look identical.

### Publishing

`--publish` is the only write the tool performs. Not a `publish:` key in a manifest, not
CI, not a prompt — a manifest's `publish:` names a destination, and CI has no publish
step (a test fails the build if one appears). The run id is repeated back before
anything uploads; without a manifest, `--run` is required.

## Python

The CLI is these calls; a manifest is `build_report()` written down, a `Composition` is
a manifest in memory.

```python
from report_fast import Drive, Mask, build_report

build_report(
    background=Drive("/mnt/data/slides"),
    masks=[
        Mask("Tissue", Drive("/mnt/data/tissue_masks"), color="#ffff00", opacity=0.5),
        Mask("Tumor", Drive("/mnt/data/tumor_masks"), color="#ff0000"),
    ],
    only=["case_001", "case_002"],      # filename stems, in report order
    min_layers=2,                       # a case with one of two overlays: not a finding
    title="QC",
    out="report.html",
)
```

`Drive(path)` becomes `MlflowRun(run_id, "artifact_dir")` when the overlays come from a
run instead of a folder; the two can be mixed in one call. Masks are matched to slides
by file stem, so `case_001.svs` and `case_001.tiff` are one case.

```python
from report_fast import MetricTable, Prose, Report, SlideGrid, XopatSession

session = XopatSession.from_slide(
    "/mnt/data/slides/case_001.tif",
    [{"path": "/mnt/data/masks/prob.tif", "name": "Probability", "type": "heatmap"}],
    name="case_001",
)
session.url()            # viewer link, session in the fragment
session.thumbnail()      # the tile server's preview for it

report = Report(title="Dysplasia screen", subtitle="12 tiles")
report.add(Prose(text="Overlays are the model's."))
report.add(SlideGrid([session], collapsible=True))
report.add(MetricTable({"dice": 0.83, "tiles": 12}))
report.write("report.html")
```

Other entry points: `XopatSession.from_url` / `from_file` / `from_config` for sessions
that already exist, `SessionTemplate.bind` to refill slots in one,
`sessions_from_folder` to list a folder, `Composition` to assemble blocks in memory,
`Mlflow` for runs, and `XopatEndpoint` to point a single call at another deployment.

## Configuration

Coordinates come from the environment and can be overridden per command or per call:
`XOPAT_BASE_URL` (viewer), `XOPAT_WSI_BASE_URL` (tile server — its own mount),
`XOPAT_IMAGE_PROTOCOL`, `XOPAT_MOUNT_ROOT` (prefix stripped to form a DataID),
`XOPAT_SESSION_CONFIG` (a JSON or TOML file of defaults merged under every session —
its accepted keys are `SessionPreset` in [config.py](report_fast/config.py)),
`MLFLOW_TRACKING_URI`, `MLFLOW_WEB_URL` (a separate setting: the API is usually
cluster-internal, the links must open in a browser).
`skills/reportfast/references/deployment.md` lists all of them with their defaults and
where each is read.

Two facts that cost time when unknown:

- **The base path selects the viewer version** — this host serves v2 at `/xopat/` and v3
  at `/v3/`, and a v3 session opened by v2 looks like it loaded.
- **Slide paths are shared state.** A session names slides by path, never by bytes, so
  the building machine and the tile server must see the same file under the same root.

## Every option in one place

`reportfast <command> --help` matches the install and says more; nothing here overrides
it.

### Global

| | |
| --- | --- |
| `--version` | tool version and the viewer commit the validation rules came from |

### How sessions arrive — `plan` and `build`

| | |
| --- | --- |
| `manifest` | the one positional argument: a YAML report spec. Omit it and use `--sessions-dir` |
| `--sessions-dir DIR` | a folder of authored session JSON, one file per case; sorted, so the order is the filename's |
| `--session PATH_OR_JSON` | one session, as a file or inline JSON. Repeatable, order preserved. **Not a URL** — a pasted viewer link goes through `XopatSession.from_url()` in Python, or a manifest's `from_url:` |
| `--design PATH_OR_JSON` | on `plan`: gate one design and print its slots. On `build`: name the design for the sidecar. **Neither binds anything** |
| `--slot INDEX=NAME` | name a `data[]` index the design fills per case, e.g. `0=slide`. Repeatable; default `0=slide`. `--design` prints the rest |

### Where the images are — `plan` and `build`

| | | Default |
| --- | --- | --- |
| `--base-url URL` | viewer root the fragment is appended to | `$XOPAT_BASE_URL` |
| `--wsi-base-url URL` | tile server root — its own mount, not under `--base-url` | `$XOPAT_WSI_BASE_URL` |
| `--image-protocol NAME` | registered `slide_protocols` entry for the backgrounds (`.czi` and friends) | *unset* |
| `--mount-root PREFIX` | prefix stripped from paths to form a DataID; `''` strips nothing | `/mnt` |
| `--tracking-uri URL` | MLflow API root, when there is no `flow:` and the env is unset | mlflow's own |

These take precedence over the manifest's `endpoint:` and over the environment, which
is what allows one manifest to be aimed at a second deployment without editing it.

### What the page says — `plan` and `build`

| | |
| --- | --- |
| `--title TITLE` | page title, for a report built from `--sessions-dir`; a manifest carries its own |
| `--subtitle SUBTITLE` | one line under the title, same condition |

### Only `plan`

| | |
| --- | --- |
| `--json` | machine-readable cases, coverage, sources and warnings — the plan's data without prose around it |

### Only `build`

| | |
| --- | --- |
| `-o, --out OUT` | where to write; defaults to the manifest's `out:`, else beside it. With `--publish` and no manifest, nothing local is written unless this is given |
| `--no-check` | skip the DataID probe — offline, or a report that will not be opened |
| `--check-only` | build in memory and probe; write neither HTML nor sidecar |
| `--publish` | upload the report and its provenance (plus manifest and plan, when there is a manifest). **The only write this tool performs** |
| `--run RUN_ID` | publish into this run instead of the manifest's `publish:`; required when there is no manifest |
| `--layout grid\|rows` | card layout; default `grid`. A manifest lays cards out by its own `grid:` |
| `--no-grid` | the same as `--layout rows` |
| `--emit-manifest` | print a manifest that would reproduce this report; writes nothing |

### Only `find`

| | |
| --- | --- |
| `RUN_ID` | a run id obtained elsewhere — nothing here searches MLflow |
| `--path PATH` | artifact subdirectory to list; default the root |
| `-r, --recursive` | descend into subdirectories |
| `--slides` | only files with a slide suffix |
| `--data-ids` | one DataID per line, nothing else, for piping |

### Only `skill`

| | |
| --- | --- |
| `show` | print `SKILL.md` |
| `show --reference NAME` | print one bundled file instead — `references/xopat-v3.md`, `references/deployment.md`, `examples/dysplasia_case.json` |
| `install` | place the skill in `~/.claude/skills` |
| `install --project` | `./.claude/skills` instead, this project only |
| `install --dest DIR` | somewhere else entirely |
| `install --force` | replace a skill that is already there — check first whether anyone edited it |
| `install --link` | symlink, so a checkout edit is live at once |
| `where` | say where the skill is and whether an agent would find it |

### Manifest keys

Keys are checked strictly: an unknown one stops the build and names the nearest
candidate, because a `min_layer:` that silently did nothing reads as a report that was
filtered. [manifests/example.yaml](manifests/example.yaml) is the starting file to copy —
`title:`, background path, mask path, colour — and paths resolve relative to the
manifest. The full set is in
[skills/reportfast/manifest.example.yaml](skills/reportfast/manifest.example.yaml).

| | |
| --- | --- |
| `title`, `subtitle`, `intro` | the page's own words |
| `out` | where the HTML goes, relative to the manifest |
| `theme`, `grid` | page appearance |
| `background` | the slides: exactly one of `drive:`/`dir:`, `run:` + `path:`, or `python:` — plus `patterns:` and `recursive:` |
| `masks` | one row per overlay: the same source keys, then `name`, `color`, `opacity`, `visible`, `classes`, `palette`, `breaks`, `mask`, and `params:` which reaches the v3 layer verbatim |
| `only` | the cases, in report order, as filename stems |
| `min_layers` | drop a case with fewer overlays than this; the plan lists what dropped |
| `sessions` | whole sessions pasted in: `from_config:`, `from_file:`, `from_url:` |
| `sessions_from` | `module:function` — caller-supplied code returning the sessions, for cases the library cannot list |
| `params`, `preset` | session defaults for every case |
| `metrics`, `charts`, `blocks` | the components around the cards — `prose`, `heading`, `bullets`, `links`, `metrics`, `chart`, `section`, `raw_html` |
| `endpoint` | `base_url`, `wsi_base_url`, `image_protocol`, `mount_root` |
| `flow` | `tracking_uri`, `web_url`, `artifact_prefix` |
| `publish` | where `--publish` would log. **Setting it uploads nothing** |

Precedence, lowest to highest: **builtin defaults → `preset` / `$XOPAT_SESSION_CONFIG` →
a pasted config → manifest keys → flags**. A preset can never carry `data`, `background`
or `visualizations` — per-slide content a default would silently rewire into the wrong
indices.

### Exit codes

| Exit | Meaning |
| --- | --- |
| `0` | fine, including a probe that could not reach the server |
| `1` | the *spec* is wrong: a bad manifest key, a missing case, a layer that lands on nothing, or an authored session the viewer would not load as written |
| `2` | the HTML was written and a DataID did not resolve |
| `3` | an optional extra this command needs is not installed |
| `4` | nothing to work on: no such manifest, folder or run, no command, bad flag |

1 and 2 have different fixes — a line in a file versus a wrong run or mount — so they do
not share a code, and neither is argparse's default 2.

## Where the detail lives

| Looking for | Read |
| --- | --- |
| The procedure an agent follows | `reportfast skill show`, or [skills/reportfast/SKILL.md](skills/reportfast/SKILL.md) |
| xOpat v3 session shape and the black-card checklist | `reportfast skill show --reference references/xopat-v3.md` |
| Deployment coordinates, DataID construction, MLflow addresses | `reportfast skill show --reference references/deployment.md` |
| A v2 Hydra config mapped onto this library | `reportfast skill show --reference references/hydra-v2-to-v3.md` |
| A real manifest at full stretch | [manifests/dysplasia.yaml](manifests/dysplasia.yaml) and [manifests/example.yaml](manifests/example.yaml) |
| Session fixtures | [examples/README.md](examples/README.md) |
| Why it is built this way, and what was ruled out | [DESIGN.md](DESIGN.md) |
| MLflow from Python | `report_fast/mlflow.py` |

## Repository layout and tests

`report_fast/` splits into the wire format (`xopat.py`, `session.py`, `layer.py`,
`config.py` — stdlib only), rendering (`core.py`, `components/`), and the edges
(`masks.py`, `mlflow.py`, `skill.py`, `__main__.py`). Nothing here copies the
viewer's vocabulary: `layer.py` is the one file that names a shader type, and the
agent reads the viewer's own source instead. `skills/reportfast/` is the agent's
half, `examples/` fixtures, `reports/` generated HTML.

```bash
uv run pytest tests     # 450+ tests; no server, no mount, no slide opened
uv run ruff check .
uv run python tests/test_cli.py   # each file is also a standalone script
```

`.github/workflows/ci.yml` runs both on Python 3.10 and 3.12 with every extra
installed, and has no publish step. Two properties are asserted rather than assumed:
output is byte-identical for the same inputs, and publishing never happens as a side
effect.

