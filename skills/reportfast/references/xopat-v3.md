# xOpat v3 — what decides whether a session loads

Viewer behaviour, not library procedure. The facts below are what makes a
session load or quietly not load; re-derive only if the viewer moves.

## The fragment is the payload

- `parse-input.js` (`xOpatParseConfiguration`) takes its session in this order:
  a POSTed configuration → `location.hash` → the legacy `?visualization=` query
  → the localStorage cache. A link *is* the session; there is nothing to fetch
  and no server state behind it.
- `viewer_url()` writes `#<percent-encoded json>` onto the viewer root
  (`xopat.py:488-500`), percent-encoding keeping `{ } " ,` out of the URL.
- The fragment never reaches the server, so sessions stay out of access logs and
  a report is a file of links that can be mailed.
- Parsing failure is indistinguishable from an empty viewer. Only
  `background[].dataReference` validity is hard-validated at parse time
  (`DESIGN.md:367-371`) — everything else the session gets wrong degrades into a
  picture that looks like a picture.

## `data[]` is a positional pool

- Every reference into `data[]` is an array index, so reordering the pool
  silently rewires overlays — the pool is append-only, and the index handed back
  by `add_data()` (`session.py:319`) is what a reference must carry.
- `background[].dataReference` is **singular**, one int (`session.py:401`).
  `visualizations[].shaders[*].dataReferences` is **plural**, a list
  (`xopat.py:432`).
- A singular `dataReference` on a shader is ignored, silently — no warning, no
  failed request, a layer bound to nothing sitting in a layer list where it reads
  as present. That is the v2 key; v3 reads only the plural one.
- The one hard failure is a `background[].dataReference` outside `data[]`: the
  viewer refuses to boot at all (`session.py:737`).
- Which overlay set a background shows is
  `visualizations[background[activeBackgroundIndex[k]].visualizationIndex]`
  (`DESIGN.md:372-374`), so renumbering `visualizations[]` changes what every
  background looks like, not what exists.

## `sessionName` is a cache key

- The cached-viewport key is `viewport:${sessionName}:${bgId}`. It is where the
  reader was zoomed and panned — not a title, and not displayed anywhere
  (`DESIGN.md:375-378`).
- One shared `sessionName` across a folder of slides makes the slides inherit
  each other's zoom/pan, so a 32-card report opens 31 times at the wrong scale.
  Unset, the viewer derives one per slide.
- Human text goes in `background[].name` (the card label and the viewer's
  background menu) and `visualizations[].name` for the overlay set. `bind_name()`
  sets those two and deliberately leaves `params.sessionName` alone
  (`session.py:256`).

## `params`: an unknown key dies quietly

- `sanitizeAgainst` in `src/app.ts` drops every key outside the viewer's `setup`
  allowlist without a word, which is why the library rejects them in Python
  against `PARAM_KEYS` (`xopat.py:92`) instead of shipping dead JSON.
- Of the flat `params.ui.<key>` spellings, only **`scaleBar`, `statusBar` and
  `toolBar` survive**. `getUiOption` does fall back to a flat `params[key]`, but
  `sanitizeAgainst` runs *first* and strips any top-level key the setup defaults
  do not carry — and `appBar`, `globalMenu`, `mainMenu`, `navigator`
  (`globalMenuMode`, `sideMenuCompact`) are not among them, so they never reach the
  fallback. Write `params.ui.<key>`, always.
- The library splits them the same way: `contract.flat_ui_aliases()` (three, kept
  silently) versus `contract.stripped_flat_ui_aliases()` (six, which the viewer
  drops). A *paste* holding one of the six gets a warning naming the key and the
  nested spelling; an authored session is refused. Trusting `getUiOption` alone is
  how four dead keys spent a release in `PARAM_KEYS`.
- `viewport`, `activeBackgroundIndex` and `activeVisualizationIndex` record where
  the author was looking, not what to show. `from_config` drops them by default
  (`STATE_KEYS`, `session.py:62`); `drop_state=False` keeps even those.
- `__age`, `__envKey`, `__fromLocalStorage`, `__fromSessionStorage` are bolted on
  by the viewer at runtime and stripped on import (`session.py:68-69`).

## Per-source handling lives on the data entry

- `options.format: "png"` is v3's replacement for v2 `lossless: true`
  (`LOSSLESS_TILE_FORMAT`, `xopat.py:75`), set per data entry as
  `{"dataID": …, "options": {"format": "png"}}` and reaching the tile server as
  `&image_format=`. Lossy tiles shift a class map's colours, so overlay layers
  ask for png by default and the background keeps the deployment default.
- `background[].protocol` is v2 and deprecated: the same override now sits as
  `protocol` on the `data[]` entry the background references
  (`background_protocol`, `xopat.py:394`).
- Only the **name** of a `slide_protocols` entry registered in the deployment's
  `env.json` works. Inline-JS protocol templates are refused outright by
  `src/classes/slide-protocols.ts` as an RCE sink, falling back to the deployment
  default with no actionable error — so the library raises `XopatError` on a
  backtick or `${` in a protocol name (`_reject_inline_protocol`, `xopat.py:252`).
- `options.plugin` only reaches `/info` when the protocol entry declares
  `tileSourceClass`; otherwise it arrives too late to matter for discovery
  (`DESIGN.md:379-380`).
- `imageSmoothingEnabled: false` on the data entry samples with `gl.NEAREST`
  instead of `gl.LINEAR`, which is what an integer label map needs to keep its
  class values exact. Only FlexDrawer honours it (`DESIGN.md:381-382`).
- `background[].id` is a stable handle — derived from the data path when unset —
  feeding `viewer.uniqueId`. Naming it keeps a background's state across slot
  reordering (`DESIGN.md:387-389`).

## If the card is black, check in this order

1. Viewer mount — `/v3/`, not `/xopat/`. v2 parses a v3 session and looks loaded.
2. DataID — relative to the mount root, no `/mnt` left on it.
3. The file — present on the tile server's mount, under that same root.
4. Tile root — `/wsi-service/`; the viewer's `/v3/` is a different mount and
   answers the image paths with nothing, which reads as a blank card.
5. Protocol — a *registered name*, and the deployment default can actually read
   the background's format (non-TIFF backgrounds are the usual miss).
6. `/v3/slides/info?slide_id=<DataID>` — the viewer's own first request; probe it
   rather than reasoning about it.
7. Overlay missing while the background shows: `dataReferences` spelled plural,
   in range, and `type` one v3 registers — `createShaderLayer` throws on an
   unknown type and drops that whole layer.
