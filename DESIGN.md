# ReportFast — design brief

Decisions and the reasons behind them. `README.md` documents what exists;
`skills/reportfast/references/` documents xOpat v3, this cluster, and MLflow. This
file holds neither: it holds *why it is this way and what was ruled out*, so that a
deleted organ is not rebuilt by someone who did not know it had been deleted.

Where this file and the code disagree, the code wins and this file is stale. That
order is deliberate — the last version of this document claimed authority over
`README.md` while five of its eleven decisions had been reversed, which is the
most convincing argument against doing so that a document can make.

## The problem being solved

The old reporting tool could not reach most of what xOpat v3 can do. A session
hand-authored by a user — backgrounds, overlay layers, protocols, per-source
options — had no way through that tool's API, so its feature ceiling was the tool's
rather than the viewer's. The requirement is that a user who hits the ceiling is not
stuck.

The failure mode that followed from that is the specific one worth solving. Session
JSON punishes exactly the mistakes that are easiest to make, and punishes them
quietly: a `data[]` reorder that silently points an overlay at the wrong file, a
`/mnt` left on a DataID (black card), an invented `params` key dropped by the viewer
without a word, a wrong viewer mount that *looks* like it loaded. Each of these has
cost someone an afternoon, because nothing reports it.

## The agent is the user

Most of this tool was written by an agent, and an agent already carries the context
xOpat needs — the mounts, the DataID rule, which run holds which masks. So a report
is **requested**, not coded, and nobody has to open this repository to get one.

> **The agent authors the sessions and the script. The library owns the URL and the
> page.**

| The agent does this | The library does this |
| --- | --- |
| Reads a folder or a run and sees what is there | The URL: session JSON → fragment → link, with the encoding right |
| Authors the session document, from the viewer's own source | `data[]` indices, minted by `add_data()` and never typed |
| Writes the script: the loop, the colours, the order, the cohort | The page: cards, grid, thumbnails, CSS — from two components |
| Decides what a report is *about* | Whether anything is published — which is never its own decision |

**The script is the report's record.** It names the folders, colours, order and
layout, and unlike a spec file it can be re-run. It is not stored by the library, so
the agent asks where it should live.

### What replaced "the gate"

The line above used to read *"the library owns the gate."* There is no gate. What
survives of it is `_check_references` in `session.py`: every index in a document is
checked to point inside the pool it indexes, and a failure names the JSON path.

That is not a narrowing of the gate, it is a category change. The deleted half
compared sessions against a Python copy of the viewer's vocabulary — a schema, a
`params` allowlist, a layer registry, all generated from the pinned viewer source by
`derive_schema.py`. It had to be re-derived every time the viewer moved, its
generator had its own tests to catch the generator drifting, and a stale copy
*rejects sessions the deployed viewer renders fine* — which is a worse failure than
the one it prevented, because it is a hard no about something that works.

The surviving half does not know what a session means. It reads the document's own
lengths. A viewer update cannot make it wrong.

So the two silent failures are handled differently now, and honestly:

- **A stale `data[]` index** — the library catches the out-of-range case. An index
  that is *in range* and wrong is not catchable by anything, which is why the skill
  says bind sessions in a loop rather than copy-paste 300 files.
- **An invented `params` key** — nobody catches it. The library keeps every key
  handed to it, and the skill says to read the key being used in viewer source or
  not write it. This is a rule for the agent, not a check, and it is stated as one
  rather than being dressed up as a guarantee.

## Terminology

**Say "session", not "slide".** A session is one viewer boot: N backgrounds (each its
own slide, timepoint or region), each with N overlay layers. One slide is the most
common input, not the unit. `from_slide(path)` is a convenience constructor.

## Core shape

```
XopatSession            config in → config / URL out. No rendering, no I/O.
   ▲
   ├── the script an agent writes
   └── SlideCard, SlideGrid, Report     (the page)
```

1. **`XopatSession` is the base object.** It creates a session from a slide path and
   accepts one someone else authored.
2. **It is a plain value object.** No FastHTML import, no file I/O, no rendering. A
   script importing only `XopatSession` keeps working when the report layer changes.
3. **Components take sessions, never paths.** That seam is what lets one session feed
   a grid, a card, or hand-rolled HTML.

### The paste path

