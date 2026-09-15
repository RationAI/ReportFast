"""The generated xOpat contract, read as Python.

`scripts/derive_schema.py` parses the pinned viewer source into four JSON files
under `report_fast/schema/`. This module is the only reader of them, which is how
"one source, two readers" happens: the skill shows an agent the *same* files the
validator checks against, so a rebuild cannot leave the advice fresh and the gate
stale, or the other way round.

Why generated at all -- the alternative is a hand-typed allowlist, and an
allowlist fails silently. A key that drifts out of the viewer is accepted here,
dropped in the browser, and nobody is told; that is the exact bug class
`xopat.py`'s docstring was written to describe. `derive_schema.py --check`, which
runs as a test, turns the drift into a failure in both directions: keys the viewer
declared and we do not, and keys we carry and it no longer does.

Nothing here reads the viewer checkout or the network. If `schema/` is missing,
the error says to run the derivation rather than falling back to a baked-in copy
-- a fallback would put back the second source of truth this removes.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional

#: Where `derive_schema.py` writes and where this reads. One directory, named in
#: `scripts/derive_schema.py::DEFAULT_OUT` -- keep the two in step.
SCHEMA_DIR = Path(__file__).resolve().parent / "schema"

SCHEMA_FILE = "session.schema.json"
PARAMS_FILE = "params-allowlist.json"
LAYERS_FILE = "layer-fields.json"
LOCK_FILE = "viewer.lock.json"

FILES = (SCHEMA_FILE, PARAMS_FILE, LAYERS_FILE, LOCK_FILE)


class ContractError(RuntimeError):
    """The generated contract is missing or unreadable.

    Carries the command to re-run rather than a bare "file not found", because
    the only fix is to regenerate: these files are never edited by hand.
    """


@lru_cache(maxsize=None)
def _load(name: str) -> Dict[str, Any]:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise ContractError(
            f"the generated xOpat contract has no {name} in {SCHEMA_DIR}. Regenerate "
            "the whole set with `uv run python scripts/derive_schema.py` (needs the "
            "pinned xOpat checkout; see its --viewer)."
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:  # pragma: no cover - guards a truncated write
        raise ContractError(f"{path} is not valid JSON: {error}") from error


def viewer_stamp() -> Dict[str, Any]:
    """Which viewer commit/version/digests these artifacts came from."""
    return dict(_load(LOCK_FILE))


def session_schema() -> Dict[str, Any]:
    """The JSON Schema for a session document, refs inlined."""
    return json.loads(json.dumps(_load(SCHEMA_FILE)))


# ---------------------------------------------------------------------- params


@lru_cache(maxsize=1)
def _params() -> Dict[str, Any]:
    return _load(PARAMS_FILE)


def accepted_param_keys() -> FrozenSet[str]:
    """Top-level `params` keys the viewer keeps.

    This is `XOpatSetup` keys UNION `config.json :: setup` keys, because that is
    what `sanitizeAgainst` (src/app.ts) filters against while the code reads the
    type -- so a key needs only one of the two to survive.
    """
    return frozenset(_params()["accepted"])


def nested_param_keys() -> Dict[str, FrozenSet[str]]:
    """Children the viewer sanitizes one level deeper, e.g. `{"ui": {...}}`."""
    return {key: frozenset(names) for key, names in _params()["nested"].items()}


def ui_param_keys() -> FrozenSet[str]:
    """The `params.ui` vocabulary: `appBar`, `navigator`, `scaleBar`, …"""
    return frozenset(_params()["ui_children"])


def flat_ui_aliases() -> FrozenSet[str]:
    """`params.ui` children that also work spelled flat on `params`.

    Deliberately narrower than `ui_param_keys()`. `getUiOption` does fall back to
    a flat `params[key]`, but `sanitizeAgainst` runs first and strips any
    top-level key the setup defaults do not carry, so most flat ui spellings never
    reach the fallback. Trusting `getUiOption` alone is how four dead keys spent a
    release in `PARAM_KEYS`.
    """
    return frozenset(_params()["flat_ui_aliases_surviving"])


def stripped_flat_ui_aliases() -> FrozenSet[str]:
    """Flat `params.ui` spellings the viewer drops -- accepted by nothing.

    Named so a validation error can say *why* the key failed and where the value
    belongs instead of just "unknown param".
    """
    return frozenset(_params()["flat_ui_aliases_stripped_by_sanitizer"])


def param_keys_for(path: str) -> Optional[FrozenSet[str]]:
    """The closed key set for the params object at dotted `path`, or None.

    `sanitizeAgainst` recurses exactly one level -- into `ui`, because that
    default is a plain object -- so there are precisely two closed sets: `""` for
    the top of `params`, and `"ui"` for its child. Every other path (and any
    deeper one) returns `None`, meaning no vocabulary is declared here, which is
    not the same as "anything goes" and never treated as such by the caller.
    """
    if path in ("", "."):
        return accepted_param_keys()
    if path in nested_param_keys():
        return nested_param_keys()[path]
    return None


# --------------------------------------------------------------- shader layers


@lru_cache(maxsize=1)
def _layers() -> Dict[str, Any]:
    return _load(LAYERS_FILE)


def shader_types() -> FrozenSet[str]:
    """Layer types registered in the pinned viewer's `ShaderLayerRegistry`."""
    return frozenset(_layers()["shader_types"])


