# ReportFast — design brief

Decisions agreed with the tool's owner. This file is the contract for what gets
built; `README.md` documents what currently exists. Where they disagree, the
code plus this file win and `README.md` is stale.

## The problem being solved

The old reporting tool could not reach most of what xOpat v3 can do — a session
hand-authored by a user (backgrounds, overlay layers, protocols, per-source
options, plugins) had no way through the API, so the tool's feature ceiling was
ours rather than the viewer's. The owner's requirement is that a user who hits
our ceiling is **not** stuck.

## The agent is the user now

Agreed with the owner and the team after the dysplasia rewrite, and **widened
after the owner + colleague review**: most of this tool was written by an
agent, and the agent already carries the context xOpat needs — the mounts, the
DataID rule, which run holds which masks. So a report should be **requested**,
not coded. Nobody should have to open this repository to get one.

The line that makes that safe:

> **The agent authors the sessions and the page. The library owns the gate.**

| The agent is better at this than a config form | The library owns this, always |
| --- | --- |
| Reading a folder or a run and seeing what is there | Validating whatever session the agent wrote: dangling `data[]` references, malformed layers, viewer state |
| **Authoring the session JSON** — one xOpat window is the agent's call now | The DataID rule (mount root, `mflow/<exp>/<run>/artifacts/…`): helpers mint it, the probe enforces it |
| Choosing colours, naming layers, writing the prose | The report shell: cards, grid, thumbnails, tables, CSS — a closed, frozen component set |
| Adapting at 4pm when the mask run was redone under a new path | Whether anything gets published, and to where |