A config or link someone else authored is taken **verbatim**. Fields we do not model
are not normalised away and keys we do not recognise are not rejected — that *is* the
feature. Only `data[]` indices are renumbered, and only when grafting something onto
it. Round-trip is a contract: `from_config(s.to_config()).to_config() ==
s.to_config()`. If it leaks, the paste path leaks.

## Locked decisions

Several of these were reversed, and the reversal is the interesting part — recorded
below the table rather than quietly rewriting it.

| # | Question | Decision |
| --- | --- | --- |
| 1 | Does a session render itself, or is it config + a card? | Both: the session is a value object, `SlideCard(session=…)` renders it |
| 2 | How does a 300-case grid avoid typing 300 documents? | **The script loops.** `from_slide()`/`case_matrix()` per case; `SessionTemplate.bind()` when a hand-authored shape needs refilling |
| 3 | What shape do the defaults take? | **The call's own arguments.** No preset, no preset file — both were a second way to set what the call sets; see reversals |
| 4 | Static file or served app? | **Static only.** No server, no state, no re-render |
| 5 | Where do masks come from? | A `Mask` names a layer and carries its own **source** — `Drive` folder or `MlflowRun` artifacts — so the call reads like the job |
| 6 | Does the agent write the report, the code, or the manifest? | **The code.** The manifest is gone; the script is the interface and the record |
| 7 | What if xOpat can do something the library does not model? | The agent writes the field, the library keeps it. One construction rule: `data[]` indices are minted, never typed |
| 8 | What is the durable artifact? | **The script and the HTML.** ~~A provenance sidecar~~ — a script that re-runs *is* the provenance |
| 9 | Can two runs differ? | **No.** Same sessions in, byte-identical HTML out — asserted in `test_the_same_report_renders_byte_identical_html` |
| 10 | Who may create report HTML? | **Two components and the page itself.** `RawHtml` is gone: one block accepting arbitrary markup makes the set advisory |
| 11 | How does the agent learn xOpat's vocabulary? | **From the viewer**, at a permalink, for the version deployed in front of it. ~~A generated schema bundled in the skill~~ — see reversals |
| 12 | Is there a CLI? | **No, beyond printing the skill.** `reportfast plan/build/find` were built and deleted |

## Reversals

Things that were built, used, ruled on in review, and then deleted. Each is listed
with what finally decided it, because "we ruled this in review" is not an argument for
keeping it.

**The CLI, and with it the manifest** (`e705d7e`). `reportfast plan|build|find` and
`manifest.py` — a YAML spec language with strict key checking, a resolved plan,
`--sessions-dir` as a door. Deleted because the report is requested in chat and what
answers it is a script; a `build` command was a second, worse way to write the same
loop over files, and the YAML needed a loader, a schema for the loader, an error
format for the schema, and a page of documentation about precedence. Review had
already made the prompt the entry point; this made it the *only* one.

**`skill install` and `skill where`.** The CLI's other half, deleted later: the skill
travelled inside the wheel and one command copied it into `~/.claude/skills`, with
`--project`, `--dest`, `--force` and `--link` covering where and how. It went because
Claude Code already has ways to take a skill — a project commits a `skills/`
directory, or the skill arrives as a plugin — and a library writing into a home
directory is neither. Being opt-in was not the same as being the official mechanism.
What survives is the reason the command existed at all: the skill must travel *inside*
the package, because a procedure shipped separately from the code it describes drifts
from it. So `reportfast` prints and writes nothing, which two tests now hold: the
module has no write path in it, and `skill show` leaves the bundle's files
byte-for-byte as they were.

**A session's defaults, twice.** This happened twice over, and the second time is why
the first time was not enough.

*The file.* `load_preset` read JSON or TOML from `$XOPAT_SESSION_CONFIG` and merged it
under every session, and `parse_preset` validated the document against `FORBIDDEN_KEYS`
and `PRESET_KEYS`. It contradicted *Not doing*, was documented nowhere a reader could
see, and was strictly less expressive than the script it was supposed to simplify. An
environment variable nobody typed could change a report's numbers, invisibly, which is
the exact property the script exists to prevent.

