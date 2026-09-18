# Example sessions

Sessions in this folder are test fixtures for `XopatSession.from_config`. They
are deliberately *not* things this tool emits — that is the point: they exercise
the paste path with configs written by other hands.

| File | What it is |
| --- | --- |
| `viewer_export.json` | A session exported by the v3 viewer (a `#<json>` link handed over in chat), transcribed by hand. Shows the shapes the paste path must survive: `protocol` on a bare-path deployment's data entries, `background[].shaders` inline instead of a `visualizations[]` entry, `visualizationIndex: null`, the v2 `lossless` key the viewer ignores, an `id` the viewer generated, `plugins`, and navigation state (`viewport`, `activeBackgroundIndex`) that `drop_state` removes. Its DataIDs are absolute `/data/Public/...` paths served by `iipimage` on a different deployment — outside this one's `/mnt` mount root, so `mount_path()` leaves them untouched, and this deployment has no such file to serve. The link opens if the viewer it is opened against can reach them; a card built from it here has no preview to draw. (Whether it 404s or times out was never checked from here — the tile server is unreachable from this pod; see `references/deployment.md`.) |
| `dysplasia_case.json` | **Authored here in the viewer's shape**, over DataIDs this deployment does serve, so it is the fixture a pasted-session snippet reads and it renders with a picture. Two backgrounds of one tile (CE and H&E) plus a saved colormap layer, and the same awkward shapes as above: viewer-minted ids, `visualizationIndex: null`, an inline `shaders` array, `sessionName`. Read it before authoring a session: `reportfast skill show --reference examples/dysplasia_case.json`. |
| `multi_background_case.json` | **Authored here as an illustration**, modelled on a hand-written radiotherapy follow-up session: several timepoints as switchable backgrounds over a shared `data[]` pool, `options.plugin` naming the server-side reader, `imageSmoothingEnabled: false` on the integer label volumes, `order` naming shaders by key, and shaders carrying more than one `dataReferences` entry. Not a copy of anyone's file — replace it with the real one when it exists. It also carries a param nobody intended: `inverse` on its three `rtstruct` **colormap** layers. `heatmap` declares `inverse` and `colormap` does not, so the identically-spelled param on the sibling `dose` layers is fine while these three are dropped by v3 in silence. Nothing in the library reports that, and nothing should — holding a per-type field vocabulary here would mean a second source of truth about a viewer this code does not run. `tests/test_session.py::test_the_multi_background_case_still_carries_its_one_stale_param` pins the six outcomes so the fixture keeps teaching the lesson rather than being tidied into uselessness. |


All are JSON so `json.loads` reads them directly; the viewer's own
`src/config.json` is JSON-with-comments and needs stripping first.

If a real config changes behaviour, re-bless the fixtures and check
`tests/test_session.py` still describes what the viewer does — the assertions
quote `xopat/src`, not this tool.
