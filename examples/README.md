# Example sessions

Sessions in this folder are test fixtures for `XopatSession.from_config`. They
are deliberately *not* things this tool emits — that is the point: they exercise
the paste path with configs written by other hands.

| File | What it is |
| --- | --- |
| `viewer_export.json` | A session exported by the v3 viewer (`#<json>` link a colleague sent), transcribed by hand. Shows the shapes the tool must survive: `protocol` on a bare-path deployment's data entries, `background[].shaders` inline instead of a `visualizations[]` entry, `visualizationIndex: null`, the v2 `lossless` key the viewer ignores, an `id` the viewer generated, `plugins`, and navigation state (`viewport`, `activeBackgroundIndex`) that `drop_state` removes. Its DataIDs are `iipimage` files on another mount, so this deployment's tile server answers 404 for them: the link opens if that other viewer can reach them, but a card built from it has no preview to draw. |
| `dysplasia_case.json` | **Authored here in the viewer's shape**, over DataIDs this deployment does serve, so it is the fixture the demo (`scripts/test_report.py`) pastes in and it renders with a picture. Two backgrounds of one tile (CE and H&E) plus a saved colormap layer, and the same awkward shapes as above: viewer-minted ids, `visualizationIndex: null`, an inline `shaders` array, `sessionName`. |
| `multi_background_case.json` | **Authored here as an illustration**, modelled on a hand-written radiotherapy follow-up session: several timepoints as switchable backgrounds over a shared `data[]` pool, `options.plugin` naming the server-side reader, `imageSmoothingEnabled: false` on the integer label volumes, `order` naming shaders by key, and shaders carrying more than one `dataReferences` entry. Not a copy of anyone's file — replace it with the real one when you have it. |


All are JSON so `json.loads` reads them directly; the viewer's own
`src/config.json` is JSON-with-comments and needs stripping first.

If a real config changes behaviour, re-bless the fixtures and check
`tests/test_session.py` still describes what the viewer does — the assertions
quote `xopat/src`, not this tool.
