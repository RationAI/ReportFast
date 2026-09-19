# xOpat v3 — what decides whether a session loads

Viewer behaviour, not library procedure. Written against xOpat **3.1.0, commit
`18c94f2`**. Nothing here is enforced by the library — it is what you check when
a card is wrong and nothing raised. When the viewer moves, this moves; read the
source rather than trusting this page for a key name.

Upstream, at that commit:

- `src/parse-input.js` — what a link goes through.
- `src/types/app.d.ts` — the session document.
- `src/libs/flex-renderer/flex-renderer.js` — where layer types are registered.
- `src/app.ts` — `sanitizeAgainst`, which is why `params` fails silently.

## The fragment is the payload

- `xOpatParseConfiguration` takes its session in this order: a POSTed
  configuration → `location.hash` → the legacy `?visualization=` query → the
  localStorage cache. A link *is* the session; nothing is fetched and there is
  no server state behind it.
- `viewer_url()` writes `#<percent-encoded json>` onto the viewer root,
  percent-encoding keeping `{ } " ,` out of the URL.
- The fragment never reaches a server, so sessions stay out of access logs and a
  report is a file of links that can be mailed.
- A parse failure is indistinguishable from an empty viewer. Only
  `background[].dataReference` validity is hard-validated at parse time —
  everything else the session gets wrong degrades into a picture that looks like
  a picture.

## `data[]` is a positional pool

- Every reference into `data[]` is an array index, so reordering the pool
  silently rewires overlays. The pool is append-only and the index returned by
  `XopatSession.add_data()` is what a reference must carry; typing one is how a
  300-card report ends up with an overlay on the wrong slide.
- `background[].dataReference` is **singular**, one int.
  `visualizations[].shaders[*].dataReferences` is **plural**, a list.
- A singular `dataReference` on a shader is ignored, silently — no warning, no
  failed request, a layer bound to nothing sitting in a layer list where it reads
  as present. That is the v2 key; v3 reads only the plural one.
- The one hard failure is a `background[].dataReference` outside `data[]`: the
  viewer refuses to boot at all. `XopatSession.from_config` raises on that, and
  on an out-of-range `dataReferences`, naming the JSON path — that check is
  arithmetic on the document, not knowledge of the viewer, so it is the one the
  library keeps.
- Which overlay set a background shows is
  `visualizations[background[activeBackgroundIndex[k]].visualizationIndex]`, so
  renumbering `visualizations[]` changes what every background *looks like*, not
  what exists.

## `sessionName` is a cache key

- The cached-viewport key is `viewport:${sessionName}:${bgId}`. It records where
  the reader was zoomed and panned — it is not a title and is not displayed
  anywhere.
- One shared `sessionName` across a folder of slides makes them inherit each
  other's zoom and pan, so a 32-card report opens 31 times at the wrong scale.
  Unset, the viewer derives one per slide — which is what you want almost always.
- Human text goes in `background[].name` (the card label and the viewer's
  background menu) and `visualizations[].name` for the overlay set.
  `XopatSession.bind_name()` sets those two and deliberately leaves
  `params.sessionName` alone.

## `params`: an unknown key dies quietly

- `sanitizeAgainst` drops every key outside the viewer's own `setup` defaults
  without a word, and the layer then renders its defaults. The library keeps
  every key you hand it — deliberately, because an allowlist here would be a
  second source of truth about a deployment it does not run, and a stale one
  rejects sessions the current viewer renders fine. So **you** are the check on
  a `params` key: read it being used in viewer source, or do not write it.
- Of the flat `params.ui.<key>` spellings, only **`scaleBar`, `statusBar` and
  `toolBar` survive**. `getUiOption` does fall back to a flat `params[key]`, but
  `sanitizeAgainst` runs *first* and strips any top-level key the setup defaults
  do not carry — and `appBar`, `globalMenu`, `mainMenu`, `navigator`
  (`globalMenuMode`, `sideMenuCompact`) are not among them, so they never reach
  the fallback. Write `params.ui.<key>`, always.
- `viewport`, `activeBackgroundIndex` and `activeVisualizationIndex` record where
  the author was looking, not what to show. `from_config` drops them by default
  (`drop_state=True`); pass `drop_state=False` to keep them.
- `__age`, `__envKey`, `__fromLocalStorage`, `__fromSessionStorage` are bolted on
  by the viewer at runtime and stripped on import.

## Per-source handling lives on the data entry

