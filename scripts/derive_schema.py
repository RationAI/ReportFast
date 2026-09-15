#!/usr/bin/env python3
"""Derive the xOpat session contract from the viewer's own source.

Why a parser and not a dependency: the contract lives in TypeScript and the
obvious tool (`ts-json-schema-generator`) needs node, which this pod does not
have. What we actually need is smaller than a TS compiler -- the *key sets* and
a coarse JSON Schema -- so this file holds a deliberately dumb reader for the
four things the viewer declares:

    src/types/app.d.ts      the session document (BackgroundItem, VisualizationItem, ...)
    src/types/config.d.ts   `XOpatSetup` / `XOpatUiSetup` -- the params allowlist
    src/config.json         `setup`: the defaults the viewer sanitizes against
    src/libs/flex-renderer/flex-renderer.js   the registered shader layers + controls

Deliberately *not* read: `src/types/session.d.ts`. It is named like the topic
and is about something else -- the live-collaboration session document. Feeding
it in would produce a schema for a feature this tool never touches, and it would
look right while doing it.

Output (default `report_fast/schema/`, `--out -` to inspect):

    session.schema.json      JSON Schema for a session, refs inlined
    params-allowlist.json    params keys; `derived` = viewer source of truth,
                             `accepted` = derived + the aliases the viewer still folds
    layer-fields.json        shader types, their fields, key aliases, index fields
    viewer.lock.json         which commit each came from

Two rules keep the output trustworthy rather than merely tidy:

  * **Declared open keys are honoured.** `BackgroundItem` and `VisualizationItem`
    end in `[key: string]: any`, so their key sets are complete but not closed --
    a key outside them is unusual, not wrong. An *interface without* that index
    signature (`VisualizationShaderLayer`) is closed, and so are the nested
    control objects, which is why an out-of-allowlist shader param is a real
    finding. `--fail-open` asserts the open-ness of those two interfaces, because
    their openness is load-bearing.
  * **The hand-maintained tables are checked, not trusted.** PARAM_KEYS and
    SHADER_PARAMS/ALIASES stay in the library as the human record of what the
    viewer does; here they are diffed against the derivation and any mismatch --
    in either direction -- fails the run. Silent drift is the only way generated
    contracts go stale while looking fresh.

Usage:
    uv run python scripts/derive_schema.py                 # write into report_fast/schema/
    uv run python scripts/derive_schema.py --check         # compare shipped artifacts, write nothing
    uv run python scripts/derive_schema.py --out - --print
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_VIEWER = Path("/home/jovyan/xopat")
DEFAULT_OUT = ROOT / "report_fast" / "schema"
DEFAULT_SKILL = ROOT / "skills" / "reportfast" / "SKILL.md"

#: Where the stamp comment begins and ends in `SKILL.md`. The block is *generated*
#: so that the skill can carry a version stamp at all -- decision 8 ruled the stamp
#: out of the report page, so the skill and the sidecar are where it lives, and a
#: hand-typed stamp is a stamp that goes stale teaching a key name the gate no
#: longer accepts. `tests/test_contract.py` asserts the comment matches the lock.
STAMP_BEGIN = "<!-- STAMP"
STAMP_END = "-->"

#: The four inputs, named the way the docstrings in `report_fast` name them.
INPUTS = {
    "app_types": Path("src/types/app.d.ts"),
    "config_types": Path("src/types/config.d.ts"),
    "defaults": Path("src/config.json"),
    "flex_renderer": Path("src/libs/flex-renderer/flex-renderer.js"),
}

#: The session-document types we publish a schema for. Chosen because a session
#: is what the URL fragment carries; the rest of app.d.ts describes live objects
#: (viewers, contexts, history) that no JSON can express.
SESSION_TYPES = (
    "DataID",
    "DataSpecification",
    "DataOverride",
    "BackgroundItem",
    "VisualizationItem",
    "VisualizationShaderLayer",
    "VisualizationShaderGroup",
)

#: Top-level session keys, and the type behind each. `XOpatConfigShape` in
#: config.d.ts declares the same set; it is kept here as prose because the .d.ts
#: types the *in-memory* config (with runtime fields), not the fragment.
SESSION_PROPERTIES = {
    "params": "XOpatSetup",
    "data": "DataSpecification[]",
    "background": "BackgroundItem[]",
    "visualizations": "VisualizationItem[]",
}

#: Which `params` keys the viewer can reach under their *flat* name even though
#: they live in `params.ui`: `getUiOption(key)` (application-context.ts) consults
#: `params.ui[key]` and then falls back to `params[key]`, folding the deprecated
#: aliases in `XOpatSetup` on the way. So `params.navigator` works and is not a
#: typo -- it is just not the spelling to prefer. Deriving this from the
#: `@deprecated` JSDoc mentioning `params.ui.` keeps it honest when the aliases
#: go away; `UI_KEYS` from the type is the other half.
FLATTENABLE_NOTE = (
    "getUiOption falls back to the flat key, so these params.ui children are also "
    "readable at the top level of params (deprecated spelling)."
)

TRUSTED_ALIASES = ("scaleBar", "toolBar", "statusBar", "appBar", "globalMenu", "mainMenu", "navigator")


# --------------------------------------------------------------- source parsing


class SourceError(RuntimeError):
    """The viewer source is not shaped the way this parser understands."""


def read_source(viewer: Path, key: str) -> str:
    path = viewer / INPUTS[key]
    if not path.is_file():
        raise SourceError(f"{path} not found -- pass --viewer at the xOpat v3 checkout.")
    return path.read_text(encoding="utf-8")


def strip_comments(text: str) -> str:
    """Blank out `//` and `/* */` runs, keeping string literals intact.

    Line comments become nothing and block comments become one space, so a
    declaration split across a comment still parses. Used for JS-ish input only;
    JSDoc is collected before this runs.
    """
    out: List[str] = []
    index, length = 0, len(text)
    while index < length:
        character = text[index]
        if character == '"':
            end = index + 1
            while end < length:
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == '"':
                    end += 1
                    break
                end += 1
            out.append(text[index:end])
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            out.append(" ")
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
            out.append(" ")
            continue
        out.append(character)
        index += 1
    return "".join(out)


def _statement_end(text: str, start: int) -> int:
    """The index of the first depth-0 `;` at or after `start`, or -1.

    Depth-aware because a `type X = { …; … };` alias carries semicolons inside
    its braces, and stopping at the first one yields half a type.
    """
    depth, index, length = 0, start, len(text)
    while index < length:
        character = text[index]
        if character in _BRACKETS:
            depth += 1
        elif character in _CLOSERS:
            depth -= 1
        elif character == ";" and depth == 0:
            return index
        index += 1
    return -1


#: The bracket pairs balanced sources can hold. Nested parens matter inside a
#: JS body (`if (a && (b || c)) {`), so the scanner keeps a stack rather than
#: counting one pair -- and does it iteratively, because flex-renderer.js is
#: 20k lines of nesting and a recursive reader runs out of stack on it.
_BRACKETS = {"{": "}", "(": ")", "[": "]"}
_CLOSERS = {value: key for key, value in _BRACKETS.items()}


def blank_comments(text: str) -> str:
    """`strip_comments`' twin, but the output is the same length as the input.

    Offsets have to survive, because members are located in the blanked text and
    their JSDoc is read from the original at the same offsets. A brace inside a
    comment would otherwise throw the depth counting off in `_split_members`,
    which is exactly how a `@property` note mentioning `{ id, createTileSource }`
    in `DataOverride` ate the field after it.
    """
    out: List[str] = []
    index, length = 0, len(text)
    while index < length:
        character = text[index]
        if character == '"':
            end = index + 1
            while end < length:
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == '"':
                    end += 1
                    break
                end += 1
            out.append(text[index:end])
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            out.append("".join("\n" if character == "\n" else " " for character in text[index:end]))
            index = end
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = length if end < 0 else end
            out.append(" " * (end - index))
            index = end
            continue
        out.append(character)
        index += 1
    return "".join(out)


def _match_delimited(text: str, start: int, opener: str, closer: str) -> int:
    """Index just past the `closer` that balances the `opener` at `start`.

    Strings are skipped (a `{` in a template is not a block) and other bracket
    kinds are tracked too, so this finds the real end of a class body or an
    object literal rather than the first brace that happens to line up.
    """
    if text[start] != opener:
        raise SourceError(f"expected {opener} at offset {start}, found {text[start:start + 10]!r}")
    stack: List[str] = []
    index, length = start, len(text)
    while index < length:
        character = text[index]
        if character in "\"'`":
            quote = character
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    break
                index += 1
        elif character in _BRACKETS:
            stack.append(character)
        elif character in _CLOSERS:
            if not stack:
                raise SourceError(f"stray {character} at offset {index}")
            opened = stack.pop()
            if not stack and opened == opener:
                return index + 1
        index += 1
    raise SourceError(f"unbalanced {opener}{closer} from offset {start}")


@dataclass
class TsField:
    name: str
    optional: bool
    ts_type: str
    deprecated: bool
    description: str
    index_signature: Optional[str] = None


@dataclass
class TsEntity:
    name: str
    kind: str  # "interface" | "type"
    bases: List[str] = field(default_factory=list)
    fields: List[TsField] = field(default_factory=list)
    union: Optional[str] = None
    description: str = ""

    @property
    def open_ended(self) -> bool:
        """Whether the declaration carries `[key: string]: any`."""
        return any(field.index_signature for field in self.fields)

    def own_keys(self) -> List[str]:
        return [field.name for field in self.fields if field.index_signature is None]


def _leading_doc(text: str, start: int) -> str:
    """The JSDoc block immediately before `start`, if any."""
    head = text[:start]
    end = len(head.rstrip())
    if end < 2 or text[end - 2 : end] != "*/":
        return ""
    begin = text.rfind("/**", 0, end - 2)
    if begin < 0:
        return ""
    return text[begin:end]


def _clean_doc(block: str) -> str:
    lines = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith(("/**", "*/")):
            continue
        lines.append(line.lstrip("* ").rstrip())
    return " ".join(line for line in lines if line).strip()


def _split_members(original: str, blanked: str) -> List[Tuple[str, str]]:
    """[(doc, declaration)] for a `{ ... }` member list.

    `blanked` is `original` with comments blanked but *not* removed -- a JSDoc
    note like `@property {{ id, createTileSource }}` otherwise reads as a nested
    object literal and swallows the field that follows it. The two strings are
    the same length, so offsets carry over: structure is scanned in `blanked`,
    prose and declaration text are sliced out of `original`.

    A member ends at a depth-0 semicolon, which keeps nested object types
    (`branding?: { … } | null`) and call signatures (`new(data, guard): X`) whole.
    A depth-0 newline ends one only when the next token cannot continue a type --
    `| ViewportSetup[]` on its own line does not.
    """
    members: List[Tuple[str, str]] = []
    cursor, length = 0, len(blanked)
    while cursor < length:
        while cursor < length and blanked[cursor] in " \t\r\n;":
            cursor += 1
        if cursor >= length:
            break
        depth, scan = 0, cursor
        while scan < length:
            character = blanked[scan]
            if character in "([{":
                depth += 1
            elif character in ")]}":
                depth -= 1
                if depth < 0:  # the closer of the enclosing block, or a stray
                    scan = length
                    break
            elif character == ";" and depth == 0:
                break
            elif character == "\n" and depth == 0:
                rest = blanked[scan:].lstrip()
                if not rest.startswith((":", "?", "|", "&", "=", ".")):
                    break
            scan += 1
        start = cursor
        cursor = scan + 1
        declaration = strip_comments(original[start:scan]).strip().rstrip(";").strip()
        doc = _clean_doc(_leading_doc(original, start))
        if declaration:
            members.append((doc, declaration))
    return members


def parse_ts(text: str, names: Sequence[str]) -> Dict[str, TsEntity]:
    """Read the named `interface`/`type` declarations out of a .d.ts file.

    The declaration *positions* come from the comment-blanked text (a `{` inside
    a doc comment must not count as a block opener) while the *text* used for
    types and prose is sliced from the original at the same offsets.
    """
    wanted = set(names)
    blanked = blank_comments(text)
    found: Dict[str, TsEntity] = {}
    pattern = re.compile(r"(?:^|\n)[ \t]*(?:export\s+)?(interface|type)\s+([A-Za-z_$][\w$]*)")
    for match in pattern.finditer(blanked):
        kind, name = match.group(1), match.group(2)
        if name not in wanted or name in found:
            continue
        body_start = blanked.find("{", match.end())
        doc = _clean_doc(_leading_doc(text, match.start()))
        if kind == "interface":
            if body_start < 0:
                raise SourceError(f"interface {name} has no body")
            extends = re.match(r"\s*extends\s+([\w.,\s$]+)", blanked[match.end() : body_start])
            bases = [part.strip() for part in extends.group(1).split(",")] if extends else []
            end = _match_delimited(blanked, body_start, "{", "}")
            fields = _ts_fields(text[body_start + 1 : end - 1], blanked[body_start + 1 : end - 1])
            found[name] = TsEntity(name, kind, bases, fields, description=doc)
            continue
        # A type alias: `type X = A | B;` or `type X = { … };`. The terminator is
        # the first *depth-0* semicolon -- an object-literal alias is full of
        # inner ones, which is why `XOpatSetup` needs the bracket-aware scan and
        # not a plain `find(";")`.
        equals = blanked.find("=", match.end())
        semicolon = _statement_end(blanked, match.end())
        if equals < 0 or semicolon < 0 or equals > semicolon:
            raise SourceError(f"type alias {name} is not an `= …;` statement")
        union = text[equals + 1 : semicolon].strip()
        entity = TsEntity(name, kind, [], [], union=union, description=doc)
        # `type XOpatSetup = { … } | null` is an object with an alias around it,
        # and the key set -- the thing this whole derivation is for -- lives in
        # that literal. Read it as fields too, so `own_keys()` works the same for
        # aliased objects as for interfaces.
        segment = text[equals + 1 : semicolon]
        stripped = segment.lstrip()
        if re.fullmatch(r"\{\s*[\s\S]*\}(?:\s*\|\s*null)?", blanked[equals + 1 : semicolon].strip()):
            open_at = equals + 1 + (len(segment) - len(stripped))
            close_at = _match_delimited(blanked, open_at, "{", "}") - 1
            entity.fields = _ts_fields(text[open_at + 1 : close_at], blanked[open_at + 1 : close_at])
        found[name] = entity
    missing = wanted - set(found)
    if missing:
        raise SourceError(f"not declared in the viewer source: {sorted(missing)}")
    return found


def _ts_fields(body: str, blanked: str) -> List[TsField]:
    fields: List[TsField] = []
    for doc, declaration in _split_members(body, blanked):
        index_signature = None
        match = re.match(r"\[\s*(?:readonly\s+)?[\w$]+\s*\??\s*:\s*(?:string|number|symbol)\s*\]", declaration)
        if match:
            index_signature = declaration[match.end() :].lstrip(":").strip()
            fields.append(TsField("*", False, index_signature, False, doc, index_signature))
            continue
        match = re.match(r"(?:readonly\s+)?(['\"]?)([\w$-]+)\1(\??)\s*:", declaration)
        if not match:
            continue  # a method, a getter, or something we do not publish
        deprecated = "@deprecated" in doc
        fields.append(
            TsField(
                name=match.group(2),
                optional=bool(match.group(3)) or deprecated,
                ts_type=declaration[match.end() :].strip().rstrip(";").strip(),
                deprecated=deprecated,
                description=_property_doc(doc, match.group(2)) or doc,
            )
        )
    return fields


def _property_doc(block: str, name: str) -> str:
    """The `@property name ...` line of a block JSDoc, if the block lists properties."""
    pattern = re.compile(rf"@property\s+(?:\{{[^}}]*\}}\s+)?{re.escape(name)}\b([^\n]*)")
    match = pattern.search(block)
    return match.group(1).strip(" -") if match else ""


def parse_config_defaults(text: str) -> Dict[str, Any]:
    """`setup` out of the viewer's config.json (which is JSONC: comments, trailing commas)."""
    body = strip_comments(text)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    try:
        config = json.loads(body)
    except json.JSONDecodeError as error:  # pragma: no cover - guards a viewer-side format change
        raise SourceError(f"viewer config.json is not JSONC-parseable: {error}") from error
    setup = config.get("setup")
    if not isinstance(setup, dict):
        raise SourceError("viewer config.json has no `setup` block")
    return setup


