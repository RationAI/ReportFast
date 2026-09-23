---
name: reportfast
description: Build static HTML reports of xOpat v3 pathology sessions — you write the script, the library builds the sessions and renders the page. Use when asked to make or update a slide report, to attach mask overlays to slides, to check why a report's cards are black or a layer is missing, or to list what is inside an MLflow run's artifacts.
---

# reportfast

A report is one HTML file of links into the xOpat v3 viewer. **You write a Python
script; the library builds the sessions and renders the page.** There is no build
command, and nothing to install: `reportfast` has one action, and it prints.

```bash
reportfast skill show                                        # print this file
reportfast skill show --reference references/xopat-v3.md     # one reference
reportfast skill show --reference examples/dysplasia_case.json
```

You are reading this in a checkout, so the references are files: open
`skills/reportfast/references/…` directly, which is faster than the CLI. `skill
show` is for someone with `uv add report-fast` and no checkout — the skill travels
inside the wheel. Use `uvx report-fast skill show`, or `uv run reportfast skill
show`, when `reportfast` is not on `PATH`.

## Using an agent that is not Claude Code

Nothing here is specific to a vendor: this file is markdown, the library is plain
Python, and any agent that can read a file and run a shell follows the same
procedure. To wire one up, create the context file that agent reads at startup —
`AGENTS.md` and `QWEN.md` are common names; check what yours loads — and put in it
a pointer, not a copy:

> Before writing any report script, run `uvx report-fast skill show` and follow
> it, including the references it names.

Do not paste this file's text into that pointer file. The skill is versioned with
the library and gains corrections as deployments break in new ways; a pasted copy
is a snapshot that starts lying the day the package updates, and every failure it
then causes looks like this tool's fault. The pointer cannot go stale because it
re-reads at the moment of use.

The script is the report's record. It is not written into the page, not logged
beside it, and not stored anywhere by the library — so ask where it should live.
"Somewhere in chat" is a fine answer for a one-off. For a report that gets re-run,
write it to a file in the project and say that you did.

## Where the xOpat knowledge lives

**Not in this library, and not in this file.** The library passes session fields
through as written, so its vocabulary is never the viewer's ceiling; the viewer
changes, and a copy of its feature list here would go stale and then reject
sessions the deployed viewer renders fine. When you need to know what a session
can contain, read the viewer:

- `https://xopat.org/generated/configuration/viewer-configuration` — the viewer's
  own configuration reference, generated from its source.
- `https://github.com/RationAI/xopat` — `src/types/app.d.ts` is the session
  document (`src/types/session.d.ts` is **not** — that one is live collaboration,
  and it is named to mislead). `src/parse-input.js` is what a link goes through.
  `src/libs/flex-renderer/flex-renderer.js` is where layer types are registered —
  over the network that path is `master` or a pinned SHA, never `main`, and the
  recipe is in `references/xopat-v3.md`.
- `https://xopat.org/api/` is marked "Coming Soon". It is not a source.

**Version stamp: this library was written against xOpat 3.1.0, commit
`18c94f2`.** That is a handwritten line, not a generated one — there is nothing
here to regenerate it from, and that is deliberate. So before trusting a field
name for a report someone will read: establish which viewer version the
*deployment* in front of you runs. A deployed viewer can lag or lead upstream, and
the library cannot tell you which, because it does not run the viewer either.

## Four questions, in this order

A report is a grid of cards; each card is one case, which is one slide plus the
overlays that have a file named after it. Every one of these is a value that goes
into the session verbatim, so answer them before writing, not approximately.

1. **Which cases?** Not "every file in the folder", if the folder holds 378 slides
   and the report is about 32. Get the stems from the person who asked, or from a
   listing. A list you did not read is a report of the wrong cohort.
2. **Where is each background?** A mounted folder, or a run's artifacts. Confirm
   the folder exists, or list the run, before using it. One wrong character is a
   report with zero cases.
3. **Where is each overlay, and how is it drawn?** Colour, opacity, and whether
   the file is a scalar heatmap or a class map are decisions, not facts: ask, or
   copy the previous report for this dataset and say you did.
4. **Who reads it, and what should they notice?** That decides the title, the
   preamble, and whether a case with 2 of 11 overlays is dropped or shown and
   worried about. If the answer is "nobody, it is a debug dump", write a debug
   dump and say so.

## The script

The whole shape, for a folder of slides:

```python
from pathlib import Path

from report_fast import Report, SlideGrid, XopatSession

slides = Path("/mnt/slides")
sessions = [
    XopatSession.from_slide(path, name=path.stem)
    for path in sorted(slides.glob("*.tif"))
]

report = Report(
    title="Cohort QC",
    subtitle=f"{len(sessions)} slides",
    preamble="One card per slide. Overlays are the model's, not a pathologist's.",
)
report.add(SlideGrid(sessions=sessions))
report.write("report.html")
```

