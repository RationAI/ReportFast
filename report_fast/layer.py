"""The two overlay layers a `Mask` can draw, as plain dicts.

This is the only place in the library that names a shader type. It exists
because `Mask(color=...)` and `Mask(classes=..., palette=...)` are claims about
how a file gets drawn -- a colour means *tint this as a heatmap*, a palette
means *map these class values through these colours* -- and the claim has to
turn into a layer dict somewhere. Here is that somewhere: two small builders,
no vocabulary tables, no validation. The dicts they return go into the session
as written; the viewer decides what to do with them, which is the arrangement
everywhere else too.

The shapes come from xOpat v3's flex-renderer, commit `18c94f2b`. Read it
(`src/libs/flex-renderer/flex-renderer.js`) before changing either builder --
in particular the `colormap` coupling the viewer calls `colormap_class_count`,
which requires the palette length to be one more than the number of breaks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union


#: What a bare mask path draws as. `xopat.normalise_layer` reaches for this
#: rather than naming a type itself, and the viewer needs a `type` to exist --
#: a layer without one is deleted on load, silently.
DEFAULT_LAYER_TYPE = "heatmap"


def _to_hex(color: Union[str, Sequence[float]]) -> str:
    """`#rrggbb` passes through; an RGB tuple in 0..1 becomes one."""
    if isinstance(color, str):
        return color
    red, green, blue = (round(float(channel) * 255) for channel in color[:3])
    return f"#{red:02x}{green:02x}{blue:02x}"


def heatmap_layer(
    data_id: str,
    *,
    name: str = "",
    color: str = "#fff705",
    opacity: float = 1.0,
    threshold: float = 1.0,
    inverse: bool = False,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """A scalar overlay tinted one colour: values drive alpha.

    `threshold` drops the dimmest values; `inverse` draws below it instead of
    above. `params` is merged last and wins, so a field this builder has not
    modelled (a `use_channel0`) is added there and ships unchanged.
    """
    return {
        "path": data_id,
        "type": "heatmap",
        "name": name,
        "params": {
            "color": _to_hex(color),
            "threshold": float(threshold),
            "inverse": bool(inverse),
            "opacity": opacity,
            **(params or {}),
        },
    }


def colormap_layer(
    data_id: str,
    *,
    name: str = "",
    classes: int,
    palette: Sequence[Union[str, Sequence[float]]],
    opacity: float = 1.0,
    breaks: Optional[Sequence[float]] = None,
    mask: Optional[Sequence[int]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """A class map: class values through a palette, one bin per class.

    Args:
        classes: Number of classes; the palette length.
        palette: One colour per class, hex or an RGB tuple in 0..1.
        breaks: `classes - 1` bin boundaries; defaults to even cuts over 0..1.
        mask: One 0/1 per class selecting which are drawn; `[0, 1, 1]` leaves
            class 0 -- usually the background -- unpainted.
    """
    if len(palette) != classes:
        raise ValueError(
            f"{name or data_id}: got {len(palette)} colours for {classes} classes"
        )
    if classes < 2:
        raise ValueError(f"{name or data_id}: a class map needs at least 2 classes")
    if breaks is not None and len(breaks) != classes - 1:
        raise ValueError(
            f"{name or data_id}: {classes} classes need {classes - 1} breaks, got {len(breaks)}"
        )
    if mask is not None and len(mask) != classes:
        raise ValueError(
            f"{name or data_id}: {classes} classes need {classes} mask flags, got {len(mask)}"
        )
    cuts: List[float] = (
        list(breaks)
        if breaks is not None
        else [round(index / classes, 4) for index in range(1, classes)]
    )
    return {
        "path": data_id,
        "type": "colormap",
        "name": name,
        "params": {
            "color": {
                "type": "custom_colormap",
                "default": [_to_hex(color) for color in palette],
                "steps": classes,
            },
            "threshold": {
                "type": "advanced_slider",
                "breaks": cuts,
                "mask": list(mask) if mask is not None else [1] * classes,
            },
            "connect": True,
            "opacity": opacity,
            **(params or {}),
        },
    }