- `options.format: "png"` is v3's replacement for v2 `lossless: true`, set per
  data entry as `{"dataID": …, "options": {"format": "png"}}` and reaching the
  tile server as `&image_format=`. Lossy tiles shift a class map's colours, so
  overlay layers ask for png by default (`add_layer(lossless=True)`) and the
  background keeps the deployment default.
- `background[].protocol` is v2 and deprecated: the same override now sits as
  `protocol` on the `data[]` entry the background references.
- Only the **name** of a `slide_protocols` entry registered in the deployment's
  `env.json` works. Inline-JS protocol templates are refused outright by
  `src/classes/slide-protocols.ts` as an RCE sink, falling back to the deployment
  default with no actionable error — so `xopat.py` raises `XopatError` on a
  backtick or `${` in a protocol name. That one check is a security boundary, not
  vocabulary.
- `options.plugin` only reaches `/info` when the protocol entry declares
  `tileSourceClass`; otherwise it arrives too late to matter for discovery.
- `imageSmoothingEnabled: false` on the data entry samples with `gl.NEAREST`
  instead of `gl.LINEAR`, which is what an integer label map needs to keep its
  class values exact. Only FlexDrawer honours it.
- `background[].id` is a stable handle — derived from the data path when unset —
  feeding `viewer.uniqueId`. Naming it keeps a background's state across slot
  reordering.

## Layer types are the viewer's list, not ours

`createShaderLayer` **throws** on a type the registry does not hold
(`Unknown shader type '…'`), and the loop that builds one background's overlays
does not catch it — so an unregistered type is not one missing layer, it is that
layer *and every layer after it in the `shaders` order*. A typo in the last layer
of a group and a typo in the first look the same from the report: an overlay set
that is not what you wrote. The message goes to the browser console, which the
reader of a report never opens.

The library passes `type` through without checking it, deliberately — a registry
here would be a second source of truth about a viewer it does not run. So read
the registrations in `flex-renderer.js` (`ShaderLayerRegistry.register` +
`static type()`) rather than trusting this page. At 3.1.0 the registered names
were:

```
adaptive_threshold  bipolar-heatmap  colormap  edge  fisheye-lens  grid
gridheatmap  group  heatmap  iconmap  identity  interaction-debug  patternmap
single_channel  sobel  stain-separation  texture  threshold  time-series
channel-series
```

`classify`, `segmentation` and `bounding_box` appear nowhere in it — those are v2
names, and a config carrying them produces a blank overlay rather than a message.
The v2 → v3 reading is `classify` → `colormap`, and `bounding_box` → `iconmap`
only in the loose sense that both mark things; `iconmap` draws a per-class icon
from a scalar field, so a box overlay usually needs a different approach entirely.

Two shapes are built for you in `report_fast.layer` — `heatmap_layer` for a
scalar field tinted one colour, `colormap_layer` for a class map. `colormap_layer`
keeps the coupling the viewer checks as `colormap_class_count`: the palette holds
one colour per class, and `classes - 1` breaks bin between them. Everything else
is a dict you write.

## If the card is black, check in this order

1. **Viewer mount** — `/v3/`, not `/xopat/`. v2 parses a v3 session and looks
   loaded.
2. **DataID** — relative to the mount root, no `/mnt` left on it.
3. **The file** — present on the tile server's mount, under that same root.
4. **Tile root** — `/wsi-service/`; the viewer's `/v3/` is a different mount and
   answers image paths with nothing, which reads as a blank card.
5. **Protocol** — a *registered name*, and the deployment default can actually
   read the background's format (non-TIFF backgrounds are the usual miss). A slide
   whose pyramid levels do not step by the factor the default reader assumes is the
   other way this bites: the background loads and the image is wrong or blank rather
   than erroring. The fix is the same one the v2 tool used — name a different
   registered protocol for the background: `XopatEndpoint(image_protocol="…")`,
   `XOPAT_IMAGE_PROTOCOL`, or `protocol=` on `from_slide`. Which names exist is a
   fact about that deployment's `env.json` (`slide_protocols`), not about this
   library; `wsi_service` is the one registered on the cluster deployment. There is
   no way to list them from a script, and no way to check the choice short of
   opening the page.
6. **`/v3/slides/info?slide_id=<DataID>`** — the viewer's own first request;
   probe it rather than reasoning about it.
7. **Overlay missing while the background shows** — `dataReferences` spelled
   plural, in range, and `type` one v3 registers.
