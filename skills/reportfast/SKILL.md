---
name: reportfast
description: Build static HTML reports of xOpat v3 pathology sessions — you author the session, the library renders the page. Use when asked to make/update/publish a slide report, to attach mask overlays or QC metrics to slides, to check why a report's cards are black or a layer is missing, or to list what is inside an MLflow run's artifacts.
---

<!-- STAMP — generated, do not hand-edit. These facts were derived from
     xOpat 3.1.0, commit 18c94f2 (2026-09-14), from report_fast/schema/viewer.lock.json.
     Check it against the install you are using:
         python -c 'from report_fast.contract import viewer_stamp as v; print(v())'
     (uv run python … inside this repo). Refresh when the pinned viewer moves:
     uv run python scripts/derive_schema.py — in the repo, never from an install.
     If the two disagree, the lock is right and this comment is stale.
-->

# reportfast

A report is one HTML file of links into the xOpat v3 viewer. **You write the
sessions; the library writes the page.** Nothing else in this contract is as
important, and the two halves have different rules: a session is yours to author
and the gate checks it, while the page is made of ten frozen components and you
choose which ones, in what order, with what words.

The default flow leaves no files behind except the report and its provenance
sidecar. A YAML manifest is a thing you ask for, not the way this works — see
"When to keep a manifest".

```bash
reportfast plan  --design $D/design.json --slot 0=slide --slot 1=mask  # validate one design
reportfast build --sessions-dir $D/sessions --title "Cohort QC" -o $D/report.html
reportfast plan  reports/foo.yaml         # the manifest door, when there is one
reportfast build reports/foo.yaml         # same gates: plan, write, probe, sidecar
reportfast build … --publish --run RUN    # the only thing that uploads
reportfast find  <run-id> --path tile_masks   # what is under a run's artifacts/
```

No `reportfast` on PATH? `uv run reportfast …` inside the project, or
`scripts/{plan,build,find}.sh` in this bundle. Missing optional dependency:
`uv sync --extra manifest` (YAML) or `--extra mlflow` (runs).

Exit codes are the machine-readable answer: **0** fine, **1** the *spec* is wrong
— a bad manifest key, a missing case, or an authored session the viewer would not
load as written, **2** the HTML exists and some of its DataIDs will not open,
**3** an extra is not installed, **4** nothing to work on — no such manifest, no
such folder, no such run, bad flag. Read the code before you say it worked.

## Four questions, in this order

A report is a grid of cards. Each card is one **case**: a slide plus the overlays
that have a file named after it. Before writing anything you must answer all four.
Not approximately — every one is a value that goes into the session verbatim.

1. **Which cases?** Not "every file in the folder", if the folder holds 378 slides
   and the report is about 32. Get the stems from the person who asked, or from
   `ls` / `reportfast find`. A list you did not read is a report of the wrong cohort.
2. **Where is each background?** A mounted folder, or a run's artifacts
   (`reportfast find` gives you `mflow/<experiment>/<run>/artifacts/…` DataIDs).
   Confirm the folder exists or list the run before you use it. One wrong character
   is a report with zero cases, and a run id you invented reports nothing at all.
3. **Where is each overlay, and what colour is it?** Colour and opacity are a
   decision, not a fact: ask, or copy the previous report for this dataset and say
   you did.
4. **Who reads it, and what should they notice?** This decides the title, the prose
   blocks above the grid, and whether a case with 2 of 11 overlays is dropped or
   shown and worried about. If the answer is "nobody, it is a debug dump", write a
   debug dump and say so — do not dress it up as a report.

Then: validate the design, bind, `build`, read the probe, show a human.

## The authoring loop

One design, one temp dir, N bindings. The design is **hand-written by you** — read
`../report_fast/schema/session.schema.json` and imitate `examples/`, then type the
JSON. The bindings are a Python loop. Never the other way round: a script that
generates 300 session files invents a design nobody read, and a generated design is
the thing the whole review was against.

```bash
D=$(mktemp -d); mkdir -p $D/sessions   # everything below lives here and dies here
$EDITOR $D/design.json                 # one session document, slots at data[0..]
reportfast plan --design $D/design.json --slot 0=slide --slot 1=mask
```

`plan --design` is the gate: it reads the document once, strictly, and prints the
slots, the shape and the endpoint — plus the Python to paste. It binds nothing, on
purpose: the loop is Python, and a flag that could express it would need a case-list
file, which is an intermediate the default flow was ruled not to leave behind.