*The object that replaced it.* `SessionPreset` kept the merging and dropped the file —
which fixed the invisible input and kept the actual problem, that a second vocabulary
sets what the call already sets. It was deleted with the file's reasoning applied
consistently: every one of its seven fields (`params`, `plugins`, `layers`, `protocol`,
`options`, `lossless`, `endpoint`) is an argument of `from_slide`, so it added
indirection and no capability. The tell was that the skill never taught it — an agent
reading `SKILL.md`, the four references and the README meets `SessionPreset` zero
times, and a knob the writer of the report is never told about is a knob that does not
exist. `test_every_session_default_is_an_argument_of_the_call` pins the absence through
`inspect.signature` rather than by asserting a refusal, because a refusal is a
compatibility shim and the point is that there is nothing to pass.

Two properties survived, which is what the deletion had to preserve. A preset could
carry no index-referencing list, so `data`/`background`/`visualizations` were unnameable
in it; with no preset at all there is still no route for one, and
`test_nothing_outside_the_call_can_supply_a_session_default` checks that against a live
`$XOPAT_SESSION_CONFIG` rather than a stub. And the one default the tool keeps — lossless
overlay tiles, because a class map's colours do not survive JPEG — is now a keyword
default, so a caller can turn it off; a preset-level default could not have offered that
without the preset coming back.

**The validation gate** (`a529e0a`). `schema/` (2,400 generated lines),
`derive_schema.py`, `contract.py`, `audit.py`, `shader.py`, `strict=`. See *What
replaced the gate*. The last sentence of the case against it: a copy of the viewer's
feature list has to be re-derived forever, and gets it wrong in the direction of
blocking working sessions.

**The build-time probe** (`e705d7e`). `verify.py` asked the tile server about every
DataID before a build counted as finished. It went with the CLI it was wired into —
and the important part survived as prose: the skill teaches the one `curl` probe, and
teaches that **`000` means unreachable, not broken**. This pod cannot reach the tile
server at all, so most reports here are built with links unverified from here, and an
agent that reports "works" after a `000` is the failure the probe was supposed to
prevent. Deleting the code without fixing that prose would have been the real loss.

**Ten components down to two** (`38cd95a`). `Prose`, `Heading`, `Bullets`, `LinkList`,
`MetricTable`, `Chart`, `RawHtml` gone; `SlideCard`, `SlideGrid` remain, with `Report`
and `Section`. The components existed so reports would look like one product, and ten
of them was ten ways not to — captions alternating between `Prose` and `Heading`,
metrics sometimes a table and sometimes cards. `RawHtml` was the load-bearing one: it
was guarded by code in `compose.py` that rejected inline HTML on the agent path, and
when `compose.py` went the guard went with it, leaving a hole that made the set
advisory. Extending a page still has a route — subclass `BaseComponent`, use a class
prefix that is not `rf-` — which differs from `RawHtml` in being something you wrote
deliberately rather than a string you passed.

`Chart` was matplotlib's only consumer, so the dev group lost it and `uv sync` no
longer pulls numpy, pillow, contourpy and fonttools to render a figure nobody can
make.

**The orphan the same deletion left behind.** `ComponentRegistry` mapped
`"slide-card"` to a class *so components could be created from a config file* — its
own docstring, quoted. The manifest went first and `test_the_library_reads_no_config_files`
keeps it gone, which left a name → class lookup with no caller able to carry a name:
the input side of a format that no longer exists, read only by the test that tested the
lookup. `layers_from_files` went the same way (a six-line helper whose `slide` argument
was discarded and which nothing in the library called, beside two other stem-join
implementations), and so did `XOPAT_MAJOR` — the major version once selected a URL
shape; there is one shape and nothing read the constant.

What replaces the registry is a capability check, not a class-name check:
`test_a_component_is_reached_by_importing_it` fails on *any* exported class that
resolves a component by name, because the reflex worth forbidding — "let the script
pick a component by name" — would arrive under a different name. Writing it surfaced a
trap worth recording, since it is the reason a guard can look present and be absent: the
first version read a class's `vars()` and filtered on `callable()`, which is False for a
raw `classmethod` object, so a real resolver's method set read as empty and the
assertion passed on the exact thing it forbids. `inspect.getmembers(owner, callable)`
goes through `getattr` and binds the descriptor. Both branches were checked by
reintroducing the regression, and the first attempt at that check passed silently, which
is how this was caught.

