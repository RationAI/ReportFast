# Hydra v2 config → this library

Old configs are still what people bring. A config is **transcribed, not parsed**:
no YAML loader, no `_target_` instantiation. One retriever becomes one mask row,
keeping the order, the colours and the visibility, so a report re-run against a
redone mask run looks like the one it replaces.

Transcribe into a script, not into a config for this library — the script *is*
the record of a report, it names the folders, colours, order and layout, and
unlike a YAML file it can be re-run.

## Wire changes the transcription has to survive

| v2 | v3 |
| --- | --- |
| `…/redirect.php?visualization=<json>` | `…/v3/#<urlencoded json>` — `redirect.php` deleted upstream |
| shader `dataReference: 0` | `dataReferences: [0]`; the singular key is ignored, silently |
| `lossless: true` on a visualization | `{"dataID": "…", "options": {"format": "png"}}` on that layer's `data[]` entry |
| `params.toolBar` | `params.ui.toolBar`. Only `toolBar`/`statusBar`/`scaleBar` still work flat; `appBar`/`globalMenu`/`mainMenu`/`navigator` are stripped before the fallback can read them (`sanitizeAgainst`) |
| `shader_conf: {type, opacity, color}` | `{type, params: {…}, opacity}` — v2 sat the controls beside `type`, v3 nests them (`normalise_layer` accepts both spellings) |
| `classify` / `segmentation` / `bounding_box` | none of these exist in v3's registry; `classify` → `colormap`. See `xopat-v3.md` — an unregistered type takes every later layer with it |
| inline JS protocol template | the **name** of a registered `slide_protocols` entry |
| `RunIDMlflowMaskRetriever` / `DriveMasksRetriever` | `MlflowRun(run_id, path)` / `Drive(dir_name)` as a `Mask`'s source |

## Config key → library

| Hydra | Here | Note |
| --- | --- | --- |
| `reporter.background:` | `background:` (first arg of `case_matrix`) | `Drive(dir_name)` or `MlflowRun(run_id, dir_name)` |
| `mask_retrievers:` in order | one `Mask(...)` row each, in order | that order **is** the layer order in the viewer |
| `layer_name` | `name` (first arg of `Mask`) | |
| `dir_name` | the source's path | `run_id` present → `MlflowRun`, absent → `Drive` |
| `shader_conf.type: heatmap` | a heatmap row: `color`, `opacity` | |
| `shader_conf.type: classify` | `classes`, `palette`, `breaks`, `mask` | field map below |
| `shader_conf.visible: false` | nothing | already the default here |
| **no `shader_conf` at all** | `visible=True`, plus the colour you want | **trap — see below** |
| `selected_items:` | `only=` | the list is the report order; empty → omit, all cases |
| `min_layer_count:` | `min_layers=` | absent → omit, no filter |
| `metrics_run_ids:` | **no equivalent** | there is no metrics block on the page; see below |
| `reporter.title` | `Report(title=…)` | |
| `reporter.description` | `Report(preamble=…)` | one paragraph above the blocks |
| `reporter.static_end_text` | **no equivalent** | closing text goes in the report's own message, not the page |
| `reporter.save: RunIDMLFlowReportAttacher(run_id: …)` | `flow.publish(report, run_id=…)` | an explicit call, asked for; nothing implies it |
| `reporter.save.artifact_path` | `filename=` / `artifact_dir=` | a published report lands at `report/report.html` either way |
| `template_asset_path` | nothing | the shell is the library's |
| Hydra conf logging | `extra_dir=` on `publish()` | lands under `report/conf` |

**The bare-retriever trap.** A retriever with no `shader_conf` — in the worked
config, the `DriveMasksRetriever` row named `epithelium`, the only one of the 11
active rows that has no shader block — got no shader block at all in the original
tool and therefore drew **on**, in that tool's default colour. Here the same row
gets this library's defaults, `color="#fff705"` and `opacity=1.0`, but `visible`
defaults to `False`, so a faithful transcription has to say `visible=True` or the
layer the old report showed is invisible in the new one. That default is
deliberate — eleven layers all drawn on top of each other is not a picture — which
is exactly why the transcription has to be awake at this row rather than faithful.

Count the **active** rows, not the ones the file contains: this config carries 26
`_target_:` lines because a commented-out retriever is how an experiment is
parked. A commented row is not a layer.

**The join is the file stem.** `case_001.svs` and `case_001.tiff` are the same
case — the original tool's rule, inherited on purpose. A mask directory matched by
anything else silently drops layers. `case_matrix` reports what it paired as
`.coverage` and what fell out as `.dropped`, so the question "did that run's masks
land?" has an answer you can print instead of infer from a blank overlay.

## `classify` → `colormap`, field by field

| v2 `shader_conf` | v3 layer (`colormap_layer`) |
| --- | --- |
| `classes: N` | `classes=N`, and `palette` of length `N` |
| `color: [c1…cN]` | `palette`, one entry per class |
| `threshold.breaks` | `breaks`, `N - 1` values; defaults to even cuts |
| `threshold.mask` | `mask`, one 0/1 per class |
| `opacity` | `opacity` |
| `modifiable: true` | nothing — the emitted `threshold` is already an `advanced_slider` |

The coupling the viewer checks (`colormap_class_count`) is that the palette holds
one colour per class and `breaks` bin between them, so `len(palette) ==
len(breaks) + 1`. `colormap_layer` raises rather than emitting a layer the viewer
will reject. `[0, 1, 1]` leaves class 0 — usually the slide — unpainted.

## One row, both spellings

```yaml
# the config's mask_retrievers, verbatim
- _target_: report.masks.RunIDMlflowMaskRetriever
  run_id: 97084241311949189445f864d42e9d4e
  dir_name: tissue_masks
  layer_name: Tissue
  shader_conf:
    type: heatmap
    opacity: 0.5
    color: '#ffff00'
    visible: false
```

```python
# the same layer, same place in the order
Mask("Tissue", MlflowRun("97084241311949189445f864d42e9d4e", "tissue_masks"),
     color="#ffff00", opacity=0.5),
```

`visible: false` is gone because it is the default here. `${run_id}` is gone
because Hydra interpolation has no meaning in a Python script — resolve it to the
literal run id.

Which run is which is the one thing a config blurs. In the worked config,
`${run_id}` is `ad84f424e21742868a746c5fd8a9978b`, and it is where a *retriever's*
masks live; the run the finished report is published to is a different run
entirely, and nothing in a config can tell you which was meant. Read the
`_target_` before you reuse an id, and ask if the config interpolates one.
