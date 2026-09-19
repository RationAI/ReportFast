"""Where session defaults come from.

Precedence for a built session is **builtin -> preset -> kwargs**. A preset is
*session-shaped*: it uses the viewer's own keys, so anything valid in a
hand-written session is valid in a preset's `params`, and the object reads like
the document it configures.

A preset is built in code and passed to `preset=`. It used to also be readable
from a file named by `$XOPAT_SESSION_CONFIG`, and that is gone: a file the script
never mentions merged invisibly under a script's values, which is the one property
a report must not have. It was also strictly less expressive than the script —
`params`, `plugins`, `layers`, `protocol`, `options`, `lossless` and four endpoint
fields, every one of them settable by passing a `SessionPreset`, an
`XopatEndpoint` or a `Report(theme=...)`. A hidden variable that can set a subset
of what code can set, and is documented nowhere the reader sees, is a second way
to do what the script already does.

A preset cannot carry `data`, `background` or `visualizations`, and now cannot
accidentally be asked to: those lists reference each other by index, so a default
carrying one would silently rewire whatever the caller passed. A frozen dataclass
with four fields cannot express it, which is a better guarantee than a validator
checking for it -- the rule that survives is in the field list, not in an error
message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from .xopat import XopatEndpoint, XopatError


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    """Recursively merge `override` onto `base`.

    Only dicts merge; everything else (including lists) is replaced, because a
    session's lists are positional and a half-inherited list is a silent bug.
    """
    merged: Dict[str, Any] = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        merged[key] = (
            deep_merge(current, value)
            if isinstance(current, Mapping) and isinstance(value, Mapping)
            else value
        )
    return merged


@dataclass(frozen=True)
class SessionPreset:
    """Default material for a session, in the viewer's own vocabulary.

    Attributes:
        params: Session `params`; deep-merged under whatever the caller passes.
        plugins: Plugin id -> config, merged the same way.
        layers: Overlay layers added to every session built without explicit
            ones -- the "my masks always look like this" knob.
        protocol: Slide-protocol name emitted on each background's data entry.
        options: `DataOverride.options` for backgrounds (reader plugin, tile
            format, pixel-size hints).
        lossless: Default for the lossless overlay tiles.
        endpoint: Deployment coordinates to use when the caller passes none.
    """

    params: Dict[str, Any] = field(default_factory=dict)
    plugins: Dict[str, Any] = field(default_factory=dict)
    layers: tuple = ()
    protocol: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    lossless: bool = True
    endpoint: Optional[XopatEndpoint] = None

    def merge(self, other: "SessionPreset") -> "SessionPreset":
        """`other` wins; this preset supplies the values it leaves unset."""
        return SessionPreset(
            params=deep_merge(self.params, other.params),
            plugins=deep_merge(self.plugins, other.plugins),
            layers=other.layers or self.layers,
            protocol=other.protocol or self.protocol,
            options=deep_merge(self.options, other.options),
            lossless=other.lossless,
            endpoint=other.endpoint or self.endpoint,
        )


#: Shipped defaults: lossless overlay tiles (a class map's colours do not
#: survive JPEG) and nothing else. `params` is deliberately empty -- the viewer's
#: own defaults are sane, and every key emitted is bytes on every link.
BUILTIN_PRESET = SessionPreset(lossless=True)


def resolve_preset(preset: Optional[SessionPreset]) -> SessionPreset:
    """Normalise the preset argument a caller may have passed.

    Raises:
        XopatError: on a mapping or a path. Both were accepted while a preset
            could come from a file, and a document is no longer a thing this
            library reads -- the message says so rather than failing with
            argparse-style confusion about an unexpected keyword.
    """
    if preset is None:
        return BUILTIN_PRESET
    if isinstance(preset, SessionPreset):
        return BUILTIN_PRESET.merge(preset) if preset is not BUILTIN_PRESET else preset
    raise XopatError(
        f"`preset` takes a SessionPreset, got {type(preset).__name__}. Passing a mapping "
        "or a file path went with the preset file: build the object "
        "(`SessionPreset(params={...}, endpoint=...)`) so the script says what the "
        "report was built with."
    )


__all__ = [
    "BUILTIN_PRESET",
    "SessionPreset",
    "deep_merge",
    "resolve_preset",
]