def _js_object_literal(text: str, brace_at: int) -> Dict[str, Any]:
    """Convert a JS object literal to Python, tolerating identifiers as keys.

    Only what the docs()/defaultControls() blocks contain is accepted: literals,
    nested objects/arrays, and `key: value` pairs with bareword keys. Anything
    else -- a function call, a template string, a variable -- raises, because a
    silently-dropped half of a control definition is worse than no derivation.
    """
    end = _match_delimited(text, brace_at, "{", "}")
    source = text[brace_at + 1 : end - 1]
    # Keys: quote barewords (`block_size:` -> "block_size":). A colon inside a
    # string is not a key separator, so only rewrite at the start of a property.
    quoted = re.sub(r"(?m)([{,]\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', "," + source)
    quoted = re.sub(r"(?m)^(\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', quoted)
    quoted = re.sub(r"/\*.*?\*/", "", quoted, flags=re.S)
    quoted = re.sub(r"(?m)^\s*//.*$", "", quoted)
    quoted = re.sub(r",(\s*[}\]])", r"\1", quoted).lstrip(",")
    try:
        return json.loads("{" + quoted + "}")
    except json.JSONDecodeError as error:
        raise SourceError(f"JS object literal beyond this parser: {error}\n{source[:400]}") from error


def _js_object_members(text: str, brace_at: int) -> List[Tuple[str, str]]:
    """[(key, member_text)] for the top level of a JS object literal.

    The values are kept because "which params does this layer declare" cannot be
    answered from the key list alone: `icons: { array: { name: i => `icon${i}` } }`
    is not a param the viewer ever reads -- `_expandControlDefinitions` replaces it
    with one control per class interval (`icon0`, `icon1`, …). Reading only the
    names would publish `icons` as legal and miss the family it generates.

    Values are often arrow functions (`accepts: (type) => …`), which the strict
    literal reader above rightly refuses; splitting at depth-0 commas and taking
    the token before the first colon needs no JS evaluation at all.
    """
    end = _match_delimited(text, brace_at, "{", "}")
    body = text[brace_at + 1 : end - 1]
    members: List[Tuple[str, str]] = []
    depth, member_start, index, length = 0, 0, 0, len(body)
    while index <= length:
        character = body[index] if index < length else ","
        if character in "\"'`":
            quote = character
            index += 1
            while index < length:
                if body[index] == "\\":
                    index += 2
                    continue
                if body[index] == quote:
                    break
                index += 1
        elif character in _BRACKETS:
            depth += 1
        elif character in _CLOSERS:
            depth -= 1
        elif character == "," and depth == 0:
            member = body[member_start:index].strip()
            match = re.match(r"(?:['\"]([^'\"]+)['\"]|([A-Za-z_$][\w$]*))\s*:", member)
            if match:
                members.append((match.group(1) or match.group(2), member))
            member_start = index + 1
        index += 1
    return members


#: The placeholder a generated control family is published under. The viewer's
#: template says `icon${index}`; we say `icon{N}` and the library's hand-written
#: table says `iconN` -- three spellings of "one control per class", so the
#: diff-check compares `_control_token` of each rather than the raw strings.
TEMPLATE_PLACEHOLDER = "{N}"


def _templated_control(key: str, member: str) -> Optional[str]:
    """The generated-name template of an `array:` control, if this member is one."""
    if not re.search(r"(?:^|[{,])\s*array\s*:", member):
        return None
    named = re.search(r"\bname\s*:\s*\([^)]*\)\s*=>\s*`([^`]*)`", member)
    if named:
        return re.sub(r"\$\{\s*\w+\s*\}", TEMPLATE_PLACEHOLDER, named.group(1))
    return f"{key}{TEMPLATE_PLACEHOLDER}"


def _control_token(name: str) -> str:
    """One comparable spelling for a control name across the three sources."""
    return re.sub(r"[^a-z0-9]", "", str(name).replace(TEMPLATE_PLACEHOLDER, "N").lower())


@dataclass
class ShaderLayer:
    type: str
    title: str
    description: str = ""
    summary: str = ""
    control_doc: List[Dict[str, Any]] = field(default_factory=list)
    default_controls: List[str] = field(default_factory=list)
    #: Control *families* from `array:` templates (`icon` -> `icon{N}`), kept
    #: separate because a family is not a param name anybody writes.
    control_families: Dict[str, str] = field(default_factory=dict)


def parse_shader_layers(text: str) -> List[ShaderLayer]:
    """Every `ShaderLayerRegistry.register(...)` class in flex-renderer.js."""
    code = strip_comments(text)
    layers: List[ShaderLayer] = []
    for match in re.finditer(r"ShaderLayerRegistry\.register\(", code):
        # Two spellings: `register(class X extends … { … })`, and `register(X)`
        # for a class declared a few lines earlier (the FisheyeLens layer). The
        # second form's body lives elsewhere, so go find it there.
        tail = code[match.end() :]
        if re.match(r"\s*class\b", tail):
            body_start = code.find("{", match.end())
        else:
            named = re.match(r"\s*([A-Za-z_$][\w$]*)\s*[,)]", tail)
            if not named:
                raise SourceError(
                    f"register() at {match.end()} is not a class expression this parser reads"
                )
            declaration = re.search(
                rf"class\s+{re.escape(named.group(1))}\b[^{{]*{{", code
            )
            if not declaration:
                raise SourceError(
                    f"register({named.group(1)}) names a class whose body this parser did not find"
                )
            body_start = declaration.end() - 1
        if body_start < 0:
            raise SourceError(f"register() at {match.end()} has no class body")
        body = code[body_start : _match_delimited(code, body_start, "{", "}") - 1]
        type_match = re.search(r"static\s+type\s*\(\s*\)\s*\{\s*return\s+[\"']([^\"']+)[\"']", body)
        if not type_match:  # an anonymous registration inside a group -- not a session-visible layer
            continue
        layer = ShaderLayer(
            type=type_match.group(1),
            title=_js_static_string(body, "name"),
            description=_js_static_string(body, "description"),
        )
        docs_at = re.search(r"static\s+docs\s*\(\s*\)\s*\{\s*return", body)
        if docs_at:
            docs = _js_object_literal(body, body.find("{", docs_at.end()))
            layer.summary = str(docs.get("summary", ""))
            layer.description = layer.description or str(docs.get("description", ""))
            controls = docs.get("controls")
            if isinstance(controls, list):
                layer.control_doc = [item for item in controls if isinstance(item, dict)]
        defaults_at = re.search(r"static\s+get\s+defaultControls\s*\(\s*\)\s*\{\s*return", body)
        if defaults_at:
            members = _js_object_members(body, body.find("{", defaults_at.end()))
            for key, member in members:
                family = _templated_control(key, member)
                if family is None:
                    layer.default_controls.append(key)
                else:
                    layer.control_families[key] = family
            layer.default_controls.sort()
        layers.append(layer)
    if not layers:
        raise SourceError("no shader layers found -- flex-renderer.js has moved or changed shape")
    return sorted(layers, key=lambda layer: layer.type)


def _js_static_string(body: str, method: str) -> str:
    match = re.search(rf"static\s+{method}\s*\(\s*\)\s*\{{\s*return\s+[\"']([^\"']*)[\"']", body)
    return match.group(1) if match else ""


# ----------------------------------------------------------- viewer source of truth


def _deprecated_alias_keys(entities: Dict[str, TsEntity]) -> List[str]:
    """Keys whose doc says "use `params.ui.<something>` instead"."""
    aliases = []
    for entity in entities.values():
        for member in entity.fields:
            if member.deprecated and "params.ui." in member.description:
                aliases.append(member.name)
    return sorted(set(aliases))


def _type_node(ts_type: str, entities: Dict[str, TsEntity], depth: int = 0) -> Dict[str, Any]:
    """A coarse JSON Schema node for a TS type expression.

    Coarse on purpose: the point is *which keys are legal*, not full validation.
    Unions of literals become enums, `| null` becomes a nullable type list, and
    anything richer (a function, a generic from OpenSeadragon) becomes `{}` --
    permissive, since a wrong rejection here would block a legal session.
    """
    ts_type = ts_type.strip().rstrip(";").strip()
    if not ts_type or depth > 12:
        return {}
    parts = _split_union(ts_type)
    if len(parts) > 1:
        nodes = [_type_node(part, entities, depth + 1) for part in parts]
        return _merge_union(nodes)
    if parts and parts[0] != ts_type:
        # `_split_union` drops `| null` from a single-kind union; the null half is
        # still worth recording, so widen rather than lose it.
        node = _type_node(parts[0], entities, depth + 1)
        node["nullable"] = True
        return node
    if ts_type.endswith("[]"):
        return {"type": "array", "items": _type_node(ts_type[:-2], entities, depth + 1)}
    match = re.fullmatch(r"Array<(.*)>", ts_type, flags=re.S)
    if match:
        return {"type": "array", "items": _type_node(match.group(1), entities, depth + 1)}
    match = re.fullmatch(r"Record<[^,]+,\s*(.*)>", ts_type, flags=re.S)
    if match:
        return {"type": "object", "additionalProperties": _type_node(match.group(1), entities, depth + 1)}
    if ts_type.startswith("{"):
        return _object_node(ts_type, entities, depth)
    if ts_type.startswith("(") and ts_type.endswith(")") and _split_union(ts_type[1:-1]):
        return _type_node(ts_type[1:-1], entities, depth + 1)
    if re.fullmatch(r"[\"'][^\"']*[\"']", ts_type):
        return {"enum": [ts_type.strip("\"'")]}
    if ts_type in ("string",):
        return {"type": "string"}
    if ts_type in ("number", "bigint"):
        return {"type": "number"}
    if ts_type in ("boolean",):
        return {"type": "boolean"}
    if ts_type in ("any", "unknown"):
        return {}
    if ts_type in ("null", "undefined", "void"):
        return {"type": "null"}
    if ts_type in ("object", "Object"):
        return {"type": "object"}
    entity = entities.get(ts_type)
    if entity is None:
        return {}  # an OpenSeadragon/class type we deliberately do not model
    if entity.kind == "type" and entity.union:
        return _type_node(entity.union, entities, depth + 1)
    return _entity_node(entity, entities, depth)


def _split_union(ts_type: str) -> List[str]:
    parts, depth, current = [], 0, ""
    for character in ts_type:
        if character in "({[<":
            depth += 1
        elif character in ")}]>":
            depth -= 1
        if character == "|" and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        current += character
    parts.append(current.strip())
    return [part for part in parts if part and part != "null"] if len(parts) > 1 else [current.strip()]


def _merge_union(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold a union: nulls widen `type`, literals collect into `enum`."""
    nulls = any(node.get("type") == "null" for node in nodes)
    simple, objects, enums = [], [], []
    for node in nodes:
        if node.get("type") == "null":
            continue
        if "enum" in node and set(node) <= {"enum"}:
            enums.extend(node["enum"])
        elif node.get("type") in ("string", "number", "boolean", "array"):
            simple.append(node["type"])
        elif node.get("type") == "object" or "properties" in node:
            objects.append(node)
        else:
            return {}
    out: Dict[str, Any] = {}
    if enums and not simple and not objects:
        out["enum"] = sorted(set(enums), key=str)
        if nulls:
            out["nullable"] = True
        return out
    kinds = sorted(set(simple))
    if objects:
        kinds.append("object")
    if len(kinds) == 1:
        out["type"] = kinds[0]
    elif kinds:
        out["type"] = kinds
    if enums:
        out["enum"] = sorted(set(enums), key=str)
    if len(objects) == 1:
        out.update({key: value for key, value in objects[0].items() if key != "type"})
    elif len(objects) > 1:
        out["anyOf"] = objects
    if nulls:
        out["nullable"] = True
    return out


def _object_node(ts_type: str, entities: Dict[str, TsEntity], depth: int) -> Dict[str, Any]:
    body = ts_type.strip()
    if body.startswith("{") and body.endswith("}"):
        inner = body[1:-1]
    else:
        raise SourceError(f"cannot read object type {ts_type[:60]}")
    # Inline object types arrive from a declaration already stripped of comments,
    # so structure and text are the same string here.
    node = _properties(_ts_fields(inner, inner), entities, depth + 1)
    node["type"] = "object"
    return node


def _entity_node(entity: TsEntity, entities: Dict[str, TsEntity], depth: int) -> Dict[str, Any]:
    node = _properties(entity.fields, entities, depth + 1)
    node["type"] = "object"
    if entity.description:
        node["x-description"] = entity.description
    return node


def _properties(fields: Sequence[TsField], entities: Dict[str, TsEntity], depth: int) -> Dict[str, Any]:
    properties, required, additional = {}, [], {}
    for member in fields:
        if member.index_signature is not None:
            additional = _type_node(member.index_signature, entities, depth + 1)
            continue
        properties[member.name] = _type_node(member.ts_type, entities, depth + 1)
        if member.description:
            properties[member.name]["x-description"] = member.description
        if member.deprecated:
            properties[member.name]["x-deprecated"] = True
        if not member.optional:
            required.append(member.name)
    node: Dict[str, Any] = {"properties": properties}
    if required:
        node["required"] = sorted(required)
    if additional:
        node["additionalProperties"] = additional
    return node


# ------------------------------------------------------------------ the derivation


def viewer_stamp(viewer: Path) -> Dict[str, Any]:
    """Which exact source tree these artifacts came from.

    The version alone is not enough: the host deploys a commit, `package.json`
    says `3.1.0` across many of them, and "the allowlist drifted" is a claim that
    has to be about one tree. So: commit, dirty flag, and a digest of the four
    files that actually fed this.
    """

    def git(*args: str) -> str:
        try:
            done = subprocess.run(
                ["git", "-C", str(viewer), *args], capture_output=True, text=True, check=True
            )
            return done.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    import hashlib

    digests = {}
    for relative in INPUTS.values():
        path = viewer / relative
        if path.is_file():
            digests[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    version = ""
    package = viewer / "package.json"
    if package.is_file():
        match = re.search(r'"version"\s*:\s*"([^"]+)"', package.read_text(encoding="utf-8"))
        version = match.group(1) if match else ""
    # `dirty` is asked per input file, not per tree: an unrelated untracked
    # directory in the viewer checkout is not a reason to distrust the schema,
    # and a flag that cries wolf gets ignored -- which is worse than not having it.
    status = git("status", "--porcelain", "--", *[relative.as_posix() for relative in INPUTS.values()])
    touched = sorted(line[3:].strip() for line in status.splitlines() if line.strip())
    return {
        "viewer": str(viewer),
        "version": version,
        "commit": git("rev-parse", "HEAD"),
        "describe": git("describe", "--tags", "--always"),
        "inputs_dirty": touched,
        "derived_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": digests,
    }


def derive(viewer: Path) -> Dict[str, Any]:
    """Build the four artifacts from the pinned viewer source."""
    app_text = read_source(viewer, "app_types")
    config_text = read_source(viewer, "config_types")
    defaults = parse_config_defaults(read_source(viewer, "defaults"))
    layers = parse_shader_layers(read_source(viewer, "flex_renderer"))

    app_entities = parse_ts(app_text, list(SESSION_TYPES) + ["SlideSourceOptions", "VirtualCroppingContext"])
    setup_entities = parse_ts(config_text, ["XOpatSetup", "XOpatUiSetup", "ViewportSetup"])

    # --- params allowlist -----------------------------------------------------
    setup_entity = setup_entities["XOpatSetup"]
    derived_params = setup_entity.own_keys()
    deprecated = _deprecated_alias_keys({setup_entity.name: setup_entity})
    ui_fields = setup_entities["XOpatUiSetup"].own_keys()
    nested = {"ui": sorted(ui_fields)}

    schema_entities = {**app_entities, **setup_entities}
    session_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "xOpat v3 session",
        "description": (
            "The JSON the viewer parses out of the URL fragment. Generated from the "
            "viewer source by scripts/derive_schema.py -- do not hand-edit. Keys an "
            "interface declares with `[key: string]: any` are open by design: the "
            "viewer passes them through to plugins and layer constructors."
        ),
        "type": "object",
        "properties": {},
        "additionalProperties": True,
        "x-note": (
            "Top-level keys outside `properties` are not an error -- the viewer "
            "forwards unknown config keys to plugins. They are a smell, not a fault; "
            "params keys and shader control params are the closed sets."
        ),
    }
    for key, type_name in SESSION_PROPERTIES.items():
        session_schema["properties"][key] = _type_node(type_name, schema_entities)
    session_schema["properties"]["plugins"] = {
        "type": "object",
        "description": "Plugin id -> options, loaded on boot.",
    }

    # A reference map of the session-document types, so a reader (human or agent)
    # can see what a field's type actually contains without resolving $refs.
    definitions = {}
    for name in SESSION_TYPES:
        entity = app_entities[name]
        node = _type_node(name, schema_entities)
        node["x-kind"] = entity.kind
        if entity.union:
            node["x-ts"] = entity.union
        definitions[name] = node
    session_schema["$defs"] = definitions

    # What the gate should accept at the TOP LEVEL of `params`, and this is the
    # subtle part of the whole derivation. The viewer's real gate is
    # `sanitizeAgainst` in src/app.ts, which filters top-level `params` keys
    # against `config.json :: setup` and recurses into `ui` only. So a key is
    # accepted at the top level iff it is a top-level key of either source:
    #   - `XOpatSetup` (what the code is typed to read; e.g. `background`,
    #     `branding`, `fetchAsync` -- declared but not defaulted),
    #   - `config.json :: setup` (what the filter checks against; e.g.
    #     `webGlPrecision`, `faultyTileThreshold`, `globalMenuMaxWidth`, the two
    #     `requestSchedulerUrgent*`, `syntheticPreviewLevel` -- defaulted and read
    #     via `getOption`, but absent from the .d.ts).
    # The `params.ui` children (navigator, appBar, …) are NOT top-level: they
    # belong under `params.ui`, and a *flat* `params.navigator` is stripped by
    # `sanitizeAgainst` before `getUiOption`'s flat fallback ever runs. So the
    # deprecated flat aliases that actually survive are only those ALSO top-level
    # in setup -- `scaleBar`/`statusBar`/`toolBar` -- not the whole UI set. This
    # is why `accepted` is not `derived | ui_children | defaults`.
    accepted = sorted(set(derived_params) | set(defaults))
    flat_survivors = sorted(set(ui_fields) & set(accepted))
    flat_stripped = sorted(set(ui_fields) - set(accepted))
    params_allowlist = {
        "accepted": accepted,
        "derived": sorted(set(derived_params)),
        "deprecated": sorted(set(deprecated)),
        "nested": nested,
        "ui_children": sorted(set(ui_fields)),
        # Only the ui children that ALSO appear top-level in a setup source
        # survive flat; the rest are stripped by sanitizeAgainst before the
        # getUiOption fallback that would have honored them can run.
        "flat_ui_aliases_surviving": flat_survivors,
        "flat_ui_aliases_stripped_by_sanitizer": flat_stripped,
        "trusted_aliases": [key for key in TRUSTED_ALIASES if key in accepted],
        "source": {
            "types": "src/types/config.d.ts :: XOpatSetup, XOpatUiSetup",
            "defaults": "src/config.json :: setup (the object `sanitizeAgainst` filters against)",
            "enforcement": "src/app.ts :: sanitizeAgainst drops any top-level params key absent from setup, recursing only into `ui`",
        },
        "notes": [
            "accepted = XOpatSetup keys | config.json :: setup keys -- exactly what "
            "sanitizeAgainst lets through at the top level.",
            "params.ui children go under `params: {ui: {...}}`. The deprecated flat "
            "spelling survives ONLY for flat_ui_aliases_surviving; the ones in "
            "flat_ui_aliases_stripped_by_sanitizer are dropped before getUiOption "
            "would read them, despite that function's own flat fallback.",
            "runtime_only_keys: in config.json :: setup but not XOpatSetup -- read via "
            "getOption, legal, just not in the type. type_only_keys: the reverse.",
            "deprecated keys are folded by the viewer, not lost: e.g. "
            "activeVisualizationIndex is distributed onto background[].visualizationIndex.",
        ],
        "defaults_keys": sorted(defaults),
        "runtime_only_keys": sorted(set(defaults) - set(derived_params)),
        "type_only_keys": sorted(set(derived_params) - set(defaults)),
    }

    # --- shader layers --------------------------------------------------------
    by_type: Dict[str, ShaderLayer] = {}
    for layer in layers:
        by_type[layer.type] = layer
    shader_fields: Dict[str, Dict[str, Any]] = {}
    aliases: Dict[str, Dict[str, str]] = {}
    for type_name, layer in sorted(by_type.items()):
        doc_names = [str(item.get("name")) for item in layer.control_doc if item.get("name")]
        defaults_names = list(layer.default_controls)
        # Generated control families (`icon{N}`) are declared *by template*, and the
        # docs name the same family with its own placeholder (`iconN`). Neither
        # spelling is a key anyone writes -- a session carries `icon0`, `icon1` --
        # so both are pulled out of `fields` and published as families instead.
        # Leaving them in would make the gate reject the real names and accept a
        # fake one, which is the exact inversion of the bug this file exists to kill.
        family_tokens = {_control_token(value) for value in layer.control_families.values()}
        family_tokens |= {_control_token(key) for key in layer.control_families}
        concrete_docs = [name for name in doc_names if _control_token(name) not in family_tokens]
        declared = sorted(set(concrete_docs) | set(defaults_names))
        entry: Dict[str, Any] = {
            "title": layer.title,
            "description": layer.description or layer.summary,
            "fields": declared,
            "declared_in": {
                "docs": sorted(concrete_docs),
                "defaultControls": sorted(defaults_names),
            },
            "controls": layer.control_doc,
        }
        if layer.control_families:
            entry["control_families"] = dict(sorted(layer.control_families.items()))
        only_defaults = sorted(set(defaults_names) - set(concrete_docs))
        only_docs = sorted(set(concrete_docs) - set(defaults_names))
        if only_defaults:
            entry["undocumented_controls"] = only_defaults
        if only_docs:
            entry["undropped_docs_only"] = only_docs
        shader_fields[type_name] = entry
        # The viewer names controls inconsistently -- `outer_color` next to
        # `edgeThickness` -- so both spellings are in play, and "is this key
        # legal?" needs a deterministic answer rather than a memory of which
        # layer used which style. Maps the spelling we might write to the
        # spelling the viewer actually declares.
        pair = {}
        for name in declared:
            for other in (_snake(name), _camel(name)):
                if other != name:
                    pair[other] = name
        if pair:
            aliases[type_name] = dict(sorted(pair.items()))

    layer_fields = {
        "shader_types": sorted(by_type),
        "common_fields": sorted(_layer_common_fields(app_entities["VisualizationShaderLayer"])),
        "common_controls": ["opacity"],
        "common_controls_source": "ShaderLayer._buildControls injects `opacity` unless the layer defines it",
        "index_fields": ["dataReferences", "tiledImages"],
        "index_notes": (
            "`dataReferences` are indices into session data[] (v2's singular "
            "`dataReference` on a layer is ignored by v3). `tiledImages` are runtime "
            "tile-image ids, not authorable references."
        ),
        "group": {
            "type": "group",
            "nested": "shaders",
            "order": "order",
            "note": "A group nests `shaders` (same shape) and may override child order.",
        },
        "aliases": aliases,
        "layers": shader_fields,
    }

    return {
        "session.schema.json": session_schema,
        "params-allowlist.json": params_allowlist,
        "layer-fields.json": layer_fields,
        "viewer.lock.json": viewer_stamp(viewer),
    }


def _camel(name: str) -> str:
    parts = str(name).split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", str(name)).lower()


def _layer_common_fields(entity: TsEntity) -> List[str]:
    return [field.name for field in entity.fields if field.index_signature is None]


# ------------------------------------------------------------------ the diff-check


def diff_against_library(artifacts: Dict[str, Any]) -> List[str]:
    """Every disagreement between the derivation and the hand-written tables.

    Fails on both directions. A key in the library but not the viewer is dead
    weight we would emit; a key in the viewer but not the library is a legal
    setting our gate would reject. Neither is acceptable once the generated file
    is what the library reads.
    """
    from report_fast import shader as shader_module
    from report_fast import xopat as xopat_module

    problems: List[str] = []
    allowlist = artifacts["params-allowlist.json"]
    mine, theirs = set(xopat_module.PARAM_KEYS), set(allowlist["accepted"])
    for key in sorted(theirs - mine):
        problems.append(f"params: viewer declares {key!r}, PARAM_KEYS does not")
    for key in sorted(mine - theirs):
        if key in set(allowlist["flat_ui_aliases_stripped_by_sanitizer"]):
            problems.append(
                f"params: PARAM_KEYS carries {key!r}, a params.ui child the viewer "
                "strips at the top level (sanitizeAgainst runs before getUiOption's "
                "flat fallback) -- emit it under params.ui instead"
            )
            continue
        problems.append(f"params: PARAM_KEYS carries {key!r}, the viewer does not declare it")

    layer_fields = artifacts["layer-fields.json"]
    theirs_types = set(layer_fields["shader_types"])
    mine_types = {value.value for value in shader_module.ShaderType}
    for value in sorted(theirs_types - mine_types):
        problems.append(f"shader: viewer registers {value!r}, ShaderType does not list it")
    for value in sorted(mine_types - theirs_types):
        problems.append(f"shader: ShaderType lists {value!r}, the viewer does not register it")

    for type_name, entry in sorted(layer_fields["layers"].items()):
        try:
            hand = shader_module.SHADER_PARAMS[shader_module.ShaderType(type_name)]
        except (KeyError, ValueError):
            continue  # already reported above
        ours = set(hand) | set(shader_module.COMMON_PARAMS)
        theirs_params = set(entry["fields"]) | set(shader_module.COMMON_PARAMS)
        missing = sorted(theirs_params - ours)
        extra = sorted(ours - theirs_params - {"opacity"})
        # A generated control family lives in neither `fields` nor a plain name:
        # the viewer builds one control per class interval, so the legal keys are
        # `icon0`, `icon1`, … and `iconN` -- the docs' own spelling of the
        # template -- is a key no viewer has ever read. Compare by token so the
        # three spellings (`icons`, `icon{N}`, `iconN`) count as one thing.
        try:
            family_type = shader_module.ShaderType(type_name)
        except ValueError:
            family_type = None
        ours_families = [
            family
            for value, family in getattr(shader_module, "SHADER_PARAM_FAMILIES", {}).items()
            if value is family_type
        ]
        ours_family_tokens = {_control_token(family.template) for family in ours_families}
        derived_families = entry.get("control_families", {})
        theirs_family_tokens = {
            _control_token(value) for value in derived_families.values()
        } | {_control_token(key) for key in derived_families}
        missing = sorted(
            key for key in missing if _control_token(key) not in ours_family_tokens
        )
        aliased = [key for key in extra if key in set(layer_fields["aliases"].get(type_name, {}))]
        templated = sorted(
            key for key in extra if _control_token(key) in theirs_family_tokens
        )
        unknown = sorted(set(extra) - set(aliased) - set(templated))
        if missing:
            problems.append(
                f"shader {type_name}: viewer declares {missing}, SHADER_PARAMS does not"
            )
        unmatched = sorted(
            derived_families[key]
            if key in derived_families
            else key
            for key in derived_families
            if _control_token(derived_families[key]) not in ours_family_tokens
            and _control_token(key) not in ours_family_tokens
        )
        if templated and not ours_families:
            problems.append(
                f"shader {type_name}: the viewer generates {sorted(derived_families.values())} "
                f"per data interval, but SHADER_PARAM_FAMILIES has no entry -- {templated} "
                "in SHADER_PARAMS is the docs' template name, which no viewer ever "
                "reads, so the real names (`icon0`, …) would be dropped"
            )
        elif unmatched:
            problems.append(
                f"shader {type_name}: SHADER_PARAM_FAMILIES is missing {unmatched}"
            )
        if aliased:
            problems.append(
                f"shader {type_name}: SHADER_PARAMS aliases {aliased} for the viewer's "
                f"own spelling; keep them in shader.SHADER_PARAM_ALIASES so the alias "
                "is a decision on record rather than a name that happens to pass"
            )
        if unknown:
            problems.append(
                f"shader {type_name}: SHADER_PARAMS carries {unknown}, which the viewer "
                "does not declare under any spelling"
            )
    return problems


def open_key_assertions(artifacts: Dict[str, Any], viewer: Path) -> List[str]:
    """The open-ness checks that must keep holding for the gate to be honest."""
    problems = []
    entities = parse_ts(read_source(viewer, "app_types"), list(SESSION_TYPES))
    for name in ("BackgroundItem", "VisualizationItem"):
        if not entities[name].open_ended:
            problems.append(
                f"{name} no longer declares [key: string]: any -- its key set is now "
                "closed, so unknown keys there are errors. The gate must be tightened."
            )
    if entities["VisualizationShaderLayer"].open_ended:
        problems.append(
            "VisualizationShaderLayer now declares an index signature: shader params are "
            "no longer closed by the type. Only config.json's control definitions close them."
        )
    return problems


# ------------------------------------------------------------------------- writing


def render(name: str, payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def stamp_comment(lock: Dict[str, Any]) -> str:
    """The generated block, from the lock and nothing else.

    Every fact in it is read off `lock`, including the date: a hand-typed date is
    the one field that quietly goes wrong, and `derived_at` is the honest answer to
    "how old is this".
    """
    return (
        f"{STAMP_BEGIN} — generated, do not hand-edit. These facts were derived from\n"
        f"     xOpat {lock['version']}, commit {str(lock['commit'])[:7]} "
        f"({str(lock['derived_at'])[:10]}), from report_fast/schema/viewer.lock.json.\n"
        f"     Check it from anywhere:\n"
        "         uv run python -c 'from report_fast.contract import viewer_stamp as v; print(v())'\n"
        f"     Refresh when the pinned viewer moves: uv run python scripts/derive_schema.py\n"
        f"     If the two disagree, the lock is right and this comment is stale.\n"
        f"{STAMP_END}"
    )


def refresh_skill_stamp(skill: Path, lock: Dict[str, Any], *, check: bool) -> List[str]:
    """Write (or verify) the stamp block in `SKILL.md`. Returns problems.

    Replaces the one block between the sentinels and nothing else, so the
    handwritten procedure around it stays handwritten. A file with no block is a
    problem rather than a place to insert one: where the stamp goes is a decision,
    and silently prepending to someone's skill is not this script's call.
    """
    wanted = stamp_comment(lock)
    if not skill.is_file():
        return [f"{skill}: not present, so the stamp could not be refreshed"]
    text = skill.read_text(encoding="utf-8")
    if STAMP_BEGIN not in text or STAMP_END not in text:
        return [f"{skill}: no {STAMP_BEGIN} block to refresh; add one by hand once"]
    head, rest = text.split(STAMP_BEGIN, 1)
    _, tail = rest.split(STAMP_END, 1)
    current = head + wanted + tail
    if check:
        # Version and commit are what the comment claims and what a reader acts on.
        # The derive date is not compared -- `derived_at` moves on every run (as
        # `_without_timestamp` already allows for the artifacts), and a check that
        # failed after a clean re-derive would teach everyone to ignore this script.
        claim = f"xOpat {lock['version']}, commit {str(lock['commit'])[:7]}"
        if claim in text:
            return []
        return [
            f"{skill}: its stamp does not claim {claim}; re-run without --check"
        ]
    if current != text:
        skill.write_text(current, encoding="utf-8")
    return []


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--viewer", type=Path, default=DEFAULT_VIEWER, help="xOpat v3 checkout")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output directory, or - for stdout")
    parser.add_argument("--print", action="store_true", help="also print each artifact")
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare against what is on disk and fail on drift; writes nothing",
    )
    parser.add_argument(
        "--fail-open",
        action="store_true",
        help="also assert the open-ness of BackgroundItem/VisualizationItem (slow on every run, so off by default)",
    )
    parser.add_argument("--no-diff", action="store_true", help="skip the diff against the library's tables")
    parser.add_argument(
        "--skill",
        type=Path,
        default=DEFAULT_SKILL,
        help="SKILL.md whose version stamp is refreshed with the contract",
    )
    parser.add_argument(
        "--no-skill",
        action="store_true",
        help="leave SKILL.md alone (its stamp then goes stale, so say so out loud)",
    )
    args = parser.parse_args(argv)

    try:
        artifacts = derive(args.viewer)
    except SourceError as error:
        print(f"derive_schema: {error}", file=sys.stderr)
        return 2

    problems = [] if args.no_diff else diff_against_library(artifacts)
    if args.fail_open:
        problems += open_key_assertions(artifacts, args.viewer)
    for problem in problems:
        print(f"derive_schema: {problem}", file=sys.stderr)

    if args.out == "-":
        for name, payload in artifacts.items():
            print(f"===== {name} =====")
            print(render(name, payload), end="")
        return 1 if problems else 0

    if not args.no_skill:
        # The skill's stamp is part of the contract's blast radius: it teaches key
        # names. Refreshed here rather than in a second command nobody runs.
        # Printed as they are found -- the loop above has already printed `problems`,
        # so a problem added here would otherwise change the exit code in silence,
        # which is the worst kind of failure for a script whose whole job is saying
        # what drifted.
        found = refresh_skill_stamp(
            args.skill, artifacts["viewer.lock.json"], check=args.check
        )
        for line in found:
            print(f"derive_schema: {line}", file=sys.stderr)
        problems += found

    out = Path(args.out)
    drift = []
    for name, payload in artifacts.items():
        text = render(name, payload)
        target = out / name
        if args.check:
            # `derived_at` moves every run, so comparing it would make --check
            # fail on a byte nobody changed. Everything else must match exactly.
            existing = target.read_text(encoding="utf-8") if target.is_file() else None
            if existing is None:
                drift.append(f"{name}: not present in {out}")
            elif _without_timestamp(existing) != _without_timestamp(text):
                drift.append(f"{name}: differs from the current viewer source")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            print(f"wrote {target}")
        if args.print:
            print(f"===== {name} =====\n{text}", end="")

    if drift:
        for line in drift:
            print(f"derive_schema: {line}", file=sys.stderr)
    if problems or drift:
        print(
            "derive_schema: the generated contract and the checked-in one disagree; "
            "re-run without --check and review the diff before committing.",
            file=sys.stderr,
        )
        return 1
    dirty = artifacts["viewer.lock.json"]["inputs_dirty"]
    warning = f"  WARNING: modified inputs {dirty}" if dirty else ""
    if args.check:
        print(
            f"derive_schema: artifacts match the viewer source "
            f"({artifacts['viewer.lock.json']['version']} @ "
            f"{artifacts['viewer.lock.json']['commit'][:8]}) and the library tables"
            f"{warning}"
        )
    else:
        lock = artifacts["viewer.lock.json"]
        print(f"derived from {lock['version']} @ {lock['commit'][:8]}{warning}")
    return 0


def _without_timestamp(text: str) -> str:
    """The rendered artifact with its `derived_at` line removed, for comparison."""
    return "\n".join(
        line for line in text.splitlines() if '"derived_at"' not in line
    )


if __name__ == "__main__":
    sys.exit(main())