```python
import json
from pathlib import Path
from report_fast import load_design, XopatEndpoint

endpoint = XopatEndpoint(base_url=…, wsi_base_url=…, image_protocol=…, mount_root=…)
design = load_design(
    f"{D}/design.json", slots={0: "slide", 1: "mask"}, endpoint=endpoint
)   # audited here, once; every bind below inherits that verdict

sessions = []
for case in cases:                                  # the 300, from the listing
    session = design.bind(
        slide=case.slide, mask=case.mask, name=case.name,
    )
    Path(f"{D}/sessions/{case.name}.json").write_text(
        json.dumps(session.to_config(), indent=1)
    )
```

```bash
reportfast build --sessions-dir $D/sessions --title "Cohort QC" -o $D/report.html
```

`build` re-reads every file through the same gate, prints the plan, writes one HTML
file plus `report.provenance.json`, and probes every DataID. `--layout rows` (or
`--no-grid`) puts one card per row instead of a grid. `--check-only` builds and
probes and writes nothing at all — not the page, not the sidecar.

The loop's slots are the only thing that varies per case. `bind()` swaps the DataID
at that `data[]` index and keeps everything the entry carried — protocol, `options`,
pixel size — which is why the design keeps `options: {format: png}` on the mask
entry and the loop does not mention it again.

## The hard rules

**The gate gives you errors, not warnings.** An authored session is refused for
anything the viewer would drop — a `params` key outside the allowlist, a layer param
it never declared, a `data[]` index that is not there — named by file and by JSON
path: `zz-07.json: visualizations[0].shaders.dose.params.threshhold`. That is
exit **1**, and it is the whole difference from pasting a colleague's config, which
keeps its bytes and warns. Do not fix it by making the key valid-looking; the viewer
reads `params.ui.toolBar`, so `params.toolBar` is dead JSON even though three keys
still work flat. Edit the file, run `plan` again.

**Author one design; bind it in a loop.** `data[]` is positional, so a
copy-paste-edit of 300 files eventually has an overlay bound to the wrong slide, and
the viewer renders that as a picture that looks like a picture. `load_design` audits
the document once and `bind()` keeps the verdict for all 300 — which is also why you
do not re-audit per case, and why a design that gained a background needs your
`slots=` to follow it.

**Never generate the design.** A script that writes `design.json` from a folder
listing has to invent layer names, colours, opacity, which class map is png — and
every one of those is a decision a person should have made. Listing cases is fine
and expected; authoring sessions by generator is not.

**Compose only from the frozen set.** `SlideCard`, `SlideGrid`, `Prose`, `Heading`,
`Bullets`, `LinkList`, `MetricTable`, `Chart`, `Section`, `Report`. Nothing else
reaches the page, and `raw_html` / inline HTML is refused **by code** on your path:
`Prose(text="<div class=…>")` is not a way around it, and neither is a nested spec.
This is what makes the same report look identical every time you build it; the
components are frozen so the page cannot drift, and if the page you need is not
expressible in ten components that is a conversation with a human, not a workaround.

**Nothing is persisted unless the user asks.** Temp dir for the design and the
bound sessions, HTML + sidecar out. When they do ask, keep the *design* (one
document) and — if the report is a standing artifact — a manifest; then **edit**
that file, never regenerate it. Regenerating kills the hand edits (the intro someone
rewrote, the colours chosen after the first read) and your version builds fine.

**Discover, never guess.** Run ids and paths come from a listing or from the user. A
hallucinated run id does not raise: the layer appears on no cases and the report
still looks finished. `reportfast find <run> --path <dir>` lists a run; `plan`
reports coverage per layer. Never invent a run id, a path, a colour, a case name, or
a metric.

**`plan` before `build`, and show the plan.** The plan is the review artifact:
cases, per-layer coverage, files per source, what was dropped, every warning. If you
did not show it, you did not check it. `--json` for the numbers.

**Probe before claiming.** A report whose DataIDs were not resolved is a report
nobody has checked: the viewer loads, the card is black, nothing reports an error.
`build` probes by default and exits **2** on a refusal. Do not use `--no-check` to
get a green run — if the tile server is unreachable from where you are, the probe
says so, that is exit 0, and you say "built, links unverified from here" instead of
"works".