**The provenance sidecar** (`e705d7e`). `report.provenance.json` beside every report:
endpoint, viewer stamp, inputs, every DataID, the design as authored. It answered a
question the manifest had created. Once the report is a script, the script answers it
better and is re-runnable. One fact worth keeping from the sidecar's design, since it
will come up again: `design` was always a *claim the caller made*, never a fact the
tool derived — by the time a page is composed, a bound session carries its DataIDs and
no note of its parent.

**The demo scripts** (`d5eccba`). Three runnable examples, all calling the deleted
`build_report`, so none had run in weeks and no test could see that — nothing under
`tests/` imports them. Worked examples are the skill's job now. The knowledge in the
last one, a hand transcription of the v2 Hydra config, is in
`references/hydra-v2-to-v3.md`.

**Two decisions the reversals forced back in.** A report needs *some* prose, so
`Report.subtitle` and a new `Report.preamble` carry the two pieces a report about
slides ever needed — one line under the title, one paragraph above the blocks, both
escaped, both styled by the library. And `case_matrix` turned out to be public in
`masks.py` but unreachable from the package root, a real drift bug that only surfaced
because writing the documentation meant executing every snippet in it.

## The workflow is the API

Most reports here are the same job: a folder of slides, ten overlay directories across
four runs, a list of cases worth looking at. `masks.py` makes that one expression
because the plumbing was the complaint about the original tool, not its YAML.

- **A source answers one question**: `index() → {case stem: DataID}`. `Drive` walks a
  mount, `MlflowRun` lists a run's artifacts, and a source of your own is those four
  lines.
- **Stem is the join.** `case_001.svs` and `case_001.tiff` are the same case — the
  original tool's rule, inherited on purpose.
- **`only=` is the report order**, `min_layers=` the case filter: the old config's
  `selected_items` and `min_layer_count` with the same meaning.
- **A mistyped run id fails loudly.** With no mask file for any case,
  `MlflowError` names the sources that got zero hits. The old tool returned an empty
  table and produced a report with one fewer column and no indication of it.
- **`coverage` and `dropped` are returned, not printed.** A layer that reached 3 of 32
  cases is either a finding or a wrong source; the caller has to look, and the numbers
  travel with the sessions instead of being lost to a log.
- Sources are read once per report however many masks share them, and a `Mask` holds
  only drawing state — the same `Mask` over two runs is a different layer, so frozen
  dataclasses are enough and there is no registry.

## Construction and merge rules

Unchanged by the reversals, and still the part of this file most likely to be right.

**Precedence:** pasted config → the arguments of the call. Nothing is merged in from
outside either of those.

- `params` **deep-merges** (it is a settings bag).
- `data` / `background` / `visualizations` / `plugins` **replace**. Lists carrying
  index references are never merged implicitly.
- Combining two sessions is an explicit `merge()` that renumbers the second one's
  indices — never a silent deep merge.
- `data[]` is append-only through one helper that returns the index it appended.
- `from_config(drop_state=True)` strips `viewport` / `activeBackgroundIndex` — where
  the author had navigated, not what to show. Runtime keys (`__age`, `__envKey`, …) are
  always stripped; the viewer bolts them on.
- Templates preserve authored shader keys (`dose`, `rtstruct`, …), because
  `visualizations[].order` refers to them by name.

## Loud and quiet, as the code stands

The design claim is not "everything is validated" — it is that **the silent failures
are known and named**, and that the list is short.

Fails loudly:

| Situation | Where |
| --- | --- |
| An index outside the pool it indexes | `_check_references`, naming the JSON path |
| A protocol name that looks like inline JS | `_reject_inline_protocol` — the viewer strips it silently, so this is caught early |
| No mask file for any case (mistyped run id or artifact dir) | `MlflowError`, naming the sources with zero hits |
| A palette/breaks/classes mismatch | `colormap_layer` raises rather than emitting a layer the viewer rejects |
| A background or shader with no `dataReference` at all | `XopatError` |

Fails quietly, on purpose or for now:

- **An unknown `params` key.** Kept, and dropped by the viewer at load. No check: see
  *What replaced the gate*.
- **An in-range but wrong `data[]` index.** Undetectable in principle.
- **A `type` the viewer does not register.** Passed through; at 3.1.0 the viewer
  throws while building that background's overlays and takes **every later layer with
  it**, so a typo in a layer list's last entry and its first look identical from a
  report. The message goes to a browser console the reader never opens.
