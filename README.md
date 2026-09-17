# ReportFast

ReportFast builds static HTML reports of pathology slides, where every card links
into the [xOpat](https://xopat.rationai.cloud.trusted.e-infra.cz/v3/) v3 viewer with
the whole session carried in the URL fragment. Nothing is served, no JavaScript runs
in the page, and the file can be mailed as it is.

Everything rests on one object. `XopatSession` holds the session document the viewer
boots from — the same JSON a viewer link carries — and turns it into a URL. On top of
it sit four pieces: a validation gate that refuses a session the viewer would not
load, a probe that asks the tile server whether every image the page references
actually opens, ten components that place sessions on a page, and a CLI that runs
them in the right order.

The failure mode this exists for is silent. If a link points at data the tile server
cannot open, the viewer still loads, the card renders black, and **no component
anywhere reports an error**. `build` therefore asks the image server about every
DataID before a report counts as finished.

**Out of scope.** No web server, no database, no metric store, no slide reader.
Nothing is uploaded unless `--publish` is passed. Sessions are authored by a person or
an agent, never invented by the library, and always checked on the way in.

## Installation

The package is not on PyPI. Either install it over git, or from a checkout under
edit:

```bash
uv add "report-fast @ git+https://github.com/RationAI/reporting.git"       # over git
uv add "report-fast[all] @ git+https://github.com/RationAI/reporting.git"  # + YAML and MLflow
uv add --editable /path/to/reporting      # a checkout under development

uv run reportfast skill install           # required for agent use
```

The last line matters more than it looks. Installing puts the library into a venv;
it cannot put the **skill** — the procedure an agent follows — anywhere an agent
looks, because installing may not write outside the environment it installs into. The
skill therefore ships inside the package, and this one command places it:
`~/.claude/skills` by default, or `./.claude/skills` with `--project`. Without it the
library works exactly as documented and no agent knows the procedure exists.
`skill show` prints the skill without installing it; `skill where` reports where it
is and whether an agent would find it.

Everything below behaves the same whether the package came from git, a checkout or the
wheel.

### Check the installation in thirty seconds

Four commands — five with MLflow — each answering a different question, none of them
needing any project data. Run them in this order: a failure at step *n* means step
*n* is broken and the later ones say nothing.

The console script lands in the environment's `bin`. If `reportfast` is not on
`PATH`, which is the normal state of a `uv` project, prefix every command in this
file with `uv run` or activate the venv first.

```bash
reportfast --version        # is the package importable, and against which viewer
reportfast skill where      # would an agent on this machine find the procedure
reportfast skill show --reference examples/dysplasia_case.json > /tmp/s.json
reportfast plan --session /tmp/s.json     # do the gates work, on a session that ships
reportfast find <run-id>    # MLflow only: is the tracking API reachable
```

`--version` prints:

```
report-fast 0.1.0 (viewer 3.1.0 @ 18c94f2b)
```

The second half is the point. Every validation rule was parsed out of that viewer
commit, so a report this tool calls correct is correct *for that viewer build*. Any
other reading of that line — `(viewer ? @ )`, or `contract unreadable: …` — means the
install has no schema artifacts and every gate is unable to answer. Treat that as a
broken install, not a warning to ignore.

`plan --session` on the shipped example exits 0 and prints that session's counts. It
contacts no server, which is what separates "the library is broken here" from "the
data is unreachable from here" — the latter being the most confusing failure in this
tool, because a report of unreachable data builds successfully and opens as black
cards.

If `skill where` answers `installed nothing at …`, run `reportfast skill install`.
Nothing else in this file behaves differently; that command only affects what an
agent follows.

## Tutorial 1 — a report from authored sessions

Two commands, the first of which writes no report. This is the entry point for
"produce a report from these slides": sessions are authored here, the library renders
the page, and nothing persists except the HTML and a record beside it.

```bash
mkdir -p myreport/sessions        # one JSON per case, authored here
$EDITOR myreport/sessions/case-01.json
cd myreport
```

A session is the JSON the viewer boots from. The fastest way to write the first one is
to start from a real one: `reportfast skill show --reference
examples/dysplasia_case.json` prints it, and
[skills/reportfast/SKILL.md](skills/reportfast/SKILL.md) explains every field. The
term is *session* rather than *slide* because one session may carry several
backgrounds — timepoints, stains, channels — that the reader switches between.

Resolve before building:

```bash
reportfast plan --sessions-dir sessions --title "QC pilot"
```

```
QC pilot: 1 cases, up to 1 overlays
out      None
publish  not set
```

`plan` resolves everything a build would do and writes nothing; there is no flag that
makes it write. Cases per layer, layers per case, files per source, everything
`min_layers:` dropped, and every warning all appear before a byte of HTML exists.

```bash
reportfast build --sessions-dir sessions --title "QC pilot" -o report.html
```

```
QC pilot: 1 cases, up to 1 overlays
out      report.html
publish  not set

wrote     report.html
probe     3/3 DataIDs open; 0 refused
```

The last line is the result of the probe: the build asked the tile server about all
three images the page links and received three confirmations. `open` and `refused` are
the server answering; `unreachable from here` means the question never got through,
which is a fact about the machine running the build rather than about the report — see
[When the probe says "unreachable"](#when-the-probe-says-unreachable-from-here).

Two files exist now, and nothing else:

```
report.html                 the report — open it, click a card
report.provenance.json      what it was built from
```

### When a session is invalid

Add a misspelled key to one session — `threshhold` for `threshold` — and `plan` stops
there:

```
reportfast: case-02.json: will not load in xOpat v3 as authored -- params.threshhold:
not in the viewer's params allowlist; `sanitizeAgainst` (src/app.ts) drops it and logs
to the console, so the viewer boots as if you had not set it. Fix the JSON at those
paths. This is the agent's door, so a key the viewer would merely drop is refused
rather than warned about: off-allowlist means a typo until proven otherwise.
```

The viewer would not have reported this: it boots, drops the key, and displays a
report whose threshold was never set. The rule comes from the pinned viewer source
rather than from a hand-maintained list. In that message, "the agent's door" means
this entry point — sessions authored rather than resolved from a manifest.

The exit code is 1 — a mistake in an editable file — as distinct from 2, which means
the page was written and an image refused to resolve, i.e. a wrong run id or mount.
[The command line](#the-command-line) carries the full table.

## Tutorial 2 — a report as a YAML manifest

A manifest resolves slide and mask folders (or MLflow runs), filters with `only:` and
`min_layers:`, and is a file that can be diffed, reviewed and re-run. Prefer it when a
report is standing infrastructure; for a one-off answer, Tutorial 1 is fewer steps.

[manifests/example.yaml](manifests/example.yaml) is the starting file: copy it and
change `title:`, the background path, one mask path and a colour.

```bash
reportfast plan my.yaml
```

```
My cohort: 2 cases, up to 1 overlays
sources
  Drive(data/slides)  2 files
  Drive(data/masks)  2 files
coverage  (cases that got a file from each layer)
  Tissue: 2/2
out      /home/me/work/../reports/example.html
publish  not set
```

`coverage` is the number to read first: `Tissue: 2/2` means every case received that
overlay. `Tissue: 3/32` means either a gap in the results or a mistyped source, and
the tool requires the difference to be decided rather than showing a slide with no
mask. The `out` line is the copied file still carrying the shipped `out:`, resolved
against the manifest's own folder — the fifth field to change; `out: report.html` is
sufficient.

```bash
reportfast build my.yaml      # writes the HTML, then probes
```

Manifest keys are checked strictly: an unknown key stops the build and names the
nearest candidate, because a `min_layer:` that silently did nothing reads as a report
that was filtered. Paths resolve relative to the manifest, so the file is portable. A
path that does not exist on the machine is an error naming that path, not an empty
report. [The manifest keys](#manifest-keys) lists all of them.

`manifests/dysplasia.yaml` is the same vocabulary with everything switched on — MLflow
run sources, class maps, `only:`, prose blocks — resolved against this deployment's
mount and runs, so it works on this cluster and nowhere else.

## Tutorial 3 — one design, many cases

This is Tutorial 1's folder at scale, and the path an agent takes when the report is
300 cases rather than one. One session document is written with blanks in it, a Python
loop fills the blanks per case, and the same `build --sessions-dir` turns the folder
into a page.

```bash
D=$(mktemp -d); mkdir -p $D/sessions
$EDITOR $D/design.json                      # one session, slots where paths go
reportfast plan --design $D/design.json --slot 0=slide --slot 1=mask
```

`plan --design` prints the design's slots, what the endpoint resolves to, and the
Python that binds it. Binding is Python because a loop over 300 cases does not fit in
a flag, and a flag that pretended to would need a case-list file anyway:

```python
import json
from pathlib import Path
from report_fast import load_design

design = load_design(f"{D}/design.json", slots={0: "slide", 1: "mask"})
for name, slide, mask in cases:            # from a listing, not from a guess
    session = design.bind(slide=slide, mask=mask, name=name)
    Path(f"{D}/sessions/{name}.json").write_text(json.dumps(session.to_config()))
```

```bash
reportfast build --sessions-dir $D/sessions -o $D/report.html --design $D/design.json
```

`load_design` is what makes a hand-written session safe to use. `data[]` is a
positional pool and every layer indexes into it, so the audit resolves each
`dataReference` and refuses one that is out of range — the error a 300-case copy-paste
eventually produces. The design is gated once and every `bind` inherits that verdict.

`--design` on the build records the design in the sidecar instead of `null`, and does
**not** bind: pass `--slot` exactly as it was passed to `load_design`, because
`{0: slide}` and `{0: slide, 1: mask}` are different reports from one JSON. What the
record cannot check is whether the folder really came from that design — a bound
session carries its DataIDs and no reference to its parent — so the flag states what
the calling loop did.

The folder of JSON is optional. `Composition` performs the same build with the
sessions held in memory; see [The Python API](#the-python-api).

## Choosing an entry point

| Available | Use |
| --- | --- |
| A folder of session JSON, or a few pasted sessions | `--sessions-dir` / `--session` |
| Slide and mask folders or MLflow runs, and the recipe belongs in a file | a YAML manifest |
| Paths already in a script | `build_report()` |
| Sessions from a source the library cannot list | `sessions_from:` in a manifest, or `Composition` |

The entry point changes what is read, not what is checked. Every session passes the
same gate, the same components compose it, the same probe runs, the same exit codes
return, and both CLI entry points write the same pair of files. Two build paths that
"both probe" is how one of them eventually stops probing, so they share the code
rather than duplicating it.

Two commands exist to be run *before* a report is trusted:

```bash
reportfast plan my.yaml                    # resolve and report; writes nothing
reportfast build my.yaml --check-only      # build in memory and probe; write nothing
```

And one that is never implied:

```bash
reportfast build my.yaml --publish         # the only write this tool performs
```

Publishing is `--publish` and nothing else — not a `publish:` key in a manifest, not
CI, not a prompt. The run id is repeated back before anything is uploaded, and a
manifest's `publish:` is a destination, not an instruction. On a manifest-less build it
requires `--run`, because there is nothing to infer a destination from; there it puts
the page on the run and writes **no local file** unless `-o` is also given. CI has no
publish step, and a test fails the build if one ever appears.

To obtain the manifest form of a report built from the command line, `build
… --emit-manifest` prints a manifest that would reproduce it and writes nothing.

### When the probe says "unreachable from here"

`probe 0/N DataIDs open; 0 refused, N unreachable from here` exits 0 and means the
machine running the build could not reach the tile server. Two different causes look
identical:

- **`0 refused` is the distinction.** A *refused* DataID is the server answering no;
  unreachable is the server never answering. Refused means a `mount_root`, a protocol
  or a run needs correcting. Unreachable means the request either never left or died
  in transit.
- **A proxy is the usual cause, in both directions.** From a pod or CI runner behind
  an HTTP proxy, requests to a cluster hostname may be sent *through* the proxy and
  time out although the same host is reachable directly — and a hostname reachable
  only via the proxy fails identically when the proxy is bypassed. Same message,
  opposite fixes, one environment variable apart (`NO_PROXY` / `HTTPS_PROXY`), so
  confirm with one direct request before drawing any conclusion about the report.

Then state which of the two applies. "Built, links unverified from here" is a complete
and accurate sentence about a report; "it works" is not, and anyone who opens it and
finds black cards has to rediscover the difference alone.

### What is in the rest of this file

[The command line](#the-command-line) explains how the commands are divided, and
[Every option in one place](#every-option-in-one-place) is the flag and key reference
for checking whether an option exists and what it defaults to.
[The Python API](#the-python-api) is the same machinery the CLI runs, for cases a flag
cannot express. Then the artifacts a build leaves, MLflow, deployment coordinates,
repository layout and tests.

## The command line

Four commands. `plan` and `build` do the work, each reachable through a manifest path
or through authored sessions; the other two answer questions without touching a
report.

```bash
reportfast plan  reports/foo.yaml              # resolve and report; writes nothing
reportfast build reports/foo.yaml              # write the HTML, then probe every DataID
reportfast build reports/foo.yaml --publish    # the only write to MLflow
reportfast find  <run-id> --path tile_masks    # list what a run's artifacts hold
reportfast skill where                         # would an agent find the procedure
reportfast --version                           # tool version + the viewer it was verified against

# the same gates, from authored sessions instead of a YAML file:
reportfast plan  --design $D/design.json --slot 0=slide --slot 1=mask
reportfast build --sessions-dir $D/sessions --title "Cohort QC" -o $D/report.html
reportfast build --sessions-dir $D/sessions --design $D/design.json  # + record the design
reportfast build --sessions-dir $D/sessions --publish --run RUN      # page on the run only
reportfast build --sessions-dir $D/sessions --emit-manifest          # print, never write
```

If the console script is not on `PATH` — the normal state of a `uv` project — prefix
each line with `uv run`. Within this repository `uv run reportfast …` is what works,
and `uv sync --extra manifest` is enough for local folders.

Options are listed in [Every option in one place](#every-option-in-one-place), and
`reportfast <command> --help` is always the version matching what is installed. What
follows is only the reasoning a flag table cannot carry.

`plan` is the artifact to read before a build: cases per layer, layers per case, files
per source, what `min_layers:` dropped, and every warning. With `--design` it validates
one design and binds nothing, because the loop over 300 cases is Python and a flag able
to express it would need a case-list file. `plan` cannot write — there is no flag that
makes it.

`build` then asks the tile server about every DataID it linked, because a report whose
links were never resolved is a report nobody has checked: the viewer loads, the card is
black, and no component reports an error. `--check-only` runs the whole thing in memory
and writes neither HTML nor sidecar; `--no-check` skips the probe when the server is
known to be unreachable.

`--design` means the same thing on `build` and does not bind there either: it names the
design the loop bound the folder from, so the sidecar records one document instead of
`null`. Pass `--slot` as it was passed to `load_design`, and the design is gated on the
way in — one that would not boot stops the build rather than landing in a record that
vouches for it.

Endpoint options work on `plan` and `build` (`--base-url`, `--wsi-base-url`,
`--image-protocol`, `--mount-root`) and take precedence over both the manifest's
`endpoint:` and the environment, which is what allows one manifest to be aimed at a
second deployment without being edited. `build` takes `-o/--out`, `--run` (where to
publish), and `--layout grid|rows` / `--no-grid`. The layout options belong to the
authored-session entry point in meaning: a report from a manifest lays its cards out by
that file's `grid:`, which a flag should not silently overrule. A block's name on the
composition entry point is the component's own (`SlideCard`), not the manifest's
snake_case (`slide_card`), and the error names the spelling that works rather than
implying a component has to be written.

`--publish` is the one write, it repeats the run id back before uploading, and on a
manifest-less build it needs `--run` because there is nothing to infer a destination
from — [Choosing an entry point](#choosing-an-entry-point) is where that rule is set
out. Where each artifact lands is in
[What a build leaves behind](#what-a-build-leaves-behind).

| Exit | Meaning |
| --- | --- |
| `0` | fine |
| `1` | the *spec* is wrong: a bad manifest key, a missing case, a layer that lands on nothing, or an authored session the viewer would not load as written |
| `2` | the HTML was written and a DataID did not resolve |
| `3` | an optional extra this command needs is not installed |
| `4` | nothing to work on: no such manifest, no such folder, no such run, no command, bad flag |

1 and 2 are different failures with different fixes — one is a line in a file, the
other a wrong run id or mount — so they do not share a code, and neither is argparse's
default 2. 1 and 4 are split for the same reason: a folder that is absent is a
retrieval step, a session the viewer would drop is a correction to one file, and a CI
job should not have to parse the message to tell them apart.

## Every option in one place

The sections above give the reasoning; this one is the list, for checking whether an
option exists and what it defaults to. `reportfast <command> --help` is always the
version matching the install and says more; nothing here overrides it.

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

### What the page says and where it goes — `plan` and `build`

| | |
| --- | --- |
| `--title TITLE` | page title, for a report built from `--sessions-dir`; a manifest carries its own |
| `--subtitle SUBTITLE` | one line under the title, same condition |

### Only `plan`

| | |
| --- | --- |
| `--json` | machine-readable cases, coverage, sources and warnings — for a script that checks a report rather than a person reading one. The text plan reports the same things; this is the same data without prose around it |

### Only `build`

| | |
| --- | --- |
| `-o, --out OUT` | where to write; defaults to the manifest's `out:`, else beside it. With `--publish` on a manifest-less build, nothing local is written unless this is given |
| `--no-check` | skip the DataID probe — offline, or a report that will not be opened |
| `--check-only` | build in memory and probe; write neither HTML nor sidecar |
| `--publish` | upload the report and its provenance (plus manifest and plan, when there is a manifest). **The only write this tool performs** |
| `--run RUN_ID` | publish into this run instead of the manifest's `publish:`; required when there is no manifest |
| `--layout grid\|rows` | card layout; default `grid` |
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
| `show --reference NAME` | print one bundled file instead — `references/xopat-v3.md`, `examples/dysplasia_case.json` |
| `install` | place the skill in `~/.claude/skills` |
| `install --project` | `./.claude/skills` instead, this repository only |
| `install --dest DIR` | somewhere else entirely |
| `install --force` | replace a skill that is already there — check first whether anyone edited it |
| `install --link` | symlink, so a checkout edit is live at once |
| `where` | say where the skill is and whether an agent would find it |

### Manifest keys

Every key is checked strictly: one the library does not know stops the build and names
the nearest candidate. A key that silently did nothing is the failure this prevents —
`min_layer:` instead of `min_layers:` reads as a report that was filtered.
[manifests/example.yaml](manifests/example.yaml) is the four-key starting file;
[skills/reportfast/manifest.example.yaml](skills/reportfast/manifest.example.yaml)
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
| `sessions_from` | `module:function` — caller-supplied code returning the sessions, for cases the library cannot list |
| `params`, `preset` | session defaults for every case |
| `metrics`, `charts`, `blocks` | the components around the cards — `prose`, `heading`, `bullets`, `links`, `metrics`, `chart`, `section`, `raw_html` |
| `endpoint` | `base_url`, `wsi_base_url`, `image_protocol`, `mount_root` |
| `flow` | `tracking_uri`, `web_url`, `artifact_prefix` |
| `publish` | where `--publish` would log. **Setting it uploads nothing** |

Precedence, lowest to highest: **builtin defaults → `preset` /
`$XOPAT_SESSION_CONFIG` → a pasted config → manifest keys → flags**. A preset can
never carry `data`, `background` or `visualizations` — those are per-slide content
a default would silently rewire into the wrong indices.

## The Python API

Four entry points, all the same machinery the CLI calls. A manifest is `build_report()`
written down; a `Composition` is a manifest held in memory.
[manifest.py](report_fast/manifest.py) holds the manifest vocabulary and
[compose.py](report_fast/compose.py) the in-memory one.

**Paths only.** Backgrounds in, report out:

```python
from report_fast import build_report

build_report(
    ["/mnt/data/slides/case_001.tif", "/mnt/data/slides/case_002.tif"],
    masks=["/mnt/data/masks/tumor.tif"],          # over every slide
    title="QC",
    out="report.html",
)
```

**Named overlays, wherever they live.** A background, then the masks over it — from a
mounted folder or from the artifacts of an MLflow run. This is the shape of an analysis
job:

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

Each mask contributes one file per case, matched to its slide by stem (`case_001.svs`
and `case_001.tiff` are the same case), and becomes the DataID the tile server
addresses — nothing is read or downloaded. `MlflowRun` is the only part needing the
extra (`uv sync --extra mlflow`, see [Runs in MLflow](#runs-in-mlflow)).
`scripts/dysplasia_tile_masks.py` is that call as Python, run for real;
`manifests/dysplasia.yaml` is the same report as a YAML file. The two are kept in step
deliberately — the manifest is the form that can be diffed and corrected.

**An existing session.** For anything the library does not model:

```python
from report_fast import SlideCard, XopatSession

session = XopatSession.from_url("https://xopat…/v3/#%7B…%7D")   # a pasted link
session = XopatSession.from_file("my_session.json")             # a pasted file
session = XopatSession.from_config({"data": [...], "background": [...]})

SlideCard(session).to_html()
```

**One design, bound many times.** The agent path, and the one `--sessions-dir` exists
for: one hand-written session document with slots, N bindings, a composition, one file
out.

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

The design is written by hand. `load_design` is the gate that makes that safe: `data[]`
is positional, so the audit resolves every `dataReference` / `dataReferences` index and
refuses one that is out of range, which is the error a 300-case copy-paste eventually
produces.

Both routes end at the same `XopatSession`, so code written directly and code going
through this package emit the same links.

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

- **A config that arrives is a config that leaves.** Importing a session keeps fields
  this tool has never heard of, in the order they came. The paste path exists so that
  sessions the library does not model still round-trip; normalising away what is not
  modelled would defeat it. Only viewer navigation state (`params.viewport`,
  `activeBackgroundIndex`) is dropped, and `drop_state=False` keeps even that.
- **Indices are the library's job.** `data[]` is a positional pool and
  `background[].dataReference` / `shaders[].dataReferences` index into it. Appending a
  second session renumbers its references, so nothing built by hand can point at the
  wrong tile.

A session is **one viewer instance**, not one slide: it may carry several backgrounds
(timepoints, stains, channels) that the reader switches between. The term is *session*,
not *slide*.

```python
plan = XopatSession.from_slide("case/plan.nii", layers, name="Plan")
followup = XopatSession.from_slide("case/fu.nii", layers, name="Follow-up")
plan.merge(followup)                       # one session, two backgrounds
```

### Many slides at once

```python
from report_fast import SessionTemplate, sessions_from_folder

sessions = sessions_from_folder("/mnt/data/slides", masks=["/mnt/data/masks/x.tif"])

# Or reuse an existing session, refilling only its data:
template = SessionTemplate.from_config(json.loads(pasted), slots={0: "slide", 1: "mask"})
session = template.bind(name="case_001", slide="case_001.tif", mask="prob_001.tif")
```

`bind` replaces the `dataID` at each slot and keeps the protocol, tile options and
shader config written around it, so a hand-authored session becomes a form with the
paths as blanks.

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

The components included: `Prose`, `Heading`, `Bullets`, `LinkList`, `SlideCard`,
`SlideGrid`, `MetricTable`, `Chart`, `Section`, `Report`. They take sessions and plain
data — no database, no metric store, no slide reader. Collapsing is `<details>`; the
only interactivity is the viewer behind the link.

Those ten are also the **frozen set**, and that is a rule rather than a list: on the
authored path, page HTML comes from them and nowhere else, so one composition renders
the same page every time it is built. `RawHtml` exists — it is how a manifest keeps a
hand-written block — but it is refused **by code** on the agent path, including
`Prose(text="<div …>")` and nested specs. The gate is
[frozen.py](report_fast/frozen.py); `reportfast` refuses with the constructor's own
signature, because "wrong keyword" is not actionable without the right ones.

A custom component is a subclass with two methods:

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

Choose a CSS class prefix of your own: `.rf-note`, `.rf-card` and the rest are already
used by the components included here, and their stylesheets land on the same page.

## What a build leaves behind

Two files, and nothing else:

```
report.html                 the report
report.provenance.json      what it was built from
```

The sidecar records the endpoint the links were built against, the viewer version and
commit the schema was derived from, the tool version, what the build was given (a
folder, a manifest, a layout), every DataID the page links, and — when the sessions
came from a template — the **design as authored**, one document rather than 300
instantiations. It is generated from data that already resolved, never from the spec
that declared it, so a published report and its logged record cannot disagree about
which was resolved and which was intended.

It is a *sidecar*, and the trade-off is deliberate: **nothing is stamped into the
page** — no footer, no comment — and **a mailed HTML carries no provenance**. The page
looks identical whether or not anyone is keeping records (tested byte for byte). If a
report must be self-explaining wherever it goes, deliver the pair, or keep a manifest.
See [provenance.py](report_fast/provenance.py); both files are gitignored.

Neither file appears when no local copy was requested. `--publish` with no `-o` on a
manifest-less build uploads the page and its record to the run and leaves the working
directory alone: `--run` named the destination, so choosing a second one beside
whatever command happened to precede it is not this tool's decision. `-o` asks for
both.

## Runs in MLflow

`report_fast.mlflow` reads a run's artifacts and writes the report back into the run.
It is optional — `uv sync --extra mlflow`, imported lazily, everything else works
without it.

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

Nothing is downloaded. `list_artifacts` names the files and each becomes the DataID the
tile server resolves — `mflow/<experiment>/<run>/artifacts/<path>` — which is exactly
what `slides=` and a layer's `path` already take. Slides living on a mount rather than
in a run go in as usual, with `masks=flow.masks(run, "predictions")` placing that
run's overlays onto them by file stem.

`publish()` is the only call that writes. With `run_id=` it attaches the report to that
run; without one it creates a run in `experiment_name=`, named, described and owned as
the predecessor tool's storer did, and returns
`Published(run_id, artifact, url, created_run)`.

The web URL is a **separate setting** from the tracking URI (`web_url=`,
`MLFLOW_WEB_URL`): the API is usually reachable only inside the cluster, and the links
in a report have to open in a browser. The extra is capped below mlflow 3 because the
tracking server here speaks the 2.x API — a 3.x client calls endpoints that do not
exist and listing artifacts 404s.

`uv run python scripts/test_mlflow.py` builds that report from the live run and uploads
nothing unless `--publish <run_id>` is passed.

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

**The base path selects the viewer version.** This host serves v2 at `/xopat/` and v3
at `/v3/`; a v3 session opened by v2 *looks* like it loaded, so a wrong mount fails
quietly rather than returning 404.

**Which is why a 404 is a different problem.** A wrong mount shows a viewer that loaded
and an empty canvas; a server's own 404 page means the path never reached the viewer —
the mount is missing or the deployment is down. Those two read alike in a bug report and
have opposite fixes, so state which one was seen. A URL fragment is never sent to a
server either: the `#…` half of a card link cannot be what a server rejected, however
much it looks like the difference.

Coordinates can be passed per call instead with
`XopatEndpoint(base_url=…, wsi_base_url=…, image_protocol=…, mount_root=…)`.

Slide paths are shared state: the machine building the report and the tile server must
see the same file under the mount root, because a session names slides by path and
never by bytes.

### Defaults editable without changing Python

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

Precedence is **builtin → preset → pasted config → arguments**. A preset cannot carry
`data`, `background` or `visualizations`: those are the per-slide content a preset
would silently rewire into the wrong indices. Pass `preset=` for a single call.

`sessionName` is the viewer's persistence namespace, not a title — slides sharing one
also share their saved zoom and pan. Left unset, the viewer derives one per slide.

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
├── compose.py      the same composition in memory, and the --sessions-dir entry point
├── contract.py     the generated xOpat facts every gate reads (schema/, no copies)
├── audit.py        one session → findings with JSON paths; the severity split
├── frozen.py       the ten components the page may be made of, and the gate
├── provenance.py   report.provenance.json: the record, never the page
├── skill.py        the skill inside the wheel: find it, print it, place it
├── schema/         GENERATED from the pinned viewer: session schema, params
│                   allowlist, layer fields, and the version stamp (viewer.lock.json)
├── verify.py       every DataID → the tile server's /info, before a reader does
├── mlflow.py       runs: artifacts → DataIDs, report → run (needs the extra)
├── __main__.py     `reportfast plan | build | find | skill`, entry points and exit codes
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
                    demos writing the reports described under Tests
reports/            generated HTML, one per manifest plus the demos
                    (generated, regenerate freely)
```

`xopat.py`, `session.py` and `config.py` are stdlib-only: a script that only needs
links imports nothing heavy and reads no slides.

## Tests

```bash
uv run python tests/test_xopat.py         # wire format: session shape, URL round-trip
uv run python tests/test_session.py       # the base object: paste path, indices, presets
uv run python tests/test_masks.py         # backgrounds and masks, from a folder or a run
uv run python tests/test_components.py    # the shell and the components
uv run python tests/test_mlflow.py        # runs in/out, against a fake client
uv run python tests/test_manifest.py      # YAML: strict keys, the plan, the entry points
uv run python tests/test_verify.py        # the probe: DataIDs read back, three answers
uv run python tests/test_cli.py           # the commands, both entry points, each exit code
uv run python tests/test_contract.py      # the generated contract, and the skill's copy
uv run python tests/test_audit.py         # the gate: findings, paths, the severity split
uv run python tests/test_frozen.py        # the ten components, and what they refuse
uv run python tests/test_compose.py       # the in-memory composition and the entry point
uv run python tests/test_provenance.py    # the sidecar, and that the page is untouched
uv run python tests/test_skill.py         # the shipped skill: where it goes, what it claims
uv run python tests/test_examples.py      # the package docstring's examples, executed
uv run pytest tests
uv run ruff check .
```

`.github/workflows/ci.yml` runs the suite and the lint on the Python floor (3.10) and
on 3.12, installed with both extras so the YAML and MLflow paths are exercised rather
than skipped. It has no publish step and must not gain one — publishing is never
implied, and `test_no_workflow_publishes` fails the build if a workflow ever grows a
`--publish` or a tracking credential. The two checks that compare the derived contract
against a viewer checkout skip when `$XOPAT_VIEWER` is absent.

Every file is also a script — `python tests/test_cli.py` runs it with no pytest
installed. None of them contact a server: the run side is driven through a double and
the probe by stubbing the socket, so a green suite says nothing about whether the tile
server is up. No mount is needed and no slide is opened.

Two properties are pinned rather than assumed:

- **Reproducibility.** `build(x.yaml)` twice writes byte-identical HTML *and*
  byte-identical provenance — no timestamps, no generated code, fixed key order, and
  component ids derived from position rather than from a uuid. That is what allows a
  report to be a build artifact diffable against last week's, instead of a transcript
  nobody can check. What "same input" means since prompt mode: the page is reproducible
  by construction, and the sessions are inputs that vary — re-authoring a session may
  change a colour, and the HTML legitimately differs. Reproducibility is asserted over
  *(sessions + composition) → HTML*.
- **Publishing is never a side effect.** A manifest's `publish:` key is a destination,
  not an instruction; the tests assert that a build with a `publish:` and no `--publish`
  uploads nothing, and that `--publish` without a run id stops before it sends anything.

The suites pin the emitted session against what xOpat 3 actually parses — the `params`
allowlist, plural `dataReferences`, the palette/`threshold.breaks` coupling, fragment
round-trip — and against what it does *not* do: drop unknown params silently, ignore
inline-JS protocols, read a singular `dataReference`.

`scripts/test_report.py` and `scripts/test_mlflow.py` are demos, not tests — `testpaths`
keeps them out of `pytest`'s way. The first writes two reports into `reports/` against
the slide directories named at the top of the file, and falls back to paths that do not
exist where those mounts are absent — the report still builds, because a session is a
DataID string, not an opened file. The second reads a real run out of MLflow and uploads
nothing unless `--publish <run_id>` is passed.

`scripts/dysplasia_tile_masks.py` and `manifests/dysplasia.yaml` are the same thing as a
real job: the report the predecessor tool produced, rebuilt from its `mask_retrievers`
in order with the same colours and opacities — 32 cases, eleven overlays each, out of
four MLflow runs and two mounted folders. The manifest is the one to edit; `plan`
reports the real coverage on it today (the annotations layer reaches 21 of 32 cases,
which is a fact about the data rather than a defect).

`tests/test_mlflow.py` drives a double through `client=`, so the suite needs no mlflow
installed. That also means the real upload is not covered here: the first time
`publish()` is pointed at an unfamiliar server, send it to a store under control
(`tracking_uri="file:/tmp/store"`) and read the artifact back with `download()` — a 200
on `log_artifact` and a file the tile server can resolve are different claims.

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

Unknown params, retired layer types and dangling references raise `XopatError` (or
warn) at build time rather than failing in the browser.