Nothing is written unless you name the file. `write()` returns the path;
`to_html()` returns the string and touches no disk.

Masks over slides, matched by filename stem (`case_001.svs` and `case_001.tiff`
are one case). This is where `sessions_from_masks` earns its place instead of a
loop you write:

```python
from report_fast import Drive, Mask, MlflowRun, Report, SlideGrid
from report_fast import case_matrix

matrix = case_matrix(
    Drive("/mnt/data/colon/dysplasia"),
    [
        Mask("Tissue", Drive("/mnt/data/tissue_masks"), color="#ffff00", opacity=0.5),
        Mask("Grades", MlflowRun("41d5e1d7d43641ea8f645f9b7945e9f7", "annot_masks"),
             classes=3, palette=["#ffffff", "#ff0000", "#00ff00"]),
    ],
    only=["1094_18_HE_0", "8625_13_HE_A"],  # the cohort, in report order
    min_layers=2,
)

Report(
    title="Dysplasia QC",
    subtitle=f"{len(matrix.sessions)} cases, {len(matrix.dropped)} filtered out",
).add(SlideGrid(sessions=matrix.sessions)).write("report.html")

# The numbers you report back, because a filtered report has to say so:
print(matrix.coverage)   # {mask name: cases that got a file from it}
print(matrix.dropped)    # {case: layer count} for cases under min_layers
```

`sessions_from_masks` is the same call returning only `.sessions`, for when you do
not need the counts. Ask for `case_matrix` whenever anything was filtered — a
layer that reached 3 of 32 cases is either a finding or a wrong source, and
`.coverage` is how you know which to say.

An authored session — written by hand, or handed over as a file, or pasted from a
viewer — comes back through one door:

```python
from report_fast import SlideCard, XopatSession

session = XopatSession.from_config(open("case.json").read())
print(session.url())          # the link; the session is in the #fragment
session.to_config()           # the document, unchanged
card = SlideCard(session)     # and the page's one affordance for it
```

## What the page can contain

`Report` takes `title`, `subtitle`, `preamble`, `blocks`, `theme`, `css`. There
are **two components**: `SlideCard` (one session) and `SlideGrid` (any number),
plus `Section`, which groups blocks under a collapsible heading. `subtitle` and
`preamble` are the whole prose budget of a page — one line under the title and one
paragraph above the blocks.

**What a card shows the reader.** The label under a card is `name`, and one line
under that is `note` — that is the whole labelling surface, and both are per card:

```python
from report_fast import SlideGrid

grid = SlideGrid(title="Grading")
for case, grade in cases:                      # [("case_001", "low grade"), …]
    grid.add(f"/mnt/data/colon/{case}.svs", name=case, note=grade)
```

`name` overrides the session's own; unset, the card shows `background[0].name`,
which for a built session is the file stem. `SlideGrid(card={…})` sets the same
keys for *every* card, so a per-case label goes through `add()` (or build the
`SlideCard`s yourself and pass them as blocks) — `card={"name": …}` gives all 32
cards one label, which is rarely what was meant. There is no table, no legend and
no per-card metadata block: a label is a string you already have in the script.

There used to be ten components, including `Prose`, `MetricTable`, `Chart` and a
`RawHtml` escape hatch. They were deleted on purpose, and the reason is the reason
they existed: reports written a month apart have to look like the same product,
and every component someone can reach for is a way for the two to drift. If the
page you need cannot be said in a grid of cards plus a preamble, **that is a
conversation with a human, not a workaround.** The supported way to extend a page
is to subclass `BaseComponent`, give it a CSS class prefix that is not `rf-`, and
`report.add()` it — that is a component you wrote deliberately, which is a
different thing from a block that accepts arbitrary markup.

`Report.add()` also takes a raw FastHTML tree, so you can build a block out of
fasthtml directly. That is the extension path, not a loophole around the above:
the point is that the *shipped* set stays two.

## The three ways a report is silently wrong

Everything else degrades loudly enough to notice. These three do not, and the first
two are why the library exists rather than being a `json.dumps` in your script.

**An artifact prefix the tile server does not serve.** `artifact_data_id()` writes
`mflow/…` by default; the namespace is a deployment fact, and a deployment whose
WSI-Service registers `public_mlflow` needs `Mlflow(artifact_prefix="public_mlflow")`
or `REPORTFAST_MLFLOW_ARTIFACT_PREFIX`. Get it wrong and *everything upstream is
fine*: the DataIDs are well-formed, the artifacts exist in MLflow with the right
bytes, the report builds clean and looks complete — and every overlay is missing,
because the tile server answers "Slide … does not exist". Nothing in this library can
see it, because nothing here asks the tile server. So a report whose overlays are all
absent while its backgrounds show is **that** bug until disproved, and the disproof is
one probe of a single artifact DataID. Do not start by suspecting the mask generation.

