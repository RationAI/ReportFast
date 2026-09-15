"""The frozen component set, as something a test can check rather than a paragraph.

DESIGN.md decision 10: *"Who may create report HTML? Nobody but the frozen
component set."* The ruling that came out of review was that this has to be
enforced by code, not by convention: composition arriving on the **agent path**
(`--sessions-dir`, or any `Composition`) rejects `raw_html` and inline HTML,
while a **manifest** may still use it, because a manifest is a human having
touched a file.

Why a module for a list of ten names: the set is the documented public API, and
the only thing standing between "the agent composes from components" and "the
agent emits a `<script>`" is a table somebody has to consult. If it lives in
prose, the code drifts from it silently. So the table is here, and
`tests/test_frozen.py` asserts it still matches DESIGN.md's component table --
two sources again, the same trick `derive_schema.py --check` plays with
`PARAM_KEYS`.

Two doors had to be accounted for, not one:

  * ``RawHtml`` -- the obvious one, `Div(NotStr(html))`, unescaped by design.
  * ``_RawBlock`` / ``Report.add`` -- wraps any prebuilt FastHTML tree, so a
    caller could bypass `RawHtml` by building a node themselves. The answer is not
    to police it from the outside but to make it unreachable: `Composition`
    builds every block through `FROZEN` below, so there is no route from a
    composition spec to a raw node. `Report.add` remains for the Python path,
    where the person writing it *is* the author.

`Prose`/`Bullets`/`Heading` are *not* doors: they pass text to FastHTML tags,
which escape it. `NotStr` is the only thing in this package that suppresses
escaping, which is what makes the door easy to name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .audit import Finding, join
from .components import Bullets, Chart, Heading, LinkList, MetricTable, Prose
from .components.slide_grid import SlideCard, SlideGrid
from .core import BaseComponent, Report, Section

#: The set, one entry per row of DESIGN.md's component table. A test reads that
#: table out of the document and fails if the two disagree in either direction, so
#: adding a component means writing the row and the entry -- not one or the other.
FROZEN: Dict[str, type] = {
    "SlideCard": SlideCard,
    "SlideGrid": SlideGrid,
    "Prose": Prose,
    "Heading": Heading,
    "Bullets": Bullets,
    "LinkList": LinkList,
    "MetricTable": MetricTable,
    "Chart": Chart,
    "Section": Section,
    "Report": Report,
}

#: The same ten names as the manifest spells them (`slide_grid`, `raw_html`), for
#: error messages only -- see `_check_name`. Deliberately not a lookup table: a
#: composition that writes `slide_grid` is a composition whose author has read the
#: manifest docs, and silently accepting it would let two vocabularies for one set
#: grow up in the same file. The manifest keeps its own `_BLOCK` tuple, and this
#: mapping is asserted to agree with it by `tests/test_frozen.py`.
_FROZEN_BY_SNAKE: Dict[str, str] = {
    "slide_card": "SlideCard",
    "slide_grid": "SlideGrid",
    "prose": "Prose",
    "heading": "Heading",
    "bullets": "Bullets",
    "links": "LinkList",
    "metrics": "MetricTable",
    "chart": "Chart",
    "section": "Section",
    "report": "Report",
}

#: Names in the frozen set that may not appear on the agent path, with the block
#: key each arrives under. Deliberately a *denied* list rather than an empty one:
#  "the agent may not emit page HTML" is the rule, and `RawHtml` is how you would
#: try. Everything else in FROZEN is always allowed.
DENIED_ON_AGENT_PATH: Dict[str, str] = {"RawHtml": "raw_html"}

#: Every spelling the raw door has been written with, so a spec cannot sneak past
#: on a synonym. `raw_html` is the manifest key, `raw-html` the registered
#: component_type, `rawHtml` the shape an agent writing JSON from habit produces.
RAW_BLOCK_KEYS = frozenset({"raw_html", "raw-html", "rawHtml", "rawHtmlBlock"})

#: Snippet that means a supposedly-plain string block is markup. `<script` is the
#: one that matters; the rest are the tags that let a page reach off-box. The check
#: is a tag-shaped pattern rather than an HTML parse because a *markdown* paragraph
#: never legitimately contains one, and a paragraph that does was not a paragraph.
_MARKUP = ("<script", "</script", "<iframe", "<object", "<embed", "<svg", "<style",
           "<link", "<img", "<html", "<body", "<form", "javascript:")


class CompositionError(ValueError):
    """A composition would put markup on the page that the frozen set forbids."""


@dataclass(frozen=True)
class Block:
    """One block of a composition: a component name and its keyword arguments.

    Deliberately this shallow. The agent's door is a *spec* -- names and data --
    which is what lets the same gate read a spec written by hand, by an agent, or
    by a manifest. Handing `Composition` live component objects instead would move
    the decision to before the check could run.
    """

    component: str
    kwargs: Mapping[str, Any]

    def block_key(self) -> str:
        return self.component


#: Where a nested block list is found in a block's kwargs. `Section` and `Report`
#: exist to group blocks (both are in the frozen set), so a spec is a tree, and a
#: gate that only read the top level would be bypassed by indentation.
NESTED_KEYS = frozenset({"blocks"})


def spec(value: Any, *, where: str = "blocks") -> Block:
    """One block, however it was written, as a `Block` the gate can read.

    A bare name, a `{Name: {kwargs}}` mapping, or a `Block` already. Nested
    `blocks` are normalised too, which is the part that matters: a spec read from
    JSON nests plain dicts, and `_check_block` can only report on what it can see.
    Before this, `{"Section": {"blocks": [{"raw_html": …}]}}` sailed past the
    check and then died inside `create` with an `AttributeError` about a dict --
    an honest refusal never happened, and neither did the report.

    Raises:
        CompositionError: the value is not a block at all, or is a mapping holding
            more than one component name, where the order would have to be guessed.
    """
    if isinstance(value, Block):
        return Block(value.component, _normalise(value.kwargs, path=where))
    if isinstance(value, str):
        return Block(value, {})
    if isinstance(value, Mapping):
        if len(value) != 1:
            raise CompositionError(
                f"{where}: one component name per block, got "
                f"{sorted(str(key) for key in value)}"
            )
        (name, arguments), = value.items()
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            raise CompositionError(
                f"{where}: {name} takes its arguments as a mapping of keyword "
                f"arguments, not {arguments!r}"
            )
        return Block(str(name), _normalise(arguments, path=f"{where}.{name}"))
    raise CompositionError(
        f"{where}: {value!r} is not a block -- write a component name, or "
        "{Name: {keyword: value}}"
    )


def _normalise(arguments: Mapping[str, Any], *, path: str) -> Dict[str, Any]:
    """A block's kwargs, with any nested block list turned into `Block` specs.

    Already-built components in a nested list are left as they are: a composition
    may mix authored objects with specs, and `Section` takes both.
    """
    made: Dict[str, Any] = {}
    for key, value in arguments.items():
        if key not in NESTED_KEYS:
            made[key] = value
            continue
        entries = []
        for index, item in enumerate(_as_blocks(value)):
            if isinstance(item, BaseComponent):
                entries.append(item)
            else:
                entries.append(spec(item, where=f"{path}.{key}[{index}]"))
        made[key] = entries
    return made


def violations(blocks: Sequence[Block]) -> List[Finding]:
    """What a block list would put on the page that the frozen set forbids.

    Reports rather than raises, for the reason `audit.audit` reports: the same
    list of blocks is read by a door that must refuse (an agent's composition) and
    by one that must not (a human's manifest, where `raw_html` is allowed by
    ruling). Only the verdict differs.

    Walks nested blocks, so `blocks[3].blocks[0]` is as visible as a top-level
    block. `Section` grouping is the documented way to structure a report; a check
    that stopped at depth one would make nesting a way round the frozen set.
    """
    findings: List[Finding] = []
    for index, block in enumerate(blocks):
        findings += _check_block(block, f"blocks[{index}]")
    return findings


def authorize(blocks: Sequence[Block]) -> None:
    """Refuse a composition that would emit page HTML.

    This is the agent path's gate -- called by `Composition.build` and by the
    CLI's `--sessions-dir` door. It is *not* called for a manifest, and that
    absence is the ruling, not an oversight: refusing a human's own manifest block
    because it uses a documented feature would make the tool worse at the one job
    the escape hatch exists for.

    Raises:
        CompositionError: naming every offending block by path, so the fix does
            not require guessing which of fourteen blocks was the problem.
    """
    found = violations(blocks)
    if found:
        raise CompositionError(
            "this composition would put HTML of its own on the page, which the "
            f"frozen component set forbids -- {join(found)}."
        )


def is_frozen(name: str) -> bool:
    return name in FROZEN


def create(block: Block) -> Any:
    """Build one block, through the frozen table and not around it.

    The only route from a spec to a component, which is the structural half of the
    enforcement: `_RawBlock` and `Report.add` stay in `core.py` for the Python
    path, and nothing here can reach them, so no composition -- however it was
    authored -- can smuggle a prebuilt node in.

    Nested `blocks` are built first, so `Section`/`Report` receive components like
    any caller would hand them. A nested block that is already built passes through
    untouched, which is what lets a composition mix authored objects with specs.
    """
    if block.component not in FROZEN:
        raise CompositionError(
            f"{block.component!r} is not in the frozen component set: "
            f"{sorted(FROZEN)}. Compose from these, or add the component to "
            "report_fast/frozen.py and its row in DESIGN.md's table."
        )
    if block.component in DENIED_ON_AGENT_PATH:
        raise CompositionError(_denied_message(block.component))
    kwargs = {
        key: [_create_nested(item, f"{block.component}.{key}")
              for item in _as_blocks(value)]
        if key in NESTED_KEYS
        else value
        for key, value in block.kwargs.items()
    }
    try:
        return FROZEN[block.component](**kwargs)
    except TypeError as error:
        # A spec with a missing or invented keyword. Turned into the door's own
        # error because the caller is not standing in a traceback of their own
        # making: an agent that wrote `{"SlideGrid": {"cards": […]}}` needs the
        # signature back, not `TypeError` from inside a library it cannot see.
        raise CompositionError(
            f"{block.component} cannot take {sorted(kwargs) or 'nothing'}: {error}. "
            f"Signature: {block.component}{_signature(FROZEN[block.component])}"
        ) from error


def _create_nested(value: Any, where: str) -> Any:
    """A nested entry: a spec is checked and built, a built component passes through.

    `Section` and `Report` accept both, so this cannot assume which it is holding --
    but a spec must not reach the constructor unchecked, which is why it goes
    through `spec()` (and so through the name check) rather than straight to
    `FROZEN[...]`.
    """
    if isinstance(value, BaseComponent):
        return value
    block = spec(value, where=where)
    if not is_frozen(block.component):
        raise CompositionError(_check_name(block, where)[0].message)
    return create(block)


def _signature(cls: type) -> str:
    """The constructor's parameters, minus `self` and the catch-all `**kwargs`."""
    import inspect

    try:
        parameters = inspect.signature(cls.__init__).parameters
    except (TypeError, ValueError):  # pragma: no cover - a C-implemented component
        return "(…)"
    return "(" + ", ".join(
        name if parameter.default is inspect.Parameter.empty
        else f"{name}={parameter.default!r}"
        for name, parameter in parameters.items()
        if name != "self" and parameter.kind is not inspect.Parameter.VAR_KEYWORD
    ) + ")"


# ------------------------------------------------------------------ internals


def _as_blocks(value: Any) -> List[Any]:
    """A nested `blocks` value as a list. Entries may be specs *or* built
    components -- a composition is allowed to mix the two, and `Section` accepts
    both, so this does not pretend to know which it is holding."""
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _check_block(block: Block, path: str) -> List[Finding]:
    findings = _check_name(block, path) + _check_payload(block, path)
    for key in sorted(NESTED_KEYS.intersection(block.kwargs)):
        for index, child in enumerate(_as_blocks(block.kwargs[key])):
            if isinstance(child, BaseComponent):
                # Constructed by whoever built it, out of this spec, and is this
                # caller's Python authorship -- the path `Report.add` is allowed
                # on. The composition gate has nothing to say about an object it
                # did not build.
                continue
            where = f"{path}.{key}[{index}]"
            try:
                nested = spec(child, where=where)
            except CompositionError as error:
                # Not a block at all. Reported rather than raised, because
                # `violations()` is the half that both doors read, and the
                # permissive one must still get an answer it can print.
                findings.append(Finding(where, "component", str(error)))
                continue
            findings += _check_block(nested, where)
    return findings


def _check_name(block: Block, path: str) -> List[Finding]:
    name = block.component
    if name in RAW_BLOCK_KEYS or name in DENIED_ON_AGENT_PATH:
        return [Finding(path, "html", _denied_message(name))]
    if not is_frozen(name):
        # The realistic miss is a manifest spelling (`slide_grid`) on the
        # composition door, which names components as the code does. Saying "not
        # in the frozen set" about a component that is in the frozen set under a
        # different casing sends someone to add a component that already exists.
        spelled = _FROZEN_BY_SNAKE.get(name.lower().replace("-", "_"))
        if spelled is not None:
            return [
                Finding(
                    path,
                    "component",
                    f"{name!r} is the manifest's spelling; this door names "
                    f"components as they are, so write {spelled!r}",
                )
            ]
        return [
            Finding(
                path,
                "component",
                f"{name!r} is not in the frozen component set {sorted(FROZEN)}. "
                "Report HTML comes from these alone -- if you need something they "
                "cannot express, that is a component to add, not markup to paste",
            )
        ]
    return []


def _check_payload(block: Block, path: str) -> List[Finding]:
    """Markup hiding in a frozen component's plain-text argument.

    The realistic version: an agent writes `Prose(text="<div class=…>")`, or a
    `Section` whose paragraphs are an HTML fragment, and the frozen set is
    satisfied while the page still gets author-written markup. FastHTML escapes it
    -- so the *output* is safe, and this is a legibility rule rather than a
    hardening one: escaped HTML in a report is a visible bug, and refusing is
    better than shipping `&lt;div&gt;` to a clinician.
    """
    findings = []
    for key, value in block.kwargs.items():
        for text, where in _texts(value, f"{path}.{key}"):
            markers = [marker for marker in _MARKUP if marker in text.lower()]
            if markers:
                findings.append(
                    Finding(
                        where,
                        "html",
                        f"contains markup ({markers[0]!r}). It will be escaped and "
                        "print as literal text; the frozen set composes components "
                        "and does not take HTML as input on this path",
                    )
                )
    return findings


def _texts(value: Any, path: str) -> List[Tuple[str, str]]:
    """Every string reachable in a kwarg, with the path it came from."""
    if isinstance(value, str):
        return [(value, path)]
    if isinstance(value, Mapping):
        return [found for key, item in value.items() for found in _texts(item, f"{path}.{key}")]
    if isinstance(value, (list, tuple)):
        return [
            found
            for index, item in enumerate(value)
            for found in _texts(item, f"{path}[{index}]")
        ]
    return []


def _denied_message(name: str) -> str:
    return (
        f"{name!r} is refused on this path: the frozen component set is the only "
        "author of page HTML here. A manifest may still use "
        f"`{DENIED_ON_AGENT_PATH.get(name, 'raw_html')}:`, because a manifest is a "
        "human having touched a file -- write one instead, or compose this out of "
        f"{sorted(set(FROZEN) - set(DENIED_ON_AGENT_PATH))}"
    )


__all__ = [
    "CompositionError",
    "DENIED_ON_AGENT_PATH",
    "FROZEN",
    "RAW_BLOCK_KEYS",
    "Block",
    "authorize",
    "create",
    "is_frozen",
    "spec",
    "violations",
]
