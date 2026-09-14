# Hydra v2 config → this library

Old configs are still what people bring. A config is **transcribed, not
parsed**: no YAML loader, no `_target_` instantiation — one retriever becomes one
mask row, keeping the order, the colours and the visibility, so a report re-run
against a redone mask run looks like the one it replaces.
`scripts/dysplasia_tile_masks.py` is the worked transcription of
`/home/jovyan/report/report/conf/dysplasia_tile_masks.yaml`.

## Wire changes the transcription has to survive

| v2 | v3 |
| --- | --- |
| `…/redirect.php?visualization=<json>` | `…/v3/#<urlencoded json>` — `redirect.php` deleted upstream |
| shader `dataReference: 0` | `dataReferences: [0]`; the singular key is ignored, silently |
| `lossless: true` on a visualization | `{"dataID": "…", "options": {"format": "png"}}` on that layer's `data[]` entry |
| `params.toolBar` | `params.ui.toolBar`; the flat spelling is a deprecated alias |
| `shader_conf: {type, opacity, color}` | `{type, params: {…}, opacity}` — v2 sat the controls beside `type`, v3 nests them (`xopat.py:323`) |
| `classify` / `segmentation` / `bounding_box` | `colormap` / `colormap` / `iconmap` (`RETIRED_SHADER_TYPES`, `shader.py:186-190`) |
| inline JS protocol template | the **name** of a registered `slide_protocols` entry |
| `RunIDMlflowMaskRetriever` / `DriveMasksRetriever` | `MlflowRun(run_id, path)` / `Drive(dir_name)` as a `Mask`'s source |

## Config key → library

| Hydra | Here | Note |
| --- | --- | --- |
| `reporter.background:` | `background:` | `Drive(dir_name)` or `MlflowRun(run_id, dir_name)` |
| `mask_retrievers:` in order | one mask row each, in order | that order **is** the layer order in the viewer |
| `layer_name` | `name` | |
| `dir_name` | the source's path | `run_id` present → `MlflowRun`, absent → `Drive` |
| `shader_conf.type: heatmap` | a heatmap row: `color`, `opacity` | |
| `shader_conf.type: classify` | `classes`, `palette`, `breaks`, `mask` | see the field map below |
| `shader_conf.visible: false` | `visible: false` | already the default here, so it may be left out |
| **no `shader_conf` at all** | `color="#fff705"`, `opacity=1.0` | **trap — see below** |
| `selected_items:` | `only:` | the list is the report order; empty → omit, all cases |
| `min_layer_count:` | `min_layers:` | `null` → omit, no filter |
| `metrics_run_ids: []` | no metrics block | a populated list meant one table per run |
| `reporter.title` | `title:` | |
| `reporter.description` | `intro:` | |
| `reporter.static_end_text` | a prose block after the grid | |
| `reporter.save: RunIDMLFlowReportAttacher(run_id: …)` | `publish: <run_id>`, plus `--publish` | a key never writes |
| `template_asset_path` | nothing | the shell is the library's |
| Hydra conf logging | `extra_dir=` on publish | lands under `report/conf` |

**The bare-retriever trap.** In the original tool a retriever with no
`shader_conf` got no shader block at all and therefore drew **on**. Here the same
row gets the library's defaults — `color="#fff705"`, `opacity=1.0`
(`masks.py:183-184`) — but `visible` defaults to `False` (`masks.py:185`), so a
faithful transcription must say `visible=True` or the layer the old report showed
is invisible in the new one. That is why `scripts/dysplasia_tile_masks.py:106-112`
passes `visible=True` for the one folder-backed row the config left bare.

**The join is the file stem.** `case_001.svs` and `case_001.tiff` are the same
case — the original tool's rule, inherited on purpose (`Mlflow.masks`,
`mlflow.py:226-247`). A mask directory matched by anything else silently drops
layers; a layer no case gets at all stops the build, which is the point, since a
mistyped run id used to cost one column and no message.

## `classify` → `colormap`, field by field

| v2 `shader_conf` | v3 layer |
| --- | --- |
| `classes: N` | `color = {"type": "custom_colormap", "default": palette, "steps": N}` |
| `color: [c1…cN]` | that palette, one entry per class |
| `threshold.breaks` / `mask` | `threshold = {"type": "advanced_slider", "breaks": […], "mask": […]}` |
| `opacity` | the shared `opacity` param (`shader.py:177`) |
| `modifiable: true` | nothing — the emitted `threshold` is already an `advanced_slider` |

`classify_shader()` keeps the viewer's `colormap_class_count` coupling valid:
`len(palette) == len(breaks) + 1`, `breaks` defaulting to even cuts, `mask` to all
ones, palette capped at 32 (`shader.py:184`). `[0, 1, 1]` leaves class 0 —
usually the slide — unpainted.

## One row, both spellings

```yaml
# conf/dysplasia_tile_masks.yaml:31-40
mask_retrievers:
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
# scripts/dysplasia_tile_masks.py:39,48 — same layer, same place in the order
TISSUE = "97084241311949189445f864d42e9d4e"
Mask("Tissue", MlflowRun(TISSUE, "tissue_masks"), color="#ffff00", opacity=0.5),
```

`visible: false` is gone because it is the default; `${run_id}` is gone because
Hydra interpolation has no meaning here — resolve it to the literal run id. The
config's run `ad84f424e21742868a746c5fd8a9978b` is where the masks live; the run
the report is published to is a separate choice (`PUBLISH_RUN`,
`dysplasia_tile_masks.py:36`), and v2's `artifact_path: report.html` needs no
equivalent — a published report lands at `report/report.html` either way
(`mlflow.py:69,293-294`).