**An invented `params` key.** `sanitizeAgainst` in the viewer's `app.ts` drops
every session param outside its allowlist *without a word*. The layer then
renders its defaults: a plausible image of the wrong thing, with nothing
reported. So a `params` key you have not seen read in viewer source is a key you
have to verify, not one you can hope at. The library will not catch it for you —
by design, it keeps every key you give it.

**A stale `data[]` index.** `data[]` is a positional pool and every reference in
the document is an index into it, so reordering the pool silently rewires the
overlays. Never type an index: let `add_data()` return it. The library does check
that each index points somewhere inside the pool it indexes — that is arithmetic
on the document, not knowledge of the viewer — and raises naming the JSON path.
What it cannot catch is an index that is *in range* and wrong, which is what a
copy-paste-edit of 300 files eventually produces. Bind one session in a loop
rather than copy-pasting 300.

## Reading a slide you cannot see

The builder never opens a slide. A session names files by path, and the machine
building the report and the tile server must see the same file under the same
root — nothing checks this, and the failure surfaces when someone opens the page.

So probe, then say what you probed. Every distinct DataID, once, against the
viewer's own first request — **batched, and bounded**:

```bash
printf '%s\n' "$DATA_IDS" | xargs -P 8 -I{} -n1 sh -c \
  'printf "%s %s\n" "$(curl -s --max-time 5 -o /dev/null -w "%{http_code}" \
  "https://xopat.rationai.cloud.trusted.e-infra.cz/wsi-service/v3/slides/info?slide_id={}")" "{}"' \
  | sort
```

`--max-time` is not a style choice. An unreachable host does not answer, and
`curl` waits its full default — **60 seconds per DataID, one at a time** — so a
32-card report on a pod that cannot reach the tile server spends half an hour
probing, then prints `000` thirty-two times. Measured on this pod: four DataIDs,
240s unbounded and sequential, 5s bounded and parallel. Batch it or you will be
tempted to skip the probe, and skipping it is the failure this page exists to
prevent.

Three outcomes, and they are not the same sentence:

- **200** — that address resolves. That is an address, not a picture: it does not
  prove the file is the right one, aligned with its slide, or carrying the class
  values the layer claims.
- **4xx** — wrong DataID, wrong `mount_root`, or a run that no longer holds the
  file. Fix it before saying anything about the report.
- **`000`, no response** — you cannot reach the tile server from where you are
  standing. This is common and it is not a broken report. Say *"built, links
  unverified from here"* — never "works", and never "broken".

**The probe is one pass over the DataIDs, not a check inside the build.** The library
never opens a slide and never contacts a server while building — it lists a run's
artifacts once, then formats strings. A report that takes tens of minutes is spending
them outside the library: per-card probing, per-card regeneration, or retrying a curl
that was never going to answer. If the probe says `000`, stop probing and say so;
repeating an unreachable request per card turns one unanswered question into forty
minutes of them. Skipping the probe entirely is legitimate — what is not legitimate is
reporting success without it.

## MLflow

Read `references/mlflow.md` before touching a run. Four things matter immediately:

- There are **two tracking servers** (an old 2.16 one and an s3-backed 3.16 one),
  each with its own runs and its own artifact store; the client reads both either
  version, and the one break is a 3.x client *publishing* to the old server (a loud
  404). Ask which server a run lives on before listing it — a run id that 404s on
  one proves nothing about the other, and its artifact prefix travels with the
  server. Addresses, the measured compatibility matrix, and what each env var
  selects: `references/mlflow.md`.
- Artifacts are **addressed, never downloaded**. A run's artifact becomes the
  DataID `mflow/<experiment_id>/<run_id>/artifacts/<path>`, and `mflow` is a
  namespace the tile server resolves — not a directory on your machine. If the
  deployment registers a different one, that name goes in `Mlflow(artifact_prefix=…)`
  (see *The three ways a report is silently wrong*).
- **`publish()` is asked for, out loud.** It is the only name in this library that
  uploads. Nothing implies it: not a key, not CI, not "they obviously want it up
  there". Publishing to a run writes to someone's record.

Credentials: never paste a token or a password into chat, and when asked what is
configured, list environment variable *names*, never their values.

## References

- `references/xopat-v3.md` — what makes a session load or quietly not load: the
  fragment is the payload, `data[]` is positional, `sessionName` is a cache key
  and not a title, which `params.ui` spellings survive, and the black-card
  checklist.
- `references/deployment.md` — this cluster's addresses, how a path becomes a
  DataID, the artifact prefix, every environment variable.
- `references/mlflow.md` — the two tracking servers and how to tell which one a
  run lives on, listing a run, publish as an explicit act.
- `references/hydra-v2-to-v3.md` — mapping the old tool's Hydra config onto this
  library, key by key.
- **Golden sessions to imitate** — `reportfast skill show --reference
  examples/dysplasia_case.json`, or `examples/multi_background_case.json`, or
  `examples/viewer_export.json` (a real viewer export, warts included). Read one
  before authoring; do not trust prose — including this file — for a key name.