- **A slide that is not there.** The builder never opens a slide. Nothing checks
  shared-path agreement between the building machine and the tile server.
- **An artifact prefix the deployment does not serve.** `artifact_data_id()` defaults
  to `mflow`; the real namespace is a fact about the WSI-Service's registered
  protocols. A wrong prefix produces well-formed DataIDs, real artifacts and a report
  that builds clean with every overlay missing. Found in use, not in review.

The last four all have the same shape: they are facts about a viewer this library
does not run, and every attempt to check them in Python has been deleted. They are
therefore *procedure* — in the skill, phrased as things to check and to say — rather
than guarantees. The prefix is the one with a cheap fix available and no clean way to
verify it here: the tile server is unreachable from this pod, so a check against it
could be written and tested exactly once, by someone else, on a machine that can
reach it.

## Reproducibility

**Same sessions in, byte-identical HTML out.** No timestamps, no generated code,
fixed key order, component ids derived from position rather than the random fallback
`BaseComponent` uses when nobody names one. A component whose id was passed by the
caller keeps it, because the point of naming one was to have it stay.

What "same input" means narrowed when the report became a script: the guarantee is
over *(sessions + composition)* → HTML, which is what the test pins. An agent
re-authoring sessions for the same data may pick a different colour, and the HTML
legitimately differs. The script is what makes the input nameable — which is the other
reason it replaced the sidecar.

## Module layout

```
report_fast/
├── xopat.py      wire only: endpoint, DataID from a path, fragment → URL, thumbnail URL
├── session.py    XopatSession, SessionTemplate, folder/path helpers   (no FastHTML)
├── layer.py      the two layer shapes it is worth building
├── masks.py      Mask + its source (Drive / MlflowRun) → sessions, coverage, dropped
├── mlflow.py     runs in / report out; the only MLflow importer
├── core.py       Report: the page shell, BASE_CSS, the two prose fields
├── components/   SlideCard, SlideGrid
├── skill.py      locating the bundle and reading one file out of it
└── __main__.py   `reportfast skill …`
```

`layer.py` is the one file that names a shader type. That is a deliberate exception,
and `test_there_is_no_copy_of_the_viewer_vocabulary` greps for it so the exception
cannot quietly become a registry.

## Tests that exist because of a specific failure

The suite is self-contained: no server, no mount, no slide opened, no network — the
`urllib.parse` calls in it decode a fragment, they do not open one. Most of it is
ordinary behaviour. These are the ones that exist because something went wrong, and
they are the ones worth reading before changing anything:

- `test_there_is_no_copy_of_the_viewer_vocabulary` — greps the sources for a shader
  registry or a `params` allowlist. The only durable defence against the gate coming
  back one file at a time.
- `test_the_component_set_is_the_two_it_is_supposed_to_be` — asserts the exported set,
  not the files on disk, because the promise is about what someone can `import`.
