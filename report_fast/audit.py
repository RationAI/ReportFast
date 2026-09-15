"""Walking a session config for everything that would go wrong in the browser.

`XopatSession.validate` asked about a handful of fields. This asks about all of
them, and answers in the unit a person or an agent can act on: the **JSON path**.
"The param `threshold` is not declared" sends you hunting; "the param `threshold`
in `visualizations[0].shaders.dose.params` is not declared by `heatmap`, which
declares [color, inverse, opacity, threshold, use_channel0]" ends the search.

Why a separate module from `validate`: the same questions are asked of three
different bodies of JSON -- a session this library built, a session pasted by a
user, and a session authored by an agent through the CLI's temp-dir door -- and
each gets a different *verdict* on the same findings. So the walk reports and
never raises, and the caller decides. That split is the whole point:

  * **agent path -- strict.** A finding the viewer would merely swallow is an
    error here. Out of the allowlist means a typo until proven otherwise, and the
    proof is cheap when the message already says which line.
  * **paste path -- warn and keep.** Surviving verbatim *is* the feature; refusing
    a colleague's session because it sets a key we have not transcribed would make
    the tool the least forgiving reader of a format the viewer itself reads
    leniently.

Two kinds stay errors on both paths, because both are lies the page tells:
a reference into `data[]` that is not there (the viewer refuses to boot), and an
unregistered shader type (`createShaderLayer` throws and the layer vanishes).

The key-vocabulary questions are answered by `report_fast.contract`, so they are
asked against the pinned viewer rather than against this file's memory of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Sequence

from . import contract
from .shader import (
    FILTER_PARAM_PREFIX,
    UNFILTERED_PARAM_TYPES,
    ShaderType,
    UnknownShaderTypeError,
    accepts_param,
    declared_params,
)
from .xopat import param_keys

#: What the walk can be wrong about, and how each is treated.
#:
#: Always an error (the page would not load, or would load showing the wrong
#: thing, which is worse than not loading):
#:   "reference" -- an index into data[]/visualizations[] that is not there
#:   "structure" -- a required field missing, a list where an object belongs
#:   "layer"     -- a shader type the viewer does not register
#:
#: An error on the agent path, a warning on the paste path (the viewer drops it
#: quietly, so the only moment anyone can be told is here):
#:   "param"     -- a params key, or a layer param, outside the declared vocabulary
#:   "key"       -- a top-level session key the viewer does not read
ERROR_KINDS = frozenset({"reference", "structure", "layer"})
SOFT_KINDS = frozenset({"param", "key"})

#: DataIDs are strings or `{...}` objects; anything else the viewer coerces in a
#: way no one would call intended.
MAX_REPORTED = 12


@dataclass(frozen=True)
class Finding:
    """One thing the walk would want to change, at the path where it lives.

    Attributes:
        path: JSON path in the viewer's own bracket style, e.g.
            `visualizations[0].shaders.dose.params.threshold`. Matches the shape
            of the viewer's `Ignoring unsupported viewer parameters: ui.nope`
            warning so the two can be read side by side.
        kind: One of `ERROR_KINDS` | `SOFT_KINDS`; decides severity.
        message: What is wrong, what the legal alternatives are, and what the
            viewer would do instead. Written to be the last sentence of an error.
    """

    path: str
    kind: str
    message: str

    @property
    def text(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message

    def __str__(self) -> str:  # pragma: no cover - convenience for callers
        return self.text


def audit(config: Mapping[str, Any], *, carried: frozenset = frozenset()) -> List[Finding]:
    """Every finding in a session-shaped mapping. Never raises.

    Accepts the raw dict rather than a `XopatSession` on purpose: the agent's
    door hands us JSON that has not survived coercion yet, and a walk that needed
    the object would only be able to inspect sessions already known to be good.

    Args:
        config: The session document.
        carried: JSON paths whose vocabulary finding the caller has already
            claimed -- the paths made by `ShaderConfig.with_params` / a manifest
            mask row's `params:`, which say in so many words "this library has not
            modelled the field, keep it". Those were warned about once, at the
            call where the caller's intent was still on the table, and re-raising
            them here would turn a documented door into an error that no opt-out
            reaches. Nothing else is silenced: the viewer's behaviour is not a
            matter of opinion, and a caller who wants a *dangling reference* kept
            has asked for a page that does not load.
    """
    if not isinstance(config, Mapping):
        return [
            Finding(
                "",
                "structure",
                f"a session is a JSON object, got {type(config).__name__}",
            )
        ]
    findings: List[Finding] = []
    data = config.get("data")
    background = config.get("background")
    visualizations = config.get("visualizations")
    size = len(data) if isinstance(data, Sequence) and not isinstance(data, str) else 0
    viz_count = len(visualizations) if isinstance(visualizations, list) else 0

    findings += _audit_keys(config)
    findings += _audit_params(config.get("params"))
    findings += _audit_data(config.get("data"))
    findings += _audit_background(background, size, viz_count)
    findings += _audit_visualizations(visualizations, size)
    if carried:
        findings = [finding for finding in findings if finding.path not in carried]
    return findings


def split(
    findings: Sequence[Finding], *, strict: bool
) -> tuple[List[Finding], List[Finding]]:
    """`(errors, warnings)` for a verdict.

    `strict` is the agent path. Non-strict keeps the paste path's promise: keep
    the bytes, say what the viewer will drop.
    """
    errors, warnings_ = [], []
    for finding in findings:
        if finding.kind in ERROR_KINDS or strict:
            errors.append(finding)
        else:
            warnings_.append(finding)
    return errors, warnings_


def join(findings: Sequence[Finding], limit: int = MAX_REPORTED) -> str:
    """Findings as one sentence, bounded so the first error stays readable."""
    shown = [finding.text for finding in findings[:limit]]
    extra = len(findings) - len(shown)
    if extra > 0:
        shown.append(f"and {extra} more")
    return "; ".join(shown)


# ---------------------------------------------------------------------------- walk


def _audit_keys(config: Mapping[str, Any]) -> List[Finding]:
    findings = []
    known = set(contract.session_schema().get("properties", {}))
    for key in config:
        if key in known:
            continue
        findings.append(
            Finding(
                str(key),
                "key",
                _session_key_hint(str(key))
                or "not a key the viewer reads (known: "
                f"{sorted(known)}). It is forwarded to plugins, so it may be "
                "someone's plugin config -- if you did not put it here on purpose, "
                "it is a typo",
            )
        )
    return findings


def _session_key_hint(key: str) -> str:
    """The root-level key that is real but in the wrong place.

    `sessionName` is the one that actually happens: the viewer reads it from
    *merged* params (`CONFIG.params["sessionName"]`, src/app.ts:176), so a
    session that saved itself can carry it at the root alongside
    `params.sessionName` and look fine, and an agent that read the viewer's type
    for it will write it at the root and set nothing. Naming the destination
    settles it; "unknown key" would send someone to diff the schema.
    """
    if key == "sessionName":
        return (
            "the viewer reads it from params.sessionName, not from the session "
            "root (src/app.ts: `CONFIG.params[\"sessionName\"]`); as a root key it "
            "is forwarded to plugins and otherwise ignored -- and note it is a "
            "persistence namespace for the viewer's own saved state, not a title "
            "for your report"
        )
    return ""


def _audit_params(params: Any) -> List[Finding]:
    if params is None:
        return []
    if not isinstance(params, Mapping):
        return [Finding("params", "structure", f"must be an object, got {_shape(params)}")]
    allowed = param_keys()
    findings = []
    for key, value in params.items():
        if key not in allowed:
            hint = _param_hint(key)
            findings.append(
                Finding(
                    f"params.{key}",
                    "param",
                    f"not in the viewer's params allowlist{hint}; `sanitizeAgainst` "
                    "(src/app.ts) drops it and logs to the console, so the viewer "
                    "boots as if you had not set it",
                )
            )
        if key == "ui" and isinstance(value, Mapping):
            findings += _audit_ui(value)
    return findings


def _audit_ui(ui: Mapping[str, Any]) -> List[Finding]:
    """`params.ui` is the one nested object the viewer filters recursively."""
    allowed = contract.ui_param_keys()
    findings = []
    for key in ui:
        if key in allowed:
            continue
        findings.append(
            Finding(
                f"params.ui.{key}",
                "param",
                f"not a UI flag the viewer declares (known: {sorted(allowed)})",
            )
        )
    return findings


def _param_hint(key: str) -> str:
    """Where a rejected key probably belonged, when we can tell.

    `appBar`/`navigator`/`mainMenu`/`globalMenu` are the interesting cases: they
    *are* real viewer settings, and `getUiOption` even has a flat fallback that
    looks like it honors them -- but `sanitizeAgainst` runs first and strips them,
    so the flat spelling has been dead JSON for as long as I have been able to
    check. Saying "unknown param" alone would send someone to argue with the
    viewer's source.
    """
    if key in contract.stripped_flat_ui_aliases():
        return f" -- it is a `params.ui` key: nest it as params.ui.{key}"
    if key in contract.ui_param_keys():
        return f" -- nest it as params.ui.{key}"
    return ""


def _audit_data(data: Any) -> List[Finding]:
    if data is None:
        return []
    if not isinstance(data, list):
        return [Finding("data", "structure", f"must be an array, got {_shape(data)}")]
    findings = []
    for index, entry in enumerate(data):
        path = f"data[{index}]"
        if isinstance(entry, Mapping):
            if "dataID" not in entry:
                findings.append(
                    Finding(
                        path,
                        "structure",
                        "a data object must carry `dataID`; without it the entry is "
                        "not a data specification and nothing can reference it",
                    )
                )
            protocol = entry.get("protocol")
            if isinstance(protocol, str):
                findings += _audit_protocol(f"{path}.protocol", protocol)
            continue
        if not isinstance(entry, str):
            findings.append(
                Finding(
                    path,
                    "structure",
                    f"a DataID is a string or a {{dataID: ...}} object, got {_shape(entry)}",
                )
            )
    return findings


def _audit_background(background: Any, data_size: int, viz_count: int) -> List[Finding]:
    if background is None:
        return []
    if not isinstance(background, list):
        return [
            Finding("background", "structure", f"must be an array, got {_shape(background)}")
        ]
    findings = []
    for index, entry in enumerate(background):
        path = f"background[{index}]"
        if not isinstance(entry, Mapping):
            findings.append(
                Finding(path, "structure", f"a background is an object, got {_shape(entry)}")
            )
            continue
        reference = entry.get("dataReference")
        if reference is None:
            findings.append(
                Finding(
                    f"{path}.dataReference",
                    "structure",
                    "missing -- every background names one data entry, and the viewer "
                    "will not boot without it",
                )
            )
        elif isinstance(reference, int) and not 0 <= reference < data_size:
            findings.append(
                Finding(
                    f"{path}.dataReference",
                    "reference",
                    f"{reference} is outside data[] (0..{data_size - 1}); the viewer "
                    "refuses to open the session at all",
                )
            )
        visualization = entry.get("visualizationIndex")
        if isinstance(visualization, int) and not 0 <= visualization < viz_count:
            findings.append(
                Finding(
                    f"{path}.visualizationIndex",
                    "reference",
                    f"{visualization} is outside visualizations[] "
                    f"(0..{viz_count - 1}); this background shows no overlay",
                )
            )
        protocol = entry.get("protocol")
        if isinstance(protocol, str):
            findings += _audit_protocol(f"{path}.protocol", protocol)
    return findings


def _audit_visualizations(visualizations: Any, data_size: int) -> List[Finding]:
    if visualizations is None:
        return []
    if not isinstance(visualizations, list):
        return [
            Finding(
                "visualizations", "structure", f"must be an array, got {_shape(visualizations)}"
            )
        ]
    findings: List[Finding] = []
    for index, visualization in enumerate(visualizations):
        path = f"visualizations[{index}]"
        if not isinstance(visualization, Mapping):
            findings.append(
                Finding(path, "structure", f"a visualization is an object, got {_shape(visualization)}")
            )
            continue
        shaders = visualization.get("shaders")
        if shaders is None:
            continue
        if not isinstance(shaders, Mapping):
            findings.append(
                Finding(
                    f"{path}.shaders",
                    "structure",
                    "must be an object keyed by layer id (v2 used an array); "
                    f"got {_shape(shaders)}",
                )
            )
            continue
        for layer_id, layer in shaders.items():
            findings += _audit_layer(layer, f"{path}.shaders.{layer_id}", data_size)
    return findings


def _audit_layer(layer: Any, path: str, data_size: int) -> List[Finding]:
    if not isinstance(layer, Mapping):
        return [Finding(path, "structure", f"a shader layer is an object, got {_shape(layer)}")]
    findings: List[Finding] = []

    shader_type = layer.get("type")
    if shader_type is None:
        findings.append(
            Finding(
                f"{path}.type",
                "structure",
                "missing -- `layers.js` deletes any layer without a type, and a group "
                "is only inferred from a nested `shaders` map",
            )
        )
    else:
        try:
            ShaderType(shader_type)
        except (UnknownShaderTypeError, ValueError) as error:
            findings.append(Finding(f"{path}.type", "layer", str(error)))

    for field in ("dataReferences", "tiledImages"):
        value = layer.get(field)
        if value is None:
            continue
        references = value if isinstance(value, (list, tuple)) else [value]
        dangling = [
            ref
            for ref in references
            if isinstance(ref, int) and not 0 <= ref < data_size
        ]
        if dangling:
            findings.append(
                Finding(
                    f"{path}.{field}",
                    "reference",
                    f"{dangling} point outside data[] (0..{data_size - 1}), so the "
                    "layer binds nothing and renders its defaults",
                )
            )
        elif not isinstance(value, (list, tuple)):
            findings.append(
                Finding(
                    f"{path}.{field}",
                    "structure",
                    f"must be an array of data indices, got {_shape(value)}",
                )
            )

    params = layer.get("params")
    if params is not None:
        findings += _audit_layer_params(params, f"{path}.params", shader_type)

    nested = layer.get("shaders")
    if isinstance(nested, Mapping):
        for layer_id, child in nested.items():
            findings += _audit_layer(child, f"{path}.shaders.{layer_id}", data_size)
    return findings


def _audit_layer_params(params: Any, path: str, shader_type: Any) -> List[Finding]:
    if not isinstance(params, Mapping):
        return [Finding(path, "structure", f"must be an object, got {_shape(params)}")]
    if not isinstance(shader_type, str) or shader_type not in contract.shader_types():
        # An absent or unregistered type is reported as `structure`/`layer` above.
        # Guessing a vocabulary for a type we do not know would invent findings.
        return []
    if shader_type in UNFILTERED_PARAM_TYPES:
        # `group` names member layers and `interaction-debug` carries a free-form
        # descriptor; neither has a closed param set to check against.
        return []
    declared = sorted(declared_params(shader_type))
    findings = []
    for key in params:
        if accepts_param(shader_type, key):
            continue
        findings.append(
            Finding(
                f"{path}.{key}",
                "param",
                f"not declared by {shader_type!r}, which declares {declared} plus any "
                f"`{FILTER_PARAM_PREFIX}*` filter flag. v3 never binds an undeclared "
                "param, so the layer renders its defaults and the image looks plausible",
            )
        )
    return findings


def _audit_protocol(path: str, protocol: str) -> List[Finding]:
    if "`" in protocol or "${" in protocol:
        return [
            Finding(
                path,
                "structure",
                f"{protocol!r} is an inline template; v3 rejects these unconditionally "
                "(src/classes/slide-protocols.ts, an RCE sink) and falls back to the "
                "deployment default",
            )
        ]
    return []


def _shape(value: Any) -> str:
    """How to name a wrong value in a message without printing a giant blob."""
    if isinstance(value, Mapping):
        return f"an object with keys {sorted(str(key) for key in list(value)[:6])}"
    if isinstance(value, (list, tuple)):
        return f"a list of {len(value)}"
    if isinstance(value, str):
        return f"the string {value[:40]!r}" if len(value) > 40 else repr(value)
    return f"{type(value).__name__} {value!r}"


__all__ = ["Finding", "audit", "split", "join", "ERROR_KINDS", "SOFT_KINDS"]
