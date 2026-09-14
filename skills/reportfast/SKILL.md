---
name: reportfast
description: Build and publish static HTML reports over xOpat v3 pathology sessions from a YAML manifest. Use when asked to make/update/publish a slide report, to attach mask overlays or QC metrics to slides, to check why a report's cards are black or a layer is missing, or to list what is inside an MLflow run's artifacts.
---

# reportfast

A report is one HTML file of links into the xOpat v3 viewer. You produce it from
a YAML manifest with three commands, and the whole job is to get the manifest to
describe data that exists.

```bash
reportfast plan  reports/foo.yaml          # writes nothing. Always first.
reportfast build reports/foo.yaml          # one HTML file, then probes every link
reportfast build reports/foo.yaml --publish   # the only thing that uploads
reportfast find  <run-id> --path tile_masks   # what is under a run's artifacts/
```

No `reportfast` on PATH? `uv run reportfast …` inside the project, or
`scripts/{plan,build,find}.sh` in this bundle. Missing optional dependency:
`uv sync --extra manifest` (YAML) or `--extra mlflow` (runs).

Exit codes are the machine-readable answer: **0** fine, **1** the manifest is
wrong, **2** the HTML exists and some of its DataIDs will not open, **3** an
extra is not installed, **4** nothing to work on — no such manifest, no such run,
no command, bad flag. Read the code before you say it worked.

## Four questions, in this order

A report is a grid of cards. Each card is one **case**: a slide plus the overlays
that have a file named after it. Before writing a manifest you must be able to
answer all four. Not approximately — every one of them is a value that goes into
the file verbatim.

1. **Which cases?** Not "every file in the folder", if the folder holds 378
   slides and the report is about 32. Get the stems from the person who asked, or
   from `ls` / `reportfast find`. They go in `only:`.
2. **Where is each background?** A mounted folder (`drive:`), or a run's artifacts
   (`run:` + `path:`). Confirm the folder exists, or list the run, before writing
   it. A path with one wrong character is a report with zero cases, which `plan`
   catches — after you have already been confident.
3. **Where is each overlay, and what colour is it?** Same two source kinds, per
   mask row. Colour and opacity are a decision, not a fact: ask, or copy the
   previous report for this dataset and say you did.
4. **Who reads it, and what should they notice?** This decides `title:`, the
   `intro:` paragraph, `blocks:`, and whether a case with 2 of 11 overlays should
   be dropped (`min_layers:`) or shown and worried about. If the answer is
   "nobody, it is a debug dump", write a debug dump and say so — do not dress it
   up as a report.

Then: `plan`, read the numbers, `build`, read the probe, show a human.

## The hard rules

**Discover, never guess.** Run ids and paths come from a listing or from the
user. A hallucinated run id does not raise: the layer appears on no cases and the
report still looks finished. `reportfast find <run> --path <dir>` lists a run;
`plan` reports coverage per layer and refuses a layer that lands on nothing.
Never invent a run id, a path, a colour, a case name, or a metric.

**`plan` before `build`, and show the plan.** The plan is the review artifact:
cases, per-layer coverage, files per source, what `min_layers:` dropped, every
warning. If you did not show it, you did not check it. `--json` for the numbers.

**Probe before claiming.** A report whose DataIDs were not resolved is a report
nobody has checked: the viewer loads, the card is black, and nothing anywhere
reports an error. `build` probes by default and exits **2** on a refusal. Do not
use `--no-check` to get a green run — if the tile server is unreachable from
where you are, the probe says so, that is exit 0, and you say "built, links
unverified from here" instead of "works".

**Edit a manifest, never regenerate one.** To add the blur masks you open the
file and add rows. Regenerating from a template kills the hand edits — the
`intro:` someone rewrote, the case list, the colours chosen after the first read —
and you will not notice, because your version builds fine.

**`--publish` is asked for, out loud.** Never imply it: not from a `publish:` key
in the manifest, not from CI, not because the user "obviously wants it up there".
When asked, repeat the run id back — the CLI prints `publish -> run <id>` before
uploading, and that line is what the run's record needs. Publishing writes to
someone's run.

**Never write session JSON by hand.** If the manifest cannot say it, use a door
(below). If no door can, write Python against the library and show a human. The
reason is in `references/xopat-v3.md`: `data[]` is positional, so a hand-typed
session whose indices are one off gives you overlays bound to the wrong file, and
the viewer renders that as a picture that looks like a picture.

## Reading a plan that is not what you wanted

| The plan says | It means |
|---|---|
| `No case reached N of M masks` | The background folder is empty here, or no case got enough overlays. |
| `1 of the cases are not in the background: X` | An `only:` stem no file answers to — a typo, or the file is not where you think. |
| `Not one of these cases has a file for: X` | X's source is a mistyped run id / artifact dir. A mask nobody gets is never an empty layer. |
| `X is not on this machine` | A `drive:` path that does not exist here. Different fix: mount it, or it is a run source you wrote as a folder. |
| `layers not on every case: Tissue 3/32` | Real coverage gap. Decide: is that a finding, or a wrong source? |
| `dropped by min_layers: case_x (2)` | `min_layers:` is doing its job; check the dropped case is a retrieval failure, not a case someone needs. |
| `unknown key 'min_layer' … did you mean 'min_layers'` | Fix that key. Everything else in that file may be fine; only this line is wrong. |
| `has no files under 'artifacts/x'. Only subdirectories: …` | The run stores classes in subdirectories. One mask row per subdirectory; the parent lists nothing. |
| `probe 0/N DataIDs open; 0 refused, N unreachable from here` | You cannot reach the tile server. Not a broken report, and not verified. Say which. |
| `probe 41/60 … 19 refused` | Exit 2. Those DataIDs are wrong — usually a wrong `mount_root`, a wrong `image_protocol`, or a run that no longer has the files. |

Exit 1 is always in the manifest. Exit 2 is in the addresses it produced. Exit 3
is `uv sync`. Exit 4 is that the thing you pointed at is not there.

## When the manifest has no key for it

Three doors, in order of how much you should prefer them:

1. **`params:` on a mask row** — v3 shader fields the library does not model.
   Passed through verbatim. Prefer this over forking anything; if the field works,
   it is a candidate for a real key.
2. **`sessions:` with `from_config:` / `from_file:` / `from_url:`** — a whole
   exported session, kept byte-for-byte including fields nobody has seen.
   Alternative to `background:`, not an addition: a manifest with both is
   rejected, because one of the two would silently never render.
3. **`sessions_from: pkg.module:fn`** — your own function returning sessions, for
   cases no source can list.

If all three fail: `report_fast` is importable, the library API underneath is
small (`build_report(background=…, masks=[Mask(…)], …)`), and writing Python is
the correct answer. `scripts/dysplasia_tile_masks.py` in the repo is that
approach, working, and is what `manifests/dysplasia.yaml` was translated from.

## References

Only what is not derivable from the code:

- `references/xopat-v3.md` — the fragment is the payload, `data[]` is positional,
  `sessionName` is a cache key not a title, what hard-fails vs. degrades.
- `references/deployment.md` — `/v3/` vs `/xopat/`, the tile server's own mount,
  how `/mnt/…` becomes a DataID, the `mflow/<experiment>/<run>/artifacts/…` prefix.
- `references/hydra-v2-to-v3.md` — mapping an old Hydra config's
  `mask_retrievers` / `selected_items` / `min_layer_count` onto a manifest.
- `manifest.example.yaml` — every block type, with comments.
- `scripts/plan.sh`, `scripts/build.sh`, `scripts/find.sh` — thin wrappers.
