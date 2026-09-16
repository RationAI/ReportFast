# ReportFast

Static HTML reports for whole slide images. A report is a file of cards and
tables; every card links into the [xOpat](https://xopat.rationai.cloud.trusted.e-infra.cz/v3/)
v3 viewer with the session carried in the URL fragment. Nothing is served, no
JavaScript runs in the report, and the file can be mailed.

Everything is built on one object. `XopatSession` holds the session document the
viewer boots from — the same JSON your colleague pastes into chat — and turns it
into a link. The rest of the tool is optional: components that put sessions on a
page, and defaults for people who just have paths.

## Install it in a project

Not on PyPI. Two ways in, from a checkout of this repo or from git:

```bash
uv add --editable /path/to/reporting       # a local checkout you are also editing
uv add "report-fast @ git+https://github.com/RationAI/reporting.git"   # or over git
uv add 'report-fast[all] @ …'              # same, with the YAML and MLflow extras

uv run reportfast skill install
```

That second command is the one that is easy to miss. Installing puts the library in
the project's venv; it cannot put the **skill** — the procedure an agent follows —
anywhere an agent looks, because installing is not allowed to write outside the
environment it installs into. So the skill travels inside the package and one
explicit command places it: in `~/.claude/skills` by default, or `--project` for
`./.claude/skills` alone. `skill show` prints it without installing anything, and
`skill where` answers why an agent did not use it.

The `git+` form has not been tried from this machine (no push credentials, and the
host is unreachable for git here); the local path form is what is exercised in the
table below.

| From a project that has it installed | Works |
| --- | --- |
| `uv run reportfast build --sessions-dir DIR -o r.html` | yes |
| `python -c "import report_fast"` | yes |
| `reportfast skill show --reference examples/dysplasia_case.json` | yes — the example sessions ship too |
| `uv run python scripts/derive_schema.py` | **no**: re-deriving the contract is a repo step, needs a checkout |

### Check the install in thirty seconds

Four commands, each answering a different question, and none of them needing your
data. Run them from a project that has the package, in this order — a failure at
step *n* means step *n* is broken and the ones after it say nothing.

`reportfast` below is the console script the package installs into the
environment's `bin`. If it is not on your PATH — which is the normal state of a `uv`
project — prefix each line with `uv run`, or activate the venv first. Every command
in this README is written the short way for that reason, and every one of them takes
the prefix.

```bash
reportfast --version        # is the package importable at all, and which viewer was it verified against
reportfast skill where      # does an agent on this machine find the procedure
reportfast skill show --reference examples/dysplasia_case.json > /tmp/s.json
reportfast plan --session /tmp/s.json     # do the gates work, on a session that ships
reportfast find <a run id>  # only if you use MLflow: is the tracking API reachable from here
```

`--version` prints `report-fast 0.1.0 (viewer 3.1.0 @ 18c94f2b)`. The second half is
the point: every validation rule was parsed out of that viewer commit, so a report
this tool says is good is good *for that viewer build*. Anything else on that line —
`(viewer ? @ )`, or `contract unreadable: …` — means the install has no schema
artifacts, which makes every gate in the package unable to answer. Treat it as a
broken wheel, not a warning to ignore.

`plan --session` with the shipped example exits 0 and prints the session's own
counts. It reaches no server, so it separates "the library is broken here" from "my
data is unreachable from here" — which is otherwise the single most confusing
failure in this tool, because a report of unreachable data builds successfully and
opens as black cards.

If `skill where` says not installed, run `reportfast skill install`. Nothing else
in this README will behave differently; that command only affects what an agent
follows.

Two ways to use it, and the first is the default:

**Ask.** You (or an agent) author one xOpat session document, the library validates
it against the viewer's own schema, a Python loop binds it to your cases, and one
command turns the result into a page. Nothing is kept except the HTML and a
provenance sidecar beside it.

```bash
uv sync --extra all
D=$(mktemp -d); mkdir -p $D/sessions
$EDITOR $D/design.json                    # one session; see skills/reportfast/SKILL.md
uv run reportfast plan --design $D/design.json --slot 0=slide --slot 1=mask
```

`plan` prints the slots, what the endpoint resolves to, and the Python that binds
the design to your case list — which is the step in the middle, and is Python
because a loop over 300 cases does not fit in a flag:

```python
import json
from pathlib import Path
from report_fast import load_design

design = load_design(f"{D}/design.json", slots={0: "slide", 1: "mask"})
for name, slide, mask in cases:            # from your listing, not from a guess
    session = design.bind(slide=slide, mask=mask, name=name)
    Path(f"{D}/sessions/{name}.json").write_text(json.dumps(session.to_config()))
```

```bash
uv run reportfast build --sessions-dir $D/sessions -o $D/report.html
```

**Write it down.** A YAML manifest resolves slide and mask folders (or MLflow runs)
for you, applies `only:` and `min_layers:`, and is a file a colleague can diff,
review and re-run. That is worth keeping when a report is standing infrastructure,
and is not worth a one-off answer to a question.

```bash
uv run reportfast plan  manifests/dysplasia.yaml   # writes nothing; read this first
uv run reportfast build manifests/dysplasia.yaml   # one HTML file, then probes
xdg-open reports/dysplasia.html
```

`manifests/dysplasia.yaml` resolves against this cluster's mount and its MLflow
runs, so those two lines work here and nowhere else. To point the tool at your own
data, copy `manifests/example.yaml` and edit four things — `title:`, the
background path, one mask path, a colour. `plan` exits 1 and names the key if a
path is wrong, so copying it and running `plan` is the whole tutorial.

## The command line

Three commands, three different blast radii, and two doors each: a manifest path,
or authored sessions. `reportfast` is installed by the package (`uv sync --extra
manifest` is enough for local folders); `uv run reportfast …` works in this
checkout.

```bash
reportfast plan  reports/foo.yaml              # resolve and report; writes nothing
reportfast build reports/foo.yaml              # write the HTML, then probe every DataID
reportfast build reports/foo.yaml --publish    # the only write to MLflow
reportfast find  <run-id> --path tile_masks    # list what a run's artifacts hold
reportfast --version                           # tool version + the viewer it was verified against

# the same three gates, from authored sessions instead of a YAML file:
reportfast plan  --design $D/design.json --slot 0=slide --slot 1=mask
reportfast build --sessions-dir $D/sessions --title "Cohort QC" -o $D/report.html
reportfast build --sessions-dir $D/sessions --design $D/design.json  # + record the design
reportfast build --sessions-dir $D/sessions --publish --run RUN   # page on the run only
reportfast build --sessions-dir $D/sessions --emit-manifest   # print, never write
```

The door changes what is read, not what is checked: every session walks the same
gate, the same components are composed, the same probe runs, the same exit codes
come back, and both doors write the same pair of files. Two build paths that "both
probe" is how one of them stops probing, so they literally share the code.

`plan` is the artifact worth reading before a build: cases per layer, layers per
case, files per source, what `min_layers:` dropped, and every warning. On
`--design` it prints the design's slots, what the endpoint will resolve to, and the
Python that binds it — it validates one design and binds nothing, because the loop
over 300 cases is Python and a flag that could express it would need a case-list
file. `plan` cannot write — there is no flag that makes it.

`--design` means the same thing on `build` and does not bind there either: it
names the design your loop bound the folder from, so the sidecar records one
document instead of `null`. Pass `--slot` the way you passed it to `load_design`
(`{0: slide}` and `{0: slide, 1: mask}` are different reports from one JSON), and
it is gated on the way in — a design that would not boot stops the build rather
than landing in a record that vouches for it. What the record cannot check is
whether the folder really came from that design: a bound session carries its
DataIDs and no note of its parent, so the flag is a claim about your own loop.

`build` writes the HTML and then asks the tile server about every DataID in it,
because a report whose links were never resolved is a report nobody has checked:
the viewer loads, the card is black, and nothing anywhere reports an error.
Publishing is `--publish` and nothing else — not a `publish:` key in the manifest,
not CI — and it repeats the run id back before uploading. From the manifest-less
door it needs `--run`, because there is nothing to infer a destination from.

| Exit | Meaning |
| --- | --- |
| `0` | fine |
| `1` | the *spec* is wrong: a bad manifest key, a missing case, a layer that lands on nothing, or an authored session the viewer would not load as written |
| `2` | the HTML was written and a DataID did not resolve |
| `3` | an optional extra this command needs is not installed |
| `4` | nothing to work on: no such manifest, no such folder, no such run, no command, bad flag |

1 and 2 are different failures with different fixes — one is a line in a file you
can edit, the other is a run id or a mount that is wrong — so they do not share a
code, and neither is argparse's default 2. 1 and 4 are split for the same reason:
a folder that is not there is an errand, a session the viewer would drop is a
mistake to go and fix in that one file, and a CI job should not have to read the
message to tell them apart.

Endpoint flags work on `plan` and `build` (`--base-url`, `--wsi-base-url`,
`--image-protocol`, `--mount-root`) and beat both the manifest's `endpoint:` and
the environment, which is what lets one manifest be aimed at a second deployment
without editing it. `build` takes `-o/--out`, `--no-check`, `--check-only`
(probe, write nothing — not the HTML, not the sidecar), `--run` (where to publish),
and on the authored door `--layout grid|rows` / `--no-grid`. A block's name is the
component's own (`SlideGrid`), not the manifest's snake_case (`slide_grid`), and the
error says so rather than telling you to invent a component.

## Every option in one place

The sections above say *why* each thing exists. This one is the list, for when you
are at a terminal and only want to know whether a flag exists and what it defaults
to. `reportfast <command> --help` is always the version that matches what you
installed, and says more; nothing here overrides it.

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

These beat the manifest's `endpoint:` and the environment, which is what lets one
manifest be aimed at a second deployment without editing it.

### What the page says and where it goes — `plan` and `build`

| | |
| --- | --- |
| `--title TITLE` | page title, for a report built from `--sessions-dir`; a manifest carries its own |
| `--subtitle SUBTITLE` | one line under the title, same condition |

### Only `plan`

| | |
| --- | --- |
| `--json` | machine-readable cases, coverage, sources and warnings — for a script that checks a report rather than a person reading one. The text plan says the same things; this is the same data without prose around it |

### Only `build`

| | |
| --- | --- |
| `-o, --out OUT` | where to write; defaults to the manifest's `out:`, else beside it. With `--publish` on a manifest-less build, nothing local is written unless you give this |
| `--no-check` | skip the DataID probe — offline, or a report that will not be opened |
| `--check-only` | build in memory and probe; write neither HTML nor sidecar |
| `--publish` | upload the report and its provenance (plus manifest and plan, when there is a manifest). **The only write this tool performs** |
| `--run RUN_ID` | publish into this run instead of the manifest's `publish:`; required on the manifest-less door |
| `--layout grid\|rows` | card layout; default `grid` |
| `--no-grid` | the same as `--layout rows` |
| `--emit-manifest` | print a manifest that would reproduce this report; writes nothing |

### Only `find`

| | |
| --- | --- |
| `RUN_ID` | a run id you already have — nothing here searches MLflow |
| `--path PATH` | artifact subdirectory to list; default the root |
| `-r, --recursive` | descend into subdirectories |
| `--slides` | only files with a slide suffix |
| `--data-ids` | one DataID per line, nothing else, for piping |

### Only `skill`

| | |
| --- | --- |
| `show` | print `SKILL.md` |
| `show --reference NAME` | print one bundled file instead — `references/xopat-v3.md`, `examples/dysplasia_case.json` |
| `install` | place the skill in `~/.claude/skills` |
| `install --project` | `./.claude/skills` instead, this repository only |
| `install --dest DIR` | somewhere else entirely |
| `install --force` | replace a skill that is already there — check first whether anyone edited it |
| `install --link` | symlink, so a checkout edit is live at once |
| `where` | say where the skill is and whether an agent would find it |

### Manifest keys

Every key is checked strictly: one the library does not know stops the build and
names the nearest candidate. A key that silently did nothing is the failure this
exists to prevent — `min_layer:` instead of `min_layers:` reads as a report that
was filtered. [manifests/example.yaml](manifests/example.yaml) is the four-key
starting file; [skills/reportfast/manifest.example.yaml](skills/reportfast/manifest.example.yaml)
lists the rest.

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
| `sessions_from` | `module:function` — your own code returning the sessions. The door for cases the library cannot list |
| `params`, `preset` | session defaults for every case |
| `metrics`, `charts`, `blocks` | the components around the cards — `prose`, `heading`, `bullets`, `links`, `metrics`, `chart`, `section`, `raw_html` |
| `endpoint` | `base_url`, `wsi_base_url`, `image_protocol`, `mount_root` |
| `flow` | `tracking_uri`, `web_url`, `artifact_prefix` |
| `publish` | where `--publish` would log. **Setting it uploads nothing** |

Precedence, lowest to highest: **builtin defaults → `preset` /
`$XOPAT_SESSION_CONFIG` → a pasted config → manifest keys → flags**. A preset can
never carry `data`, `background` or `visualizations` — those are per-slide content
a default would silently rewire into the wrong indices.

## Four ways in

The CLI is the same machinery as these four calls; a manifest is `build_report()`
written down, and a `Composition` is a manifest held in memory instead. Read
[manifest.py](report_fast/manifest.py) for the manifest vocabulary,
[compose.py](report_fast/compose.py) for the in-memory one, and
`manifests/example.yaml` for a file to copy.

**Just paths.** Backgrounds in, report out:

```python
from report_fast import build_report

build_report(
    ["/mnt/data/slides/case_001.tif", "/mnt/data/slides/case_002.tif"],
    masks=["/mnt/data/masks/tumor.tif"],          # over every slide
    title="QC",
    out="report.html",
)
```

**Named overlays, wherever they live.** A background, then the masks over it —
from a mounted folder or from the artifacts of an MLflow run. This is the shape
of a real analysis job, and the shape the original tool's Hydra config had:

```python
from report_fast import Drive, Mask, MlflowRun, build_report

build_report(
    background=Drive("/mnt/data/IKEM/colon/IBD_AI/dysplasia"),
    masks=[
        Mask("Tissue", MlflowRun("97084241311949189445f864d42e9d4e", "tissue_masks"),
             color="#ffff00", opacity=0.5),
        Mask("Annotations", MlflowRun("41d5e1d7d43641ea8f645f9b7945e9f7", "annot_masks"),
             classes=3, palette=["#ffffff", "#ff0000", "#00ff00"],
             breaks=[0.25, 0.75], mask=[0, 1, 1]),       # class 0 stays clear
        Mask("epithelium", Drive("/mnt/projects/.../epithelium_masks/downscale"),
             visible=True),
    ],
    only=["1094_18_HE_0", "8625_13_HE_A"],   # the cases, in report order
    min_layers=3,                            # fewer overlays: not a finding
    title="Dysplasia report",
    out="report.html",
)
```

Each mask is one file per case, matched to its slide by stem (`case_001.svs`
and `case_001.tiff` are the same case), and becomes the DataID the tile server
addresses — nothing is read or downloaded. `MlflowRun` is the only part that
needs the extra (`uv sync --extra mlflow`, see [Runs in MLflow](#runs-in-mlflow)).
`scripts/dysplasia_tile_masks.py` is that call as Python, run for real;
`manifests/dysplasia.yaml` is the same report as a YAML file, and the two are
kept in step deliberately — the manifest is what a person can diff and correct.

**Your own session.** Whatever the tool does not model, you write:

```python
from report_fast import SlideCard, XopatSession

session = XopatSession.from_url("https://xopat…/v3/#%7B…%7D")   # a pasted link
session = XopatSession.from_file("my_session.json")             # a pasted file
session = XopatSession.from_config({"data": [...], "background": [...]})

SlideCard(session).to_html()
```

**Your own design, bound many times.** The agent path, and the one the CLI's
`--sessions-dir` door is for. One hand-written session document with slots; N
bindings; a composition; one file out.

```python
from report_fast import Composition, load_design

design = load_design(
    f"{D}/design.json", slots={0: "slide", 1: "mask"}, endpoint=endpoint
)   # audited strictly, once; every bind below inherits that verdict

page = Composition(title="Cohort QC", blocks=[])
for case in cases:
    page.blocks.append({"SlideCard": {"session": design.bind(
        slide=case.slide, mask=case.mask, name=case.name,
    )}})
built = page.build(out=f"{D}/report.html")     # writes, probes, records
```

The design is written by hand. `load_design` is the gate that makes that safe:
`data[]` is positional, so the audit resolves every `dataReference` /
`dataReferences` index and refuses one that is out of range, which is the mistake a
300-case copy-paste eventually makes.

Both paths end at the same `XopatSession`, so code you write yourself and code
that goes through this package emit the same links.

## The base object

```python
from report_fast import XopatSession

session = XopatSession.from_slide(
    "/mnt/data/slides/case_001.tif",                  # the background
    [{"path": "/mnt/data/masks/prob.tif", "name": "Probability", "type": "heatmap"}],
    name="case_001",
)
session.url()          # https://…/v3/#%7B"data":…%7D
session.thumbnail()    # the tile server's thumbnail for that slide
session.to_json()      # the session itself
```

Two invariants hold everywhere in the package:

- **A config that arrives is a config that leaves.** Importing a session keeps
  fields this tool has never heard of, in the order they came. The paste path
  exists so you are not limited by the tool; normalising away what we do not
  model would defeat it. Only viewer navigation state (`params.viewport`,
  `activeBackgroundIndex`) is dropped, and `drop_state=False` keeps even that.
- **Indices are the tool's job.** `data[]` is a positional pool and
  `background[].dataReference` / `shaders[].dataReferences` index into it.
  Appending a second session renumbers its references; nothing you build by
  hand can point at the wrong tile.

A session is **one viewer instance**, not one slide: it may carry several
backgrounds (timepoints, stains, channels) that the reader switches between. Say
*session*, not *slide*.

```python
plan = XopatSession.from_slide("case/plan.nii", layers, name="Plan")
followup = XopatSession.from_slide("case/fu.nii", layers, name="Follow-up")
plan.merge(followup)                       # one session, two backgrounds
```

### Many slides at once

```python
from report_fast import SessionTemplate, sessions_from_folder

sessions = sessions_from_folder("/mnt/data/slides", masks=["/mnt/data/masks/x.tif"])

# Or reuse a session somebody pasted, refilling only its data:
template = SessionTemplate.from_config(json.loads(pasted), slots={0: "slide", 1: "mask"})
session = template.bind(name="case_001", slide="case_001.tif", mask="prob_001.tif")
```

`bind` replaces the `dataID` at each slot and keeps the protocol, tile options
and shader config written around it, so a hand-authored session becomes a form
with the paths as blanks.

## The report

```python
from report_fast import Chart, MetricTable, Prose, Report, SlideGrid

report = Report(title="TNBC dysplasia screen", subtitle="12 tiles")
report.add(Prose(text="Screens run overnight; overlays are the model's."))
report.add(SlideGrid(sessions, collapsible=True))
report.add(MetricTable({"dice": 0.83, "tiles": 12}))
report.add(Chart.from_matplotlib(fig))
report.write("report.html")
```

Components in the box: `Prose`, `Heading`, `Bullets`, `LinkList`, `SlideCard`,
`SlideGrid`, `MetricTable`, `Chart`, `Section`, `Report`. They take sessions and
plain data — no database, no metric store, no slide reader. Collapsing is
`<details>`; the only interactivity is the viewer behind the link.

Those ten are also the **frozen set**, and that is a rule rather than a list: on
the authored path, page HTML comes from them and nowhere else, so the same
composition renders the same page every time it is built. `RawHtml` exists — it is
how a manifest keeps a human's hand-written block — but it is refused **by code**
on the agent path, including `Prose(text="<div …>")` and nested specs. The gate is
[frozen.py](report_fast/frozen.py); `reportfast` refuses with the constructor's own
signature, because "wrong keyword" is not actionable without the right ones.

Your own component is a subclass with two methods:

```python
from fasthtml.common import Div
from report_fast import BaseComponent

class Finding(BaseComponent):
    component_type = "finding"

    def __init__(self, text, **kwargs):
        super().__init__(**kwargs)
        self.text = text

    def css(self) -> str:                                  # inlined once, by the report
        return ".rf-finding { color: #555; }"

    def render(self):
        return Div(self.text, cls="rf-finding", id=self.id)
```

Pick your own class prefix: `.rf-note`, `.rf-card` and the rest are already taken
by the components in the box, and their stylesheets land on the same page.

## What a build leaves behind

Two files, and nothing else:

```
report.html                 the report
report.provenance.json      what it was built from
```

The sidecar records the endpoint the links were built against, the viewer version
and commit the schema was derived from, the tool version, what the build was given
(a folder, a manifest, a layout), every DataID the page links, and — when the
sessions came from a template — the **design as authored**, one document rather
than 300 instantiations. It is generated from data that already resolved, never
from the spec that declared it: the old failure was a published report and its
logged config disagreeing because one was resolved and one was meant.

It is a *sidecar*, and the trade-off is deliberate and accepted: **nothing is
stamped into the page**, so no footer, no comment, and **a mailed HTML carries no
provenance**. The page looks identical whether or not anyone is keeping records
(that is tested, byte for byte). If a report must be self-explaining wherever it
goes, deliver the pair, or keep a manifest. See
[provenance.py](report_fast/provenance.py); both files are gitignored.

Neither file appears when you did not ask for a local copy. `--publish` with no
`-o` on a manifest-less build uploads the page and its record to the run and leaves
the directory you ran it in alone — `--run` named the destination, so guessing a
second one beside whatever command happened to precede it is not the tool's call.
`-o` asks for both.

## Runs in MLflow

`report_fast.mlflow` reads a run's artifacts and writes the report back into the
run. It is optional — `uv sync --extra mlflow`, imported lazily, and everything
else keeps working without it.

```python
from report_fast import Mlflow, build_report

flow = Mlflow(tracking_uri="http://mlflow.rationai-mlflow:5000/")
run = "5b72e2a73b3941f0be63e232d8072127"

report = build_report(
    title="Level 1 heatmaps",
    slides=flow.slides(run, "heatmaps/epi0_negon"),   # DataIDs, not files
    masks=flow.masks(run, "heatmaps/epi0_negoff"),    # paired with slides by file stem
    metrics=flow.metrics(run),
)
print(flow.publish(report, run_id=run).url)           # uploads report/report.html
```

Nothing is downloaded. `list_artifacts` names the files and each becomes the
DataID the tile server resolves — `mflow/<experiment>/<run>/artifacts/<path>` —
which is exactly what `slides=` and a layer's `path` already take. Slides that
live on a mount instead of in a run go in as usual, with `masks=flow.masks(run,
"predictions")` putting that run's overlays onto them by file stem.

`publish()` is the only call that writes. With `run_id=` it attaches the report
to that run; without one it creates a run in `experiment_name=`, named,
described and owned as the original tool's storer did, and returns
`Published(run_id, artifact, url, created_run)`.

The web URL is a **separate setting** from the tracking URI (`web_url=`,
`MLFLOW_WEB_URL`): the API is usually reachable only inside the cluster, and the
links in a report have to open in a browser. The extra is capped below mlflow 3
because the tracking server here speaks the 2.x API — a 3.x client calls
endpoints it does not have, and listing artifacts 404s.

`uv run python scripts/test_mlflow.py` builds that report from the live run and
uploads nothing unless you pass `--publish <run_id>`.

## Deployment

| Variable | Default | Purpose |
| --- | --- | --- |
| `XOPAT_BASE_URL` | `https://xopat.rationai.cloud.trusted.e-infra.cz/v3/` | Viewer root the fragment is appended to |
| `XOPAT_WSI_BASE_URL` | `https://xopat.rationai.cloud.trusted.e-infra.cz/wsi-service/` | Tile server answering `/v3/slides/…` (thumbnails) |
| `XOPAT_IMAGE_PROTOCOL` | *unset* | Name of a `slide_protocols` entry for backgrounds; unset defers to the deployment default |
| `XOPAT_MOUNT_ROOT` | `/mnt` | Prefix stripped from slide paths to form the DataID; `""` keeps them absolute |
| `XOPAT_SESSION_CONFIG` | *unset* | JSON/TOML file of session defaults |
| `MLFLOW_TRACKING_URI` | mlflow's own default | Tracking API runs are read from and written to |
| `MLFLOW_WEB_URL` | `https://mlflow.rationai.cloud.trusted.e-infra.cz/` | Base of the run links inside a report |
| `REPORTFAST_MLFLOW_ARTIFACT_PREFIX` | `mflow` | DataID namespace the deployment serves artifacts under |

**The base path selects the viewer version.** This host serves v2 at `/xopat/`
and v3 at `/v3/`; a v3 session opened by v2 *looks* like it loaded, so a wrong
mount fails quietly rather than 404ing.

**Which is why a 404 is a different problem.** A wrong mount shows a viewer that
loaded and an empty canvas; a server's own 404 page means the path never reached
the viewer at all — the mount is missing or the deployment is down. Those two read
alike in a bug report and have opposite fixes, so say which one you saw. And a URL
fragment is never sent to a server: the `#…` half of a card link cannot be what a
server rejected, however much it looks like the thing that differs.

Pass coordinates per call instead with `XopatEndpoint(base_url=…, wsi_base_url=…,
image_protocol=…, mount_root=…)`.

Slide paths are shared state: the machine building the report and the tile server
must see the same file under the mount root, because a session names slides by
path, never by bytes.

### Defaults you can edit without touching Python

`XOPAT_SESSION_CONFIG` points at a session-shaped file merged under everything:

```json
{
  "params": {"theme": "dark", "ui": {"toolBar": true}},
  "layers": [{"type": "colormap", "params": {"opacity": 0.5}}],
  "protocol": "wsi_service",
  "options": {"format": "png"},
  "endpoint": {"base_url": "https://other.host/v3/", "mount_root": "/data"}
}
```

Precedence is **builtin → preset → pasted config → arguments**. A preset cannot
carry `data`, `background` or `visualizations`: those are the per-slide content
a preset would silently rewire into the wrong indices. Pass `preset=` for one
call.

`sessionName` is the viewer's persistence namespace, not a title — slides
sharing one also share their saved zoom and pan. Leave it unset and the viewer
derives one per slide.

## Layout

```
report_fast/
├── xopat.py        wire format: endpoint, DataIDs, thumbnails, session → URL
├── session.py      XopatSession, SessionTemplate, sessions_from_folder/paths
├── config.py       SessionPreset, the environment preset file
├── shader.py       layer types and their parameters, as v3 declares them
├── masks.py        Mask + where its files come from: Drive, MlflowRun
├── core.py         BaseComponent, ComponentRegistry, Report, Section
├── build.py        build_report(): paths or masks → report, in one call
├── manifest.py     YAML spec → build_report: strict keys, plan, resolve
├── compose.py      the same composition in memory, and the --sessions-dir door
├── contract.py     the generated xOpat facts every gate reads (schema/, no copies)
├── audit.py        one session → findings with JSON paths; the severity split
├── frozen.py       the ten components the page may be made of, and the gate
├── provenance.py   report.provenance.json: the record, never the page
├── skill.py        the skill inside the wheel: find it, print it, place it
├── schema/         GENERATED from the pinned viewer: session schema, params
│                   allowlist, layer fields, and the version stamp (viewer.lock.json)
├── verify.py       every DataID → the tile server's /info, before a reader does
├── mlflow.py       runs: artifacts → DataIDs, report → run (needs the extra)
├── __main__.py     `reportfast plan | build | find | skill`, doors and exit codes
└── components/     prose.py · slide_grid.py · metrics.py · chart.py
scripts/derive_schema.py  regenerate schema/ from the pinned xOpat checkout, and
                    refresh the version stamp in the skill
manifests/          standing reports, as YAML: dysplasia.yaml, example.yaml
skills/reportfast/  the agent's half: SKILL.md, references/, an example manifest
examples/           session fixtures: a real viewer export + pasteable demos
tests/              test_xopat.py · test_session.py · test_masks.py ·
                    test_components.py · test_mlflow.py · test_manifest.py ·
                    test_verify.py · test_cli.py · test_contract.py ·
                    test_audit.py · test_frozen.py · test_compose.py ·
                    test_provenance.py · test_skill.py
scripts/            test_report.py, test_mlflow.py, dysplasia_tile_masks.py —
                    demos writing the reports below
reports/            generated HTML, one per manifest plus the demos
                    (generated, regenerate freely)
```

`xopat.py`, `session.py` and `config.py` are stdlib-only: a script that just
needs links imports nothing heavy and reads no slides.

## Tests

```bash
uv run python tests/test_xopat.py         # wire format: session shape, URL round-trip
uv run python tests/test_session.py       # the base object: paste path, indices, presets
uv run python tests/test_masks.py         # backgrounds and masks, from a folder or a run
uv run python tests/test_components.py    # the shell and the components
uv run python tests/test_mlflow.py        # runs in/out, against a fake client
uv run python tests/test_manifest.py      # YAML: strict keys, the plan, the three doors
uv run python tests/test_verify.py        # the probe: DataIDs read back, three answers
uv run python tests/test_cli.py           # the commands, both doors, each exit code
uv run python tests/test_contract.py      # the generated contract, and the skill's copy
uv run python tests/test_audit.py         # the gate: findings, paths, the severity split
uv run python tests/test_frozen.py        # the ten components, and what they refuse
uv run python tests/test_compose.py       # the in-memory composition and the door
uv run python tests/test_provenance.py    # the sidecar, and that the page is untouched
uv run python tests/test_skill.py         # the shipped skill: where it goes, what it claims
uv run python tests/test_examples.py      # the package docstring's examples, executed
uv run pytest tests
uv run ruff check .
```

`.github/workflows/ci.yml` runs the suite and the lint on the Python floor (3.10)
and on 3.12, installed with both extras so the YAML and MLflow paths are exercised
rather than skipped. It has no publish step and must not gain one — publishing is
never implied, and `test_no_workflow_publishes` fails the build if a workflow ever
grows a `--publish` or a tracking credential. The two checks that compare the
derived contract against a viewer checkout skip when `$XOPAT_VIEWER` is absent.

Every file is also a script — `python tests/test_cli.py` runs it with no pytest
installed. None of them reach a server: the run side is driven through a double
and the probe by stubbing the socket, so a green suite says nothing about whether
your tile server is up. It needs no mount and opens no slide.

Two properties are pinned rather than hoped for:

- **Reproducibility.** `build(x.yaml)` twice writes byte-identical HTML *and*
  byte-identical provenance — no timestamps, no generated code, fixed key order,
  and component ids derived from position rather than from a uuid. That is what
  lets a report be a build artifact you can diff against last week's, instead of a
  transcript nobody can check. Note what "same input" means since prompt mode:
  the page is reproducible by construction, and the sessions are inputs that vary
  — re-authoring a session may change a colour, and the HTML legitimately differs.
  Reproducibility is asserted over *(sessions + composition) → HTML*.
- **Publishing is never a side effect.** A manifest's `publish:` key is a
  destination, not an instruction; the tests assert that a build with a `publish:`
  and no `--publish` uploads nothing, and that `--publish` without a run id stops
  before it sends anything.

The suites pin the emitted session against what xOpat 3 actually parses — the
`params` allowlist, plural `dataReferences`, the palette/`threshold.breaks`
coupling, fragment round-trip — and against what it does *not* do: drop unknown
params silently, ignore inline-JS protocols, read a singular `dataReference`.

`scripts/test_report.py` and `scripts/test_mlflow.py` are demos, not tests —
`testpaths` keeps them out of `pytest`'s way. The first writes the two reports
into `reports/` against the slide directories named at the top, and falls back
to paths that do not exist where those mounts are absent — the report still
builds, because a session is a DataID string, not an opened file. The second
reads a real run out of MLflow and uploads nothing unless you pass
`--publish <run_id>`.

`scripts/dysplasia_tile_masks.py` and `manifests/dysplasia.yaml` are the same
thing as a real job: the report the original tool built from
`/home/jovyan/report/report/conf/dysplasia_tile_masks.yaml`, rebuilt from its
`mask_retrievers` in order with the same colours and opacities — 32 cases, eleven
overlays each, out of four MLflow runs and two mounted folders. The manifest is
the one to edit; `plan` reports the real coverage on it today (the annotations
layer reaches 21 of 32 cases, which is a fact about the data, not a bug).

`tests/test_mlflow.py` drives a double through `client=`, so the suite needs no
mlflow installed. That also means the real upload is not covered here: the first
time you point `publish()` at a server you have not used before, send it to a
store you own (`tracking_uri="file:/tmp/store"`) and read the artifact back with
`download()` — a 200 on `log_artifact` and a file the tile server can resolve are
different claims.

## v2 → v3

| v2 | v3 |
| --- | --- |
| `…/redirect.php?visualization=<json>` | `…/v3/#<urlencoded json>` (`redirect.php` was deleted upstream) |
| `dataReference: 0` on a shader layer | `dataReferences: [0]` — the singular key is ignored |
| `lossless: true` on a visualization | `{"dataID": "…", "options": {"format": "png"}}` on that layer's `data[]` entry |
| `params.toolBar` | `params.ui.toolBar` — and only `toolBar`/`statusBar`/`scaleBar` still work flat; `appBar`/`globalMenu`/`mainMenu`/`navigator` are stripped by the sanitizer before the fallback ever reads them |
| `classify` / `segmentation` / `bounding_box` | `colormap` / `colormap` / `iconmap` |
| inline JS protocol template | the **name** of an entry in the deployment's `slide_protocols` |
| `viewer.addLayer(...)` JS init | `visualizations[].shaders` |

Unknown params, retired layer types and dangling references raise `XopatError`
(or warn) at build time rather than failing in the browser.
