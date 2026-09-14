"""Where session defaults come from.

Precedence for a built session is **builtin -> preset file -> pasted config ->
kwargs**. A preset is *session-shaped*: it uses the viewer's own keys, so
anything valid in a hand-written session is valid in a preset, and a user can
read, diff and version it without learning a second schema.

A preset deliberately cannot carry `data`, `background` or `visualizations`.
Those lists reference each other by index, so a default that shipped one would
silently rewire whatever the user passed -- the slides come from the caller or
from a pasted config, never from a default.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from .xopat import XopatEndpoint, XopatError

#: Environment variable pointing at the preset file users edit.
ENV_CONFIG_VAR = "XOPAT_SESSION_CONFIG"

#: Keys that reference `data[]` by index and therefore cannot be defaulted.
FORBIDDEN_KEYS = ("data", "background", "visualizations")

#: Endpoint fields a preset may pin, so a report aimed at another deployment
#: needs only the preset file.
ENDPOINT_KEYS = ("base_url", "wsi_base_url", "image_protocol", "mount_root")

PRESET_KEYS = frozenset(
    {"params", "plugins", "layers", "protocol", "options", "lossless", *ENDPOINT_KEYS}
)


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
        source: Where the preset was read from, for error messages.
    """

    params: Dict[str, Any] = field(default_factory=dict)
    plugins: Dict[str, Any] = field(default_factory=dict)
    layers: tuple = ()
    protocol: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    lossless: bool = True
    endpoint: Optional[XopatEndpoint] = None
    source: Optional[str] = None

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
            source=other.source or self.source,
        )


#: Shipped defaults: lossless overlay tiles (a class map's colours do not
#: survive JPEG) and nothing else. `params` is deliberately empty -- the viewer's
#: own defaults are sane, and every key emitted is bytes on every link.
BUILTIN_PRESET = SessionPreset(lossless=True)


def parse_preset(
    mapping: Mapping[str, Any], *, source: Optional[str] = None
) -> SessionPreset:
    """Validate one preset document.

    Raises:
        XopatError: on a key the viewer would not read, or on one of
            `FORBIDDEN_KEYS` -- a default that carries data or backgrounds would
            overwrite the caller's slides through their indices.
    """
    if not isinstance(mapping, Mapping):
        raise XopatError(
            f"Session preset must be a mapping, got {type(mapping).__name__}"
        )

    keys = set(mapping)
    forbidden = sorted(keys.intersection(FORBIDDEN_KEYS))
    if forbidden:
        raise XopatError(
            f"Session preset {source or ''}".rstrip()
            + f" cannot define {forbidden}: those lists reference `data[]` by index, so a "
            "default carrying them would silently rewire the slides the caller passed. "
            f"Set defaults in {sorted(PRESET_KEYS)} instead."
        )
    unknown = sorted(keys - PRESET_KEYS)
    if unknown:
        raise XopatError(
            f"Unknown preset keys {unknown}. A preset only carries defaults "
            f"({sorted(PRESET_KEYS)}); slide-specific config belongs in a pasted session."
        )

    layers = mapping.get("layers") or ()
    if isinstance(layers, Mapping):
        layers = (layers,)
    else:
        layers = tuple(layers)

    endpoint = None
    if keys.intersection(ENDPOINT_KEYS):
        endpoint = XopatEndpoint(
            **{key: mapping[key] for key in ENDPOINT_KEYS if key in mapping}
        )

    return SessionPreset(
        params=dict(mapping.get("params") or {}),
        plugins=dict(mapping.get("plugins") or {}),
        layers=layers,
        protocol=mapping.get("protocol"),
        options=dict(mapping.get("options") or {}),
        lossless=bool(mapping.get("lossless", True)),
        endpoint=endpoint,
        source=source,
    )


def load_preset(path: Optional[Union[str, Path]] = None) -> SessionPreset:
    """Read a preset file (`.json`, or `.toml` on Python 3.11+) over the builtin.

    `path` defaults to `$XOPAT_SESSION_CONFIG`; with neither, the builtin
    defaults apply. A missing or unreadable file is an error rather than a
    silent fallback -- a user who set the variable expects their config to
    apply, and quietly ignoring it is the worse failure.
    """
    if path is None:
        path = os.environ.get(ENV_CONFIG_VAR)
    if not path:
        return BUILTIN_PRESET

    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise XopatError(f"Session preset {file_path} does not exist.")

    text = file_path.read_text(encoding="utf-8")
    if file_path.suffix.lower() == ".toml":
        try:
            import tomllib
        except ImportError:  # pragma: no cover - Python 3.10
            raise XopatError(
                f"{file_path} is TOML; reading it needs Python 3.11+. Use JSON."
            ) from None
        raw: Any = tomllib.loads(text)
    else:
        raw = json.loads(text)

    return BUILTIN_PRESET.merge(parse_preset(raw, source=str(file_path)))


def resolve_preset(
    preset: Union[None, SessionPreset, Mapping[str, Any], str, Path],
) -> SessionPreset:
    """Normalise the preset argument a caller may have passed in any shape."""
    if preset is None:
        return load_preset()
    if isinstance(preset, SessionPreset):
        return BUILTIN_PRESET.merge(preset) if preset is not BUILTIN_PRESET else preset
    if isinstance(preset, Mapping):
        return BUILTIN_PRESET.merge(parse_preset(preset))
    return load_preset(preset)


__all__ = [
    "ENV_CONFIG_VAR",
    "FORBIDDEN_KEYS",
    "PRESET_KEYS",
    "BUILTIN_PRESET",
    "SessionPreset",
    "deep_merge",
    "parse_preset",
    "load_preset",
    "resolve_preset",
]