**The agent authors session JSON.** This reverses the earlier rule that it never
would. A session is one xOpat window — several slides and masks in one instance
— and the agent decides what that window holds, working from the viewer's
vocabulary shipped inside the skill (a schema plus golden examples, see **The
agent's context on xOpat**) rather than from memory.

What justified the ban is still true, which is why the gate exists: that JSON
punishes exactly the mistakes language models make, and punishes them quietly.
All four have cost us time here: a `data[]` reorder that silently points an
overlay at the wrong file, a `/mnt` left on a DataID (black card), an invented
`params` key dropped by the viewer without a word, a wrong viewer mount that
*looks* like it loaded. The answer moved from **forbid** to **gate**: everything
the agent writes still passes through `XopatSession.from_config` — hard failure
on a `data[]` reference outside the pool, a warning naming every `params` key
outside the viewer's allowlist, endpoint and thumbnail rules applied — and then
through the build-time probe. The ban made it impossible for the agent to emit
a broken session; the gate makes broken sessions loud.

## Terminology

**Say "session", not "slide".** A session is one viewer boot: N backgrounds
(each its own slide / timepoint / region), each with N overlay layers. One slide
is the most common input, not the unit. `XopatSession` is the object,
`from_slide(path)` is a convenience constructor, "one session per slide" is a
usage pattern.

## Core shape

Everything is built on one base object.

```
XopatSession            config in → config / URL out. No rendering, no I/O.
   ▲
   ├── user / AI-agent scripts        (DIY mode)
   └── SlideGrid, Report, …           (batteries-included mode)
```

1. **`XopatSession` is the base component** the whole tool is built on. It
   *creates* a session from a slide path and *accepts* one the user authored.
2. **It is a plain value object.** No FastHTML import, no file I/O, no
   rendering. Nothing above it is load-bearing for it — a script that imports
   only `XopatSession` must keep working when the report layer changes.
3. **Components above it take sessions, never paths.** That seam is what lets
   the same session feed a grid, a table, or hand-rolled HTML.

### Two modes, one pipeline

| Mode | Entry | Intermediate file | Who writes HTML |
| --- | --- | --- | --- |
| **Prompt (default)** | user asks an agent; the agent writes sessions + composes frozen components | **none persisted** — scratch files in a temp dir are the agent's working memory, not an artifact | us, through frozen components |
| **Manifest (opt-in)** | a YAML file describing the report → `reportfast build r.yaml` | the file, **when the user asked for it** | the same code |

The manifest was the plan; it is now a **feature of the default, not the
default**. A colleague's requirement, in the review: *by default the user prompts
the agent and the agent produces the report* — no intermediate file exists
unless someone asks to keep one. The pipeline is identical underneath: sessions
→ frozen components → HTML. A report can still end as a committed file (say so,
and the agent saves the spec it already had), but persistence is a request, not
a tax.

DIY — `from report_fast import XopatSession, SlideGrid, …` in your own script —
stays first-class, and is in fact what "the agent composes the report" means in
practice: the agent drives the same public API a human would, so there is one
set of guarantees for both.

### The paste path

A user config/link is taken **verbatim**. We do not normalise away fields we do
not model, and we do not reject keys we do not recognise — that is the whole
point of the feature. We only renumber `data[]` indices when grafting something
onto it; otherwise we do not touch it.

### The default path

No config → built-in defaults, so passing a slide (or a folder of them) is
enough to get a report. Users must not need to know a session exists.

## Locked decisions

| # | Question | Decision |
| --- | --- | --- |
| 1 | Does a session render itself, or is it config + a card? | Spec **and** card: the session is a value object, `SlideCard(session=…)` renders it |
| 2 | How do reusable templates bind slides in? | Slots + index binding — `SessionTemplate.from_config(cfg, slots={0: "image"}).bind(image=path)` |
| 3 | What shape do the defaults take? | A **session-shaped** preset file (real keys, no invention) overridable by kwargs |
| 4 | Static file or served app? | **Static only.** State, filters, and the FastHTML server go away |
| 5 | Where do masks come from? | A `Mask` names a layer and carries its own **source** — `Drive` folder or `MlflowRun` artifacts — so the report call reads like the job: a background, then these masks over it |
| 6 | Does the agent write the report, the code for it, or the manifest? | **It writes the sessions.** Session JSON is the agent's output now, gated through `from_config`; the frozen component set is the only HTML anyone gets. The manifest is an opt-in persistence format, not the interface |
| 7 | What if xOpat can do something the library does not model? | **Constrain construction, never vocabulary.** The agent writes the field into the session; `from_config` keeps it. The only construction rule: `data[]` indices are minted by `add_data()`, never typed — see "The component set is the contract" |
| 8 | What is the durable artifact of a report? | The HTML, plus a **sidecar `provenance.json`** written beside it (gitignored; a publish logs it in the manifest's place). Ruled in review: **nothing is stamped into the page** — no provenance footer. A mailed HTML therefore carries no provenance with it; accepted knowingly |
| 9 | Can two runs of one manifest differ? | **No.** Same manifest, same bytes. Asserted in a test, so no timestamp and no generated code creeps in |
| 10 | Who may create report HTML? | **Nobody but the frozen component set.** The agent composes reports *from* components and may not emit page HTML, CSS, or JS of its own. **Enforced by code, ruled in review:** composition arriving on the agent path (`--sessions-dir`) rejects `raw_html` and any inline HTML; a manifest may still use it, because a manifest is a human having touched a file. Same principle for sessions: out-of-allowlist keys are *errors* on the agent path, warnings for pasted human configs |
| 11 | How does the agent know xOpat's vocabulary? | From the skill's bundled `references/` + `schema/` + golden examples, regenerated from the pinned viewer source — not from model memory, not from GitHub at build time. The machine-checkable half (the schema) is generated; only the lore is handwritten |

## The workflow is the API

Most reports here are the same job: a folder of slides, ten overlay directories
scattered across four runs, a list of cases worth looking at. `masks.py` makes
that one expression instead of one hundred lines of plumbing, because the
plumbing was the complaint about the original tool, not its YAML.

- **A source answers one question**: `index() → {case stem: DataID}`. `Drive`
  walks a mount, `MlflowRun` lists a run's artifacts, and a source of your own
  is those four lines. Nothing else in the module knows where files came from.
- **Stem is the join.** `case_001.svs` and `case_001.tiff` are the same case —
  the rule the original tool used, inherited on purpose.
- **`only=` is the report order**, `min_layers=` the case filter: the config's
  `selected_items` and `min_layer_count`, with the same meaning.
- **A layer no case gets stops the build.** A mistyped run id used to produce a
  report with one fewer column and no indication of it.
- Sources are read once per report, however many masks share them, and a
  `Mask` holds only drawing state — the same `Mask` object over two runs is a
  different layer, so frozen dataclasses are enough and there is no registry.

## The prompt is the interface; the manifest is an option

The default flow has no file in the middle:

```
user prompt → agent discovers data → agent authors ONE session design
            → instantiated per case (SessionTemplate.bind — the loop, in code)
            → agent composes frozen components
            → reportfast builds from a temp dir: validates, writes, probes → HTML + provenance.json
```

Nothing is persisted except the HTML and its provenance sidecar (the plan is
printed, not written). The agent keeps scratch JSON in a temp dir while it
works — that is its notepad, not an artifact. A report becomes durable by being
**published** (or by the user saving the HTML), not by an intermediate existing
on disk. The door the sessions arrive through — `--sessions-dir`, same gates as
a manifest — was ruled in review over in-process library calls precisely so the
validate/plan/probe/exit-code gate is unavoidable rather than reimplemented per
caller.

**Why this changed.** The manifest used to be the interface — "the agent writes
YAML, runs one command." It is now one way to persist intent, chosen by the
user, because the colleague review asked for the prompt itself to be the entry
point: reports are requested in conversation, and making every one of them
produce a file nobody asked to keep is ceremony. Everything the manifest mode
still provides is unchanged and still valuable:

```
report.yaml   →   manifest.py (written once, tested, ships)   →   build_report()   →   HTML
   data                    library code                            the same call a
 opt-in                                                          human writes
```

### One session *design*, instantiated N times

Ruled in the review, and it resolves the volume worry without conceding it:
**the session JSON always comes from the agent's hand** — even for a grid of 300
slides. What the agent writes is **one** session document as the design of the
window (its layers, colours, bindings, params); the 300-case grid is that
document iterated, the slide path swapped per case. Nothing generates the
*shape*; the library only substitutes which file an index points at.

This is `SessionTemplate` (decision 2, already built) with its actual purpose
named: `from_config(cfg, slots=…)` + `bind()` is the for-loop the review
described, with the one part an LLM should not do by hand — renumbering `data[]`
references correctly 300 times — done by code. In prompt mode the flow is: the
agent authors one session (or one template), instantiates per case, and hands
the resulting directory of session files to the CLI door. The 300 instantiated
files are temp-dir scratch like everything else; the *design* is the authored
artifact. If the user wants it kept, that is the moment a file is persisted.

`manifest.py` remains exactly that mechanism when a file *is* wanted — a report
that runs monthly, a report a human wants to diff and correct, a report whose
recipe belongs in git. Saying "keep this one" costs the agent one extra write of
the spec it already had in hand. And the anti-goal stands: **no generated
per-report Python.** Improvised scripts are still the thing we cannot check;
what changed is that the checked thing can now be the sessions plus a
transcript, not only a YAML file.

```yaml
# reports/dysplasia.yaml — manifest mode: the whole dysplasia job, minus mask rows
title: Dysplasia report
subtitle: Tile-level QC overlays and dysplasia annotations

background: {drive: /mnt/data/IKEM/colon/IBD_AI/dysplasia}
# background: {run: 970842…, path: slides}     or a list: [a.svs, b.svs]

masks:
  - {name: Tissue, run: 970842…, path: tissue_masks, color: "#ffff00", opacity: 0.5}
  - {name: Annotations, run: 41d5e1…, path: annot_masks, classes: 3,
     palette: ["#ffffff", "#ff0000", "#00ff00"], breaks: [0.25, 0.75], mask: [0, 1, 1]}
  - {name: epithelium, drive: /mnt/projects/…/downscale, visible: true}

only: cases.txt          # a file of case ids, or the ids themselves
min_layers: 3

endpoint: {image_protocol: wsi_service}
intro: notes/overview.md # string, list, or a .md/.txt path, relative to the manifest
blocks:
  - prose: notes/methods.md
  - chart: figs/roc.png

publish: e313fc2a62d54057b41b46057577c75d   # where it belongs; does not write
```

**The manifest's own keys are validated strictly** — an unknown key stops the
build and names the close matches. This is the opposite of the paste path, and
deliberately so: an xOpat field we do not model must survive, while a manifest
key we do not model means somebody's `min_layer:` quietly did nothing. Strict
about our vocabulary, verbatim about the viewer's.

**The gates stay three, whatever the input was.** The gate — resolve, print,
write one file, probe, and *only then* allow `--publish` — is the valuable part,
not the YAML. Prompt mode needs it as much as manifest mode did, so the CLI
grows a non-file input for the sessions the agent just authored (a temp-dir
notepad is not a persisted artifact):

| | | |
| --- | --- | --- |
| `reportfast plan r.yaml` | resolves sources, prints cases per case, layers per case, per-source file counts, warnings | writes nothing |
| `reportfast build r.yaml` | writes the HTML, probes every DataID against the tile server | one file, gitignored |
| `reportfast build --sessions-dir /tmp/x/sessions --layout grid -o /tmp/x/report.html` | the same gate, prompt mode: validate every session through `from_config`, compose the grid, probe | same one file; no manifest exists |
| `reportfast build r.yaml --publish` | logs `report/report.html` plus the resolved inputs — `manifest.yaml` + `plan.json` + `provenance.json`, or `provenance.json` alone when there is no manifest | **the only write to MLflow, never implied** |

`plan` exists because an agent needs to be able to check itself without
producing anything, and because "32 cases, 11 overlays, 0 cases short" is the
thing worth reading before a build, not after. `--json` makes it machine-readable
for the same reason. In prompt mode its equivalent is the session validation
warnings plus the composition summary, and the rule "read them before claiming"
is the same one.

**The tension, named:** prompt mode means something per report *is* generated —
the sessions, plus a scratch composition note. What stays unbent is that every
byte of report HTML and every invariant comes from the library, and that nothing
generated by an agent becomes a committed artifact without someone asking. If
"no generated Python" is kept literally, the CLI above is the only door and every
session must arrive through it; that question is under **Open**.

**What is kept, in manifest mode** (in prompt mode the only artifact is the
HTML plus its provenance block/sidecar):

| | | |
| --- | --- | --- |
| `reports/x.yaml` | **committed** | the intent; hand-editable forever |
| `reports/notes/*.md` | **committed** | prose as prose, so a reworded paragraph diffs as one |
| `reports/x.html` | gitignored | build output |
| `report/manifest.yaml`, `report/plan.json` | in the run | what actually ran |

The committed manifest stays short: no expanded case lists, no resolved DataIDs,
no absolute paths the agent happened to see. Resolved detail belongs in the copy
logged to the run. The reason is concrete — the report published on run
`ad84f424…` carries 974 sessions while the config on disk says 32 cases with
`min_layer_count: 3`, because the resolved config logged beside it predates those
settings. Log the resolved manifest and the run answers "what is in this report"
forever; keep the declared one in git and it answers "what did someone mean".

## The agent's context on xOpat

The agent authors sessions, so the skill has to carry the viewer's vocabulary.
Two options were on the table for keeping that context — a link to the xOpat
GitHub repo, or a written summary in `SKILL.md` — and the answer is **neither
alone, because both rot**, in different directions:

- A link is not context. At build time nothing reads it, and when something
  does, the answer is one web fetch and a guess away. It belongs in the skill as
  a stamp — *"these facts were generated from xOpat `v3.x.y`, commit `abc1234`"* —
  so a human can re-derive the context when the viewer moves. Not as the source of
  truth. **Amended by decision 8:** the stamp lives in `schema/viewer.lock.json`
  and in each report's provenance sidecar, *not* in the page — the footer that used
  to be proposed here was ruled out. The skill's copy of the stamp is generated
  into `SKILL.md` from the lock, so it cannot drift from the gate.
- A handwritten summary is context that rots silently. `SKILL.md` prose is
  updated by whoever remembers; the viewer schema is updated by the build. A
  summary drifting a field behind is precisely the failure we are trying to
  escape: the agent confidently writing `dataReference` on a shader because a
  doc said so.

So the skill's context is split by *how it can go wrong*:

```
skills/reportfast/
├── SKILL.md              procedure + hard rules (handwritten, short)
├── references/           lore: what a wrong mount does, what fails quietly
│                         (handwritten — a generated file cannot know this)
├── schema/               GENERATED from the pinned viewer source
│   ├── session.schema.json    the v3 session shape, machine-checkable
│   ├── params-allowlist.json  params/layer/plugin keys the viewer keeps
│   ├── layer-fields.json      every shader field, by family
│   └── viewer.lock.json       viewer version, commit, source digests
└── examples/             golden sessions: the viewer export, the
                          multi-background case, one per common pattern
```

The machine-checkable half is a `scripts/derive_schema.py` run against the
pinned checkout at `/home/jovyan/xopat` (the local source of truth; upstream
GitHub is the fallback), emitting the schema, the allowlists, and a stamp with
the viewer version and commit. `report_fast` validates against the same lists in
Python — **the skill and the library are generated from one place**, so they
cannot disagree. Golden examples stay curated by hand: `from_config` accepts
whatever a real viewer exported, and the best defence against a plausible-but-
wrong session is three sessions that are provably right to imitate.

Refresh rule: re-derive when the pinned viewer moves; a stale stamp prints one
line at build time. Staleness is then visible instead of load-bearing.

## The component set is the contract (frozen)

> **The agent composes; the library renders.**

Report HTML comes from a closed set of components and nowhere else:

| Component | Renders | The agent decides |
| --- | --- | --- |
| `SlideCard` | one session as a card: thumbnail, name, viewer link | which session, what name |
| `SlideGrid` | cards in a grid — N slides from a folder, or N pasted sessions | which cards, order, columns |
| `Prose` | paragraphs of markdown | the words |
| `Heading` / `Bullets` | structure | the words |
| `LinkList` | links out (runs, protocols, next report) | labels and URLs |
| `MetricTable` | a table of metrics | which run, which rows |
| `Chart` | one image, captioned | which image, the caption |
| `Section` | a titled group of blocks | grouping and titles |
| `Report` | the page shell: order, theme, CSS | title, subtitle, block order |

Frozen means: **the agent may not emit page HTML, CSS, or JS.** A report is a
`Report` composed of those blocks, so two reports on unrelated topics share
typography, card shape, spacing, and the same class names, and the reproducibility
guarantee covers the page and not just the sessions. One session in one card, or
forty sessions in a grid — that range of expression is exactly the "it's up to
the user how they use them" the review asked for; inventing a new widget is not.

Consequences, stated honestly:

- **A layout the set cannot express is a library gap.** The answer is a PR
  adding a component, with a test, not an agent improvising markup. `RawHtml`
  stays in the library for a human filling a hole — it is explicitly *not* an
  agent door, and the skill rulebook says so. This is the load-bearing decision
  in this whole section: freeze the components or "the report looks the same" is
  a wish.
- **Unmodelled session features are *not* frozen.** The old three doors collapse
  into the main path now that the agent authors sessions: any field the viewer
  declares can appear in a session the agent writes, and `from_config` keeps it.
  What remains is one rule — `data[]` indices are only ever minted by the
  library (`add_data()` returns the index it appended), never typed.
- **Nobody checks a field we have never seen.** We can warn that a key is
  outside the derived allowlist; we cannot know it means what the author meant.
  Off-allowlist fields ship with "open this one in the viewer yourself."

The escalation rule survives: a pattern of off-allowlist fields used twice
becomes a modelled field with a test, and if the sessions are being kept (the
manifest path) "used twice" is something you can actually grep for.

## Shipped next to the code: the skill

The agent-facing half of the tool lives in `skills/reportfast/` in this repo, so
the procedure, the viewer context, and the code version travel together.

**It ships inside the wheel.** `pyproject.toml` force-includes `skills/reportfast/`
as `report_fast/skill/` (and `examples/` as `report_fast/examples/`, since a
procedure that says "copy an example" has to arrive with the example), and
`report_fast/skill.py` finds it from either layout — the wheel path, or the
checkout two directories up, which is what an editable install sees.

Placing it is a separate, explicit command, and that is a packaging constraint
rather than a design preference: an install is not allowed to write outside the
environment it installs into, so no `uv add` can put a skill where an agent looks.

```
reportfast skill show                 # print it; touches nothing
reportfast skill install              # -> ~/.claude/skills  (every project)
reportfast skill install --project    # -> ./.claude/skills  (this one; gitignore it)
reportfast skill where                # bundled path + what is installed; the "why
                                      # didn't the agent use it" question
reportfast skill show --reference examples/dysplasia_case.json
```

Personal is the default because the question an agent asks is rarely about one
repository, and because a project install creates `.claude/skills/` that then has to
be ignored or committed — that repository's decision, not this package's. An
existing install is refused without `--force`: overwriting a skill someone edited
silently is how a hand-fixed procedure disappears.

`reportfast skill install --dest DIR` is the only way these commands write, and
`tests/test_skill.py` uses it exclusively — a test that installed into the
developer's own `~/.claude/skills` would change which skills their next session
sees. The same file asserts the force-include targets against `pyproject.toml`,
because a renamed target is otherwise silent: the checkout keeps working, the
install does not, and only someone in another project finds out.

The bundle's commands are held to one rule, tested: every command `SKILL.md` tells
an agent to run has to work *from an install*. It used to say "check it from
anywhere: `uv run python -c …`", which is true in this repo and false in every
project that installed the library — i.e. exactly where the sentence matters.
Re-deriving the contract (`scripts/derive_schema.py`) stays a repo step and the
skill now says so.

Whether the skill reaches *other* agent CLIs (the previous text named
`qwen extensions install`) is unverified from the machine this was written on — no
qwen binary, and discovery paths differ per tool. Verified here: Claude Code's
`~/.claude/skills` and `./.claude/skills`.

The skill's layout (schema, examples, references) is in **The agent's context on
xOpat** above; the tree below is the procedural half.

```
skills/reportfast/
├── SKILL.md            four questions to ask, the flow, the hard rules
├── references/
│   ├── xopat-v3.md     fragment is the payload, data[] positional, sessionName
│   ├── deployment.md   /v3/ vs /xopat/, tile root, /mnt → DataID, mflow/ prefix
│   └── hydra-v2-to-v3.md   retriever → Mask row, classify → colormap
├── schema/  examples/  GENERATED: see derive_schema.py, stamped with a commit
├── manifest.example.yaml   (the opt-in persistence format, documented not default)
└── scripts/            the CLI wrappers, thin by design
```

`references/` carries only what is **not derivable from the code** — deployment
lore, viewer behaviour, the v2 mapping. Anything a generator can emit belongs in
`schema/`; anything a `read_file` answers is not written down twice, because
twice is twice that rots.

The rules in `SKILL.md` are the ones that came out of real failures here. One
of them died in the review — *"never write session JSON"* — and its replacement
is the point of this document:

- **Discover, never guess.** Run ids and paths come from a listing or from the
  user. A hallucinated run id produces a missing layer, not an error.
- **Author sessions from the shipped schema and examples, never from memory.**
  The vocabulary lives in `schema/` and `examples/` for a reason: every quiet
  v3 failure — the singular `dataReference`, the dropped `params` key, the
  reordered `data[]` — is a field-level detail, and field-level details are
  exactly what memory gets wrong. Let `from_config` be the gate: warnings read,
  fixed, retried.
- **Compose only from the component set.** No page HTML, CSS, or JS of your own;
  a layout the set cannot express is a library request, not an improvisation.
- **`plan`/validate before `build`, and show the plan.** The plan is the review
  artifact.
- **Probe before claiming.** A report whose DataIDs were not resolved is a
  report nobody has checked.
- **No intermediate file unless the user asks for one.** Scratch in a temp dir
  is fine; `manifests/` and `reports/` are for kept things. When the user does
  ask to keep a manifest, write the spec you already have — and edit that file
  forever after, never regenerate it, or the human's hand edits die on the next
  "actually, add the blur masks".
- **`--publish` is asked for, out loud, with the run id repeated back.**

## Provenance without a manifest

With no intermediate file kept, "what is in this report" still has to be
answerable — from the file beside the HTML. Ruled in review: **the page itself
stays untouched** (no footer block; the report looks the same whether it says
anything about its inputs or not), and provenance lives in a sidecar:

- `report.provenance.json` next to the HTML, gitignored — sources (folder paths,
  run ids), endpoint, viewer schema stamp, tool version, and, when the build
  came through a template instantiation, the **session design as authored** (one
  document, not 300 instantiations). What the sidecar can never contain is
  anything the CLI did not see: the agent's reasoning stays in the transcript,
  which is why "keep the spec?" remains a question worth asking once;
- **`design` is a claim the caller makes, not a fact the tool derives.** The field
  existed, `provenance.design_of()` was correct, `record_provenance` passed it —
  and nothing ever set it, so every sidecar on disk said `design: null`. The
  reason was structural rather than careless: by the time a page is composed the
  library cannot know which design its sessions came from, because a bound session
  carries its DataIDs and no note of its parent. So `build --design` names it
  (ruling, 2026-09-15), gated as authored on the way in, with `--slot` recorded
  because the same JSON bound two ways is two different reports. It **binds
  nothing** on either command — that ruling predates the flag and survives it,
  since a flag that could express the loop would need the case-list file the
  default flow was ruled not to leave behind. What the tool cannot check is whether
  the folder really was bound from that design, and the record does not pretend to;
- a publish logs the sidecar beside the report when there is no manifest to log —
  and logs it *alongside* `manifest.yaml` + `plan.json` when there is one, since
  the three answer three different questions (what was meant / what resolved / what
  the links actually point at); the sidecar is in the manifest's *place* only in
  the sense that it is what a manifest-less build has instead;
- the skill's own version stamp (*"schema derived from xOpat `v3.x.y`, commit
  `abc1234`"*) lives in that sidecar and in `schema/`, not in the page.

The accepted trade-off, stated so nobody rediscovers it: a **mailed HTML file
carries no provenance** — the sidecar does not travel with it. If a report must
be self-explaining wherever it goes, that is the moment to ask for a manifest or
to deliver the pair.

This replaces the manifest-as-record role. The rule that made the old tool's
runs unanswerable — the published report and the logged config disagreeing
because one was resolved and one was declared — is handled by logging the
*resolved* thing (the sidecar is generated from data that already resolved)
rather than a declaration.

## Reproducibility

**Same input, byte-identical HTML.** No timestamps in the output, no generated
code, fixed key order, component ids derived from position rather than chance.
There is a test for it (and a real one: `manifests/dysplasia.yaml` builds to one
sha256 across repeated runs), because the moment that property is
convenient-to-break, reports stop being build artifacts and go back to being
transcripts somebody trusts.

What counts as "same input" changed with prompt mode, and worth stating
plainly:

- **The page is reproducible by construction.** Frozen components + fixed key
  order + no clock means the serializer cannot vary. This is exactly the
  guarantee the review asked for — the report looks the same every time — and it
  no longer depends on a manifest existing.
- **The sessions are inputs, and they vary.** An agent re-authoring sessions for
  the same data may pick a different colour or name a layer differently, and the
  HTML will legitimately differ. Reproducibility is asserted over
  *(sessions + composition)* → HTML, which is what the tests pin.
- **Therefore the provenance sidecar matters more, not less.** With no manifest,
  `provenance.json` is what makes "same input" answerable at all. A report whose
  sessions nobody saved is reproducible in *form* only; if someone wants it
  rebuildable, that is the moment to ask for a manifest — or at least for the
  session design to be kept next to the sidecar.

## Construction and merge rules

**Precedence:** builtin → preset file → pasted config → kwargs.

- `params` **deep-merges** (it is a settings bag).
- `data` / `background` / `visualizations` / `plugins` **replace**. Lists that
  carry index references are never merged implicitly.
- Combining two sessions is an explicit `merge()` that renumbers — never a
  silent deep merge.
- `data[]` is append-only through one helper that returns the index it appended.

**Import flags:**

- `strict=True` raises on unknown/retired fields; `strict=False` (default) warns
  and keeps them — real exports carry things like `lossless: false` and
  `visualizationIndex: null` that must survive.
- `drop_state=True` strips viewer session state (`viewport`,
  `activeBackgroundIndex`, cache blobs) so a pasted config does not dictate
  where a new report opens.

**Round-trip contract:** `from_config(s.to_config()).to_config() == s.to_config()`.
If this leaks, the paste path leaks.

**Templates must preserve authored shader keys** (`dose`, `rtstruct`, …) —
`visualizations[].order` references them by name.

## Module layout

```
report_fast/
├── xopat.py        wire only: session JSON → fragment → URL, param allowlist, validation
│                   (allowlists generated — see derive_schema.py, below)
├── session.py      XopatSession, SessionTemplate, sessions_from_folder   (new, no FastHTML)
├── masks.py        Mask + its source (Drive / MlflowRun) → sessions      (new)
├── config.py       preset loading (builtin → file → kwargs)              (new)
├── mlflow.py       runs in / reports out; optional import                (new)
├── core.py         Report.write(path) owning the HTML shell              (was: serve/to_fasthtml_app)
├── build.py        build_report(): sessions and blocks → report, in one call
├── manifest.py     YAML spec → build_report; strict keys, plan, resolve  (opt-in persistence)
├── compose.py      the spec prompt mode writes: sessions + layout →      (new)
│                   the same build_report call, no YAML on disk
├── verify.py       every DataID → tile server /info, before anyone opens (new)
├── __main__.py     `reportfast plan|build|find`, file input or --sessions-dir
└── components/     THE FROZEN SET: slide_grid, prose, metrics, chart +   (locked — see
                    heading, bullets, links, section, report                decision 10;
                    (provenance is a sidecar file, not a page component)     raw_html: human-only)

skills/reportfast/  SKILL.md + references/ + GENERATED schema//examples/ —
                    the agent's half of the tool, versioned with this code
manifests/          reports someone chose to keep, as YAML (no longer the default)
```

`state.py` is deleted, along with `Report.state`, `BaseComponent.bind_state` /
`render_with_state`, `Report.to_fasthtml_app` and `Report.serve`.
`build_session()` stays as a thin wrapper over `XopatSession.from_slide` for one
release.

Output is always a static HTML file: inline CSS, base64/inline-SVG charts, zero
external JS. Description / metric / chart blocks are components like any other.

## Build order

1. `session.py` — spec + `from_slide` / `from_config` / `from_url` / `to_config`
   / `url` / `thumbnail` / `add_layer`. Golden files from two **real** configs
   (a viewer export and the multi-background `wsi_service`/`nifti` case),
   reference-integrity + round-trip tests.
2. `SessionTemplate` + `sessions_from_folder` + `SlideCard`.
3. `Report.write()` + promote the components out of `test_report.py`.
4. `config.py` presets, then delete the state/server layer.

Preset files may carry `params`, `plugins`, layer defaults, `protocol`,
`options`. They must **refuse** `data` / `background` / `visualizations` — those
come from the slides or from a pasted config, never from a default.

All four phases are in place. What shipped beyond the list: `build.py`
(`build_report`, `sessions_for`) for the paths-in-report-out path,
`as_session()` as the single coercion door every component goes through,
`Section`, the CLI with its three gates and exit codes, and the skill bundle.
The state/server/resolver layer and the components that needed it were held in
`_legacy/` — "deleted in spirit, kept on disk only because this directory is not a
git repository." It is a git repository now, so the directory has been deleted
properly; the layer is gone, not parked. Anything that needed a live server or a
resolver state has no home here, and the read-only model is the whole design.

### Build order, round 2 (the review refinements)

All four review questions are ruled (provenance = sidecar only, never the page;
frozen set enforced by code; session *design* always authored by the agent and
iterated by `SessionTemplate`; prompt mode enters through the CLI's temp-dir
door). Ordered by what unblocks the agent; each step ships alone.

Status as of this writing: **0–5 are in** (git, `derive_schema.py`, the session
gate, the frozen set, `compose.py` + the door, the sidecar), 6–7 are the docs
catching up. Steps 1–5 are each pinned by their own test module
(`test_contract` / `test_audit` / `test_frozen` / `test_compose` / `test_provenance`)
plus CLI tests for the door.

0. **`git init`** — the repo is not one, and decisions 8/10 and the skill's
   whole "committed manifest" story assume it. Plus `.gitignore`
   (`.venv/`, `__pycache__`, `reports/*.html`, `*.provenance.json`). Prerequisite,
   not a feature; it is what let `_legacy/` be deleted properly instead of parked.
1. **`scripts/derive_schema.py`** — Python-parse the pinned viewer source (no
   node on this pod, so `ts-json-schema-generator` is out; the session-document
   types are `src/types/app.d.ts` + `XOpatSetup` in `config.d.ts` — *not*
   `session.d.ts`, which is live-collaboration; see Open) →
   `schema/session.schema.json`, `params-allowlist.json`, layer-field lists, and
   a `viewer.lock.json` stamp (pin: `18c94f2b`, v3.1.0). The hand-maintained
   allowlist in `xopat.py` stays as the generation's **diff-check** until the
   derived lists provably cover it. Wire the artifact into both the Python
   validation and the skill — one source, two readers. Everything else trusts
   this.
2. **Session gate hardening** — `from_config` against the derived schema, with
   the two-mode severity ruling: **agent path = strict** (out-of-allowlist key →
   error, message naming the full JSON path `visualizations[0].shaders.foo…`;
   off-allowlist means a typo until proven otherwise), **paste path = warn and
   keep** (verbatim survival is the feature). `drop_state` on by default for
   authored sessions; `data[]` reference validation stays hard for both. Refresh
   golden exports into `skills/reportfast/examples/` — including the real
   multi-background session the current fixture only illustrates (input needed
   from the team).
3. **The frozen set, enforced** — audit `core.py`/`components/` against decision
   10 so nothing in the page comes from anywhere else; the component table in
   this file becomes the documented API. Agent-path composition rejects
   `RawHtml` *by code*; manifests (human-touched files) keep it. No `Provenance`
   component — ruled out of the page.
4. **`compose.py` + the temp-dir door** — a composition spec in memory (which
   sessions/templates, which components, what order) and `reportfast build
   --sessions-dir … [--layout …] [-o …]`: validate every session through the
   step-2 gate, print the plan, write one HTML file, probe, exit codes — the
   same gates as a manifest, persisting nothing. This is the door prompt mode
   was ruled to enter through.
5. **Provenance sidecar** — `report.provenance.json` written on every build
   (sources, endpoint, schema stamp, tool version, the authored session design
   when a template was used); publish logs it in the manifest's place. The page
   itself stays untouched. **Done** (`report_fast/provenance.py`): `--publish`
   reaches the manifest-less door too, and the refusal that used to be there is
   gone; `--run` without `--publish` is still a usage error, since naming a
   destination is not asking to write to it.
6. **SKILL.md rewritten** for the new contract: author one session design from
   `schema/` + `examples/` and instantiate with `SessionTemplate`; compose only
   from the frozen set; strict-gate warnings don't exist for you — errors do;
   no persisted intermediates unless the user asks (and then edit, never
   regenerate); the old "never write session JSON" rule replaced by the gate's
   rules.
7. **README refresh, again** — it currently documents manifest-as-default;
   rewrite after step 4 exists, once, rather than twice.

## xOpat v3 facts this design leans on

Verified against the viewer source; re-derive only if the viewer moves.

- **The mount selects the major version.** This host serves v2 at `/xopat/` and
  v3 at `/v3/`. A v3 session handed to the v2 mount *looks* like it loaded —
  wrong base fails quietly, so pin the base URL against a known-good link.
- **The fragment is the payload.** `parse-input.js` reads POST → hash →
  `?visualization=` → localStorage cache. Only `background[].dataReference`
  validity is hard-validated at parse time; unknown `params` keys are dropped
  **silently** (`sanitizeAgainst` in `src/app.ts`), which is why we reject them
  in Python instead.
- **`data[]` is positional.** Every reference into it is an array index, so
  reordering silently rewires overlays. Binding:
  `visualizations[background[activeBackgroundIndex[k]].visualizationIndex]`.
- **`sessionName` is a persistence key, not a title.** The cached-viewport key
  is `viewport:${sessionName}:${bgId}`. One shared `sessionName` across a folder
  of slides makes them inherit each other's zoom/pan. Human text goes in
  `background[].name`.
- **`options.plugin` only reaches `/info`** when the protocol entry declares
  `tileSourceClass`; otherwise it lands too late to matter for discovery.
- **`imageSmoothingEnabled: false`** → `gl.NEAREST` sampling, for integer label
  maps. Only FlexDrawer honours it.
- **`options.format: "png"`** is v3's replacement for v2 `lossless: true`;
  `background[].protocol` is v2 and deprecated in favour of the
  `DataOverride.protocol` on the referenced data entry.
- **`background[].id`** is a stable handle (derived from the data path if unset)
  feeding `viewer.uniqueId`; naming it keeps state across slot reordering.
- **The tile server is its own mount.** This deployment's
  `slide_protocols.wsi_service` entry resolves to
  `…/wsi-service/v3/slides/info?slide_id=…`, and the card's picture comes from
  `…/v3/slides/thumbnail/max_size/{w}/{h}?slide_id=…` on that same root — the
  viewer's own `getThumbnail()`. It is *not* under the viewer's `/v3/`, so
  `XopatEndpoint` carries both roots; a wrong one 403s at nginx and the card
  just shows nothing.

## Runs

`mlflow.py` is the optional edge of the package: the only module that touches
MLflow, importing it inside `client()` so everything else stays stdlib-and-FastHTML.

- **Reading a run is listing it, not fetching it.** `list_artifacts` gives names;
  each becomes `mflow/<experiment>/<run>/artifacts/<path>`, which is a DataID the
  tile server resolves on its own. `slides()` returns those strings, `masks()`
  returns a `layers_for` pairing masks with slides by file stem — the original
  tool's rule — and `metrics()` returns what `MetricTable` already takes. Only
  `download()` moves bytes, for artifacts read as data rather than shown.
- **Writing is one call, always explicit.** `publish()` logs `report/report.html`,
  the original layout: attached to a `run_id`, or into a run it creates in
  `experiment_name` with the name/description/user the old storer set. The
  description goes in as `mlflow.note.content` because that is what the UI reads
  and because recent clients no longer expose `update_run_description`.
- **Two URLs, not one.** `tracking_uri` is the API (cluster-internal), `web_url`
  is the link a browser opens. The original tool hardcoded the second.
- **Silent-empty is a bug worth losing.** The old retrievers returned an empty
  frame for a missing artifact directory and reported zero slides; `data_ids()`
  raises and names what it did find.
- **A Hydra config is transcribed, not parsed.** No YAML loader, no `_target_`
  instantiation: `mask_retrievers` becomes a table of layers in a script, one
  row per retriever, keeping the order, the colours and the visibility. The v2
  `classify` block maps onto v3 `colormap` one field at a time
  (`classes`→palette steps, `colors`→`custom_colormap.default`,
  `threshold.breaks`/`mask`→`threshold`), so a config stays reviewable as a
  diff. `scripts/dysplasia_tile_masks.py` is that transcription, and the reason
  the mapping is exact: a report re-run against a redone mask run has to look
  like the one it replaces.

Not ported from the original: reading artifacts off a local mount (this pod has
no `/mflow`, and the API path works), the Hydra conf logging (a plain
`extra_dir=` slot instead), `get_conf.py`, the retrievers nothing configured
any more, and a pinned web host.

## Not doing

- No server, no filters, no live state, no reactive re-render.
- No protocol *code* in sessions — inline-JS protocol templates are rejected by
  the viewer outright; sessions name registered protocols.
- Not modelling a field is never grounds for dropping it from an authored or
  pasted config. The agent's sessions are the widest vocabulary in the system.
- **No HTML outside the component set — on the agent path.** Composition
  arriving through `--sessions-dir` is rejected by code if it contains
  `RawHtml` or inline markup (decision 10, ruled). A human editing a manifest
  may still reach `RawHtml` as an explicit stopgap; that is the one seam, and it
  is guarded by the file having a human's name on it.
- **No generated per-report Python, and no committed intermediate nobody asked
  for.** Sessions are data through the CLI's door, not a script that rebuilds a
  report from scratch on every run.
- **No persisted intermediate by default.** The prompt is the interface; a file
  exists because someone asked to keep it.
- Publishing is never a side effect of building. Not from a manifest key, not
  from CI, not from a prompt that mentioned MLflow in passing.

## Open

Ruled in the August 2026 review, recorded here so the rulings are findable:
provenance is a **sidecar only** (decision 8, footer rejected); the frozen set
is **enforced by code** on the agent path (decision 10); sessions are always
**agent-authored as one design**, instantiated by `SessionTemplate` rather than
written 300 times; prompt mode enters through **`--sessions-dir`**, not
in-process calls. What is actually still open:

- **Derivation completeness.** Method is forced — this pod has no node/npm, so
  `derive_schema.py` Python-parses the `.d.ts` files; the session-document types
  are `src/types/app.d.ts` (`DataID`, `DataOverride`, `BackgroundItem`,
  `VisualizationItem`, `VisualizationShaderLayer`) plus `XOpatSetup` in
  `config.d.ts` — **not** `session.d.ts` / `src/SESSION.md`, which are the
  (disabled) live-collaboration feature and a trap for the unwary implementer.
  Pin: xopat @ `18c94f2b`, v3.1.0. Still open: whether the parser covers
  `sanitizeAgainst`'s actual runtime behaviour (it drops keys the *type* allows
  but the *registration* lacks — the schema must encode the registration, not
  the type) and how the diff-check against the hand list graduates to
  replacement. `viewer.lock.json` as the pin's home is assumed, not approved.
- `find` needs a real `search_runs` behind it before the skill can promise run
  discovery; today the CLI only lists artifacts of a run you already have.
- Per-layer `smoothing=` / `plugin=` / `quality=` / `channels=` / `microns`
  passthrough into the data entry — offered, not yet approved. (Moot for agent
  sessions, which can carry these today through raw data-entry `options`; it is
  about the modelled `Mask` row in manifest mode.)
- Fate of the duplicate `requirements.txt` next to `pyproject.toml`; a
  `.gitignore` for `.venv/`, `__pycache__` and generated `reports/*.html`.
- Whether the extension bundle installs cleanly on this cluster's hosts, or the
  skill ends up symlinked from `~/.qwen/skills` by habit.
- Ground-truth configs to keep as golden files: the viewer-exported session is
  saved (`examples/viewer_export.json`), and so is a session over DataIDs this
  deployment answers (`examples/dysplasia_case.json`, the demo's paste fixture).
  The multi-background MRI session is still only ever pasted in chat —
  `examples/multi_background_case.json` is our illustration of it, not a copy.
  Under the new contract these graduate into the skill (`examples/` beside
  `schema/`) *and* stay as test fixtures — one source, two readers.

(Retired since last draft: the raw-`params:` allowlist opt-out — implemented as
`strict_params`, unknown fields pass verbatim when a row declares it; and the
"manifest is the source code" framing, superseded by decision 6 as revised.)