- `test_a_component_is_reached_by_importing_it` — the companion to the above, and worth
  reading before writing any check that walks a class's methods: its first version was
  vacuous in the specific way such a check usually is (`callable()` is False for a raw
  `classmethod`, so a resolver's methods read as absent). See *The orphan the same
  deletion left behind*.
- `test_the_skill_never_fetches_a_ref_that_does_not_exist` — the reference tells an
  agent to fetch the layer-type list from upstream, and that repo's default branch is
  `master`. Spelled with `main` the URL returns a 404 *body*, `grep` matches none of
  it, and the pipeline prints zero names: a fetch that fails by answering "none",
  which is the one failure mode a timeout does not catch. Written while measuring the
  fetch it guards, and fired on an injected `main` URL before it was committed.
- `test_no_workflow_publishes` — greps the workflow files. A publish step is what a
  helpful person adds while trying to be useful, and it would pass review, pass tests,
  and upload. Comments are stripped first: the workflow that must not publish is the
  one that spends five lines explaining why it must not.
- `test_every_rule_ci_claims_in_a_comment_is_a_test_that_exists` — the comment above
  cited `test_no_workflow_publishes` for a while *after* that test was deleted with the
  CLI it belonged to, which is worse than no comment.
- `test_the_same_report_renders_byte_identical_html` — added back when the CLI's
  reproducibility test was deleted with it; the guarantee outlived the command.
- `test_every_api_name_the_skill_prose_names_is_public` and its snippet companion —
  writing the documentation produced three API names that did not exist and surfaced a
  fourth that did but was not exported. No other test could see either. The three
  guards they replaced split `SKILL.md` on literal sentences, so they could only fail
  on a rename; three of them were still asserting a deleted CLI because the strings
  they hunted survived the thing they described.

Every gate above was checked by introducing its violation and reverting it.

## xOpat v3 facts

Deliberately not here. They are in `references/xopat-v3.md`, written against 3.1.0 at
commit `18c94f2`, with the viewer's own source named for each claim rather than a line
number — line numbers in a document are a decay clock. The three that shaped this
design:

- The **fragment carries the payload**, so a report is a mail-able file of links and
  nothing is served. It also means a parse failure is indistinguishable from an empty
  viewer.
- **`data[]` is positional**, which is why the library mints indices instead of
  accepting them.
- **`sessionName` is a viewport cache key, not a title.** One shared value across a
  folder of slides makes them inherit each other's zoom and pan, so a 32-card report
  opens 31 times at the wrong scale.

## Runs

`mlflow.py` is the optional edge: the only module touching MLflow, imported inside
`client()` so everything else stays FastHTML-and-stdlib. In short:

- **Reading a run is listing it, not fetching it.** Each artifact name becomes
  `mflow/<experiment>/<run>/artifacts/<path>`, a DataID the tile server resolves on its
  own. Only `download()` moves bytes.
- **Writing is one call, always explicit.** `publish()` logs `report/report.html`,
  either attached to a run or into one it creates. The description goes in as
  `mlflow.note.content` because that is what the UI reads and current clients no longer
  expose `update_run_description`.
- **Two URLs, not one.** `tracking_uri` is the cluster-internal API, `web_url` is the
  link a browser opens; the original tool hardcoded the second.

Publishing is never a side effect of building. Not from a config key, not from CI, not
from a prompt that mentioned MLflow in passing. An artifact logged to somebody's run is
in the record and there is no clean way to take it back out, which is why this is the
strictest rule in the project and the only one with a grep protecting it.

Run ids that are referenced elsewhere and are not otherwise rediscoverable live in
`references/mlflow.md`, with what each one is.

## Not doing

- No server, no filters, no live state, no reactive re-render.
- No protocol *code* in sessions — inline-JS templates are rejected by the viewer
  outright; sessions name registered protocols.
- Not modelling a field is never grounds for dropping one from an authored or pasted
  config.
- No HTML, CSS or JS in a report beyond the two components and the page's own prose
  fields. A layout the set cannot express is a conversation with a human, not a
  workaround.
- No generated per-report Python, and no committed intermediate nobody asked for.
- No configuration *file* for the library. Endpoints come from the environment and
  sessions from code. This now holds with no exceptions.

## Open

- **`SessionTemplate` is public API the skill does not teach.** Its job is real —
  `bind()` is the honest answer to refilling one hand-authored session 300 times, which
  neither `case_matrix` nor a `from_slide` loop does. But it is another name this library
  exports that an agent reading the skill never meets, which is how `SessionPreset` ended
  up deleted. So either the skill gains a paragraph on when a template beats a loop, or
  the class goes and the 300-case authored shape becomes a `for` loop over `from_config`
  and `bind_name`. Unresolved because the two answers cost different things: the
  paragraph is skill length, the deletion is a capability.
- **The skill's version stamp is handwritten.** `SKILL.md` says "xOpat 3.1.0 @
  `18c94f2`" and nothing regenerates it — deliberately, since the thing it would
  regenerate from was the deleted generator. The skill therefore tells the agent to
  establish which viewer version the deployment in front of them runs rather than to
  trust the stamp. That is a procedure standing in for a check, which is the correct
  shape here but worth knowing.
- **Run discovery.** `find` went, and nothing replaces it: you need a run id already.
  A `search_runs` would be the way back, and until then the skill should not promise
  it.
- **`examples/multi_background_case.json` is our illustration**, not a copy of a real
  one — the multi-background session it models has only ever been pasted in chat.
  `viewer_export.json` and `dysplasia_case.json` are genuine.
- **End-to-end verification has never been run from this pod**, which cannot reach the
  tile server. The library is verified by unit tests and by executing its snippets
  against real MLflow metadata; no card here has been confirmed to render.