**`--publish` is asked for, out loud.** Never implied: not from a `publish:` key, not
from CI, not because the user "obviously wants it up there". From the manifest-less
door it needs `--run RUN`, and the CLI prints `publish -> run <id>` before uploading —
that line is what the run's record needs. A publish uploads the page plus
`provenance.json`; with no manifest, that record *is* the configuration the run keeps.

## Reading a build that is not what you wanted

| It says | It means |
|---|---|
| `X.json: params.threshhold … the viewer will drop it` | Typo or v2 key. Fix the file; the nested/new spelling is in the message. |
| `X.json: visualizations[0].shaders.d.p.dataReferences [3] is out of range` | The design references a `data[]` entry that is not there — a slot list that did not follow the design. |
| `case 217 (slide=…, mask=…) could not be bound` | One case out of 300. The values are printed so you do not search the list. |
| `--sessions-dir … is not a directory` / `holds no *.json` | Exit 4. Nothing was read; go make the folder, do not go editing files. |
| `--design validates a design and binds nothing` | You asked `build` to do `plan`'s job. Plan, loop, then build. |
| `this door names components as they are, so write 'SlideGrid'` | Snake_case block names are the manifest's vocabulary, not this one. |
| `RawHtml is not on the frozen set` | Correct, and not negotiable from here. Ask a human, or say what you wanted and could not say. |
| `No case reached N of M masks` | The background folder is empty here, or no case got enough overlays. |
| `Not one of these cases has a file for: X` | X's source is a mistyped run id / artifact dir. A mask nobody gets is never an empty layer. |
| `layers not on every case: Tissue 3/32` | Real coverage gap. Decide: is that a finding, or a wrong source? |
| `probe 0/N DataIDs open; 0 refused, N unreachable from here` | You cannot reach the tile server. Not a broken report, and not verified. Say which. |
| `probe 41/60 … 19 refused` | Exit 2. Usually a wrong `mount_root`, a wrong `image_protocol`, or a run that no longer has the files. |

Exit 1 is the spec — file or manifest. Exit 2 is the addresses it produced. Exit 3
is `uv sync`. Exit 4 is that the thing you pointed at is not there.

## When the report is a standing artifact

Ask, then keep a manifest: `reportfast build --sessions-dir … --emit-manifest`
prints a manifest that would reproduce the page (prints only — the tool never
chooses to keep one). Write it to `reports/foo.yaml`, commit it, and from then on
`reportfast build reports/foo.yaml` is the reproducible command, with `background:` /
`masks:` / `only:` / `min_layers:` resolving sources for you.

A manifest is worth it when the report is re-run, reviewed, or owned by someone
else. It is not worth it for a one-off answer to a question, and a manifest nobody
edits for a year is a second source of truth going stale.

Manifests keep one door this path does not: `sessions:` with `from_config:` /
`from_file:` / `from_url:` pastes a whole exported session verbatim (warnings, not
errors), and `sessions_from: pkg.module:fn` lets your own function return sessions.
That asymmetry is deliberate — a paste is a human handing over bytes, an authored
session is yours to fix.

## References

Only what is not derivable from the code:

- `references/xopat-v3.md` — the fragment is the payload, `data[]` is positional,
  `sessionName` is a cache key not a title, which flat `params.ui` keys survive,
  what hard-fails vs. degrades.
- `references/deployment.md` — `/v3/` vs `/xopat/`, the tile server's own mount,
  how `/mnt/…` becomes a DataID, the `mflow/<experiment>/<run>/artifacts/…` prefix.
- `references/hydra-v2-to-v3.md` — mapping an old Hydra config's
  `mask_retrievers` / `selected_items` / `min_layer_count` onto a report.
- **the generated contract** — four files: the session schema, the `params`
  allowlist, every layer field, the viewer stamp. Find them from your install with
  `python -c "from report_fast.contract import SCHEMA_DIR; print(SCHEMA_DIR)"`.
  Read them before authoring a design; do not trust prose — including this file —
  for a key name. `report_fast.contract` answers the same facts in Python
  (`accepted_param_keys()`, `shader_types()`, `layer_field_names()`, `viewer_stamp()`)
  — the version that cannot drift from the gate that uses it.
- **golden sessions to imitate** — including the multi-background case. From an
  install: `reportfast skill show --reference examples/dysplasia_case.json`; from a
  checkout, the same files are in `examples/`.
- `manifest.example.yaml` — every manifest block type, with comments.