def layer_field_names(shader_type: str) -> FrozenSet[str]:
    """Params a layer of `shader_type` declares, shared `opacity` included.

    Generated control families (`icon0`, …) are *not* listed here -- they are
    patterns, not names. Ask `accepts_layer_param`, which knows about them.
    """
    entry = _layers()["layers"].get(shader_type)
    if entry is None:
        return frozenset()
    return frozenset(entry["fields"]) | frozenset(_layers()["common_controls"])


def layer_field_families(shader_type: str) -> Dict[str, str]:
    """`{definition_key: name_template}` for one-param-per-data-interval controls."""
    entry = _layers()["layers"].get(shader_type) or {}
    return dict(entry.get("control_families", {}))


def layer_param_aliases(shader_type: str) -> Dict[str, str]:
    """snake_case <-> camelCase spellings the viewer declares for one control.

    The viewer names controls inconsistently (`outer_color` beside
    `edgeThickness`), so "is this key legal" needs both spellings in play.
    """
    return dict(_layers()["aliases"].get(shader_type, {}))


def accepts_layer_param(shader_type: str, key: str) -> bool:
    """Whether a layer of `shader_type` may carry `key`, by any legal spelling."""
    if key in layer_field_names(shader_type):
        return True
    if key in layer_param_aliases(shader_type):
        return True
    if key.startswith("use_"):
        # Filter/blend flags, skipped by `_buildControls` and read by the shader.
        return True
    template_suffixes = _family_parts(shader_type)
    return any(_matches_family(key, prefix, suffix) for prefix, suffix in template_suffixes)


def _family_parts(shader_type: str) -> List[tuple]:
    parts = []
    for template in layer_field_families(shader_type).values():
        prefix, _, suffix = template.partition("{N}")
        parts.append((prefix, suffix))
    return parts


def _matches_family(key: str, prefix: str, suffix: str) -> bool:
    if not key.startswith(prefix) or not key.endswith(suffix):
        return False
    middle = key[len(prefix) : len(key) - len(suffix) if suffix else None]
    return middle.isdigit()


def layer_documentation(shader_type: str) -> Dict[str, Any]:
    """Title, description and the viewer's own control docs for one layer type."""
    entry = _layers()["layers"].get(shader_type)
    if entry is None:
        raise ContractError(
            f"{shader_type!r} is not a registered shader layer; the viewer registers "
            f"{sorted(shader_types())}."
        )
    return dict(entry)


def index_fields() -> FrozenSet[str]:
    """Layer fields whose values are indices into the session's `data[]`."""
    return frozenset(_layers()["index_fields"])


# ------------------------------------------------------------------ publishing


def dump(kind: str = "all") -> str:
    """The contract as text, for an agent or a terminal. `kind` names one file."""
    choices = {
        "params": PARAMS_FILE,
        "layers": LAYERS_FILE,
        "session": SCHEMA_FILE,
        "lock": LOCK_FILE,
    }
    if kind == "all":
        return "\n\n".join(
            f"===== {name} =====\n" + json.dumps(_load(name), indent=2, sort_keys=True)
            for name in FILES
        )
    if kind not in choices:
        raise ContractError(
            f"unknown contract part {kind!r}; ask for one of "
            f"{sorted(choices) + ['all']}."
        )
    return json.dumps(_load(choices[kind]), indent=2, sort_keys=True)


def summary() -> Mapping[str, Any]:
    """A one-glance description of what this contract covers."""
    stamp = viewer_stamp()
    return {
        "viewer": f"{stamp.get('version')} @ {str(stamp.get('commit'))[:8]}",
        "params_keys": len(accepted_param_keys()),
        "shader_types": len(shader_types()),
        "schema_top_level": sorted(session_schema().get("properties", {})),
    }


__all__ = [
    "ContractError",
    "FILES",
    "SCHEMA_DIR",
    "accepted_param_keys",
    "accepts_layer_param",
    "dump",
    "flat_ui_aliases",
    "index_fields",
    "layer_documentation",
    "layer_field_families",
    "layer_field_names",
    "layer_param_aliases",
    "nested_param_keys",
    "param_keys_for",
    "session_schema",
    "shader_types",
    "stripped_flat_ui_aliases",
    "summary",
    "ui_param_keys",
    "viewer_stamp",
]
