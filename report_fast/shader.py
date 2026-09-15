"""Shader configuration for xOpat v3 WebGL overlays.

The "Shader Problem": overlay layers are described by a JSON blob the viewer
hands to a WebGL renderer, and a wrong key does not fail loudly -- v3 simply
never binds it to a control, so the layer renders with defaults and the report
author sees a plausible but wrong image.

This module is the validation layer that keeps a layer description inside what
xOpat v3 actually declares. The tables below are transcribed from
`src/libs/flex-renderer/flex-renderer.js` (each layer's `static docs().controls`
and `static get defaultControls()`); `report_fast.xopat` emits the layers.

v2 -> v3 differences encoded here:

  * `classify`, `segmentation` and `bounding_box` shaders no longer exist.
    Class maps are a `colormap` layer with a `custom_colormap` palette.
  * A layer is keyed by `type`, not `shader`, and binds data through
    `dataReferences` (indices into the session's `data` array).
  * `opacity` is now a *param* (v3 adds an `opacity` control to every layer)
    instead of a sibling of `params`.
"""

from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from . import contract


class ShaderType(str, Enum):
    """Shader layer types registered in xOpat v3's `$.FlexRenderer.ShaderLayerRegistry`.

    Anything else makes `$.FlexRenderer::createShaderLayer` throw, which drops
    the whole layer.
    """

    ADAPTIVE_THRESHOLD = "adaptive_threshold"
    BIPOLAR_HEATMAP = "bipolar-heatmap"
    CHANNEL_SERIES = "channel-series"
    COLORMAP = "colormap"
    EDGE = "edge"
    FISHEYE_LENS = "fisheye-lens"
    GRID = "grid"
    GRIDHEATMAP = "gridheatmap"
    GROUP = "group"
    HEATMAP = "heatmap"
    ICONMAP = "iconmap"
    IDENTITY = "identity"
    INTERACTION_DEBUG = "interaction-debug"
    PATTERNMAP = "patternmap"
    SINGLE_CHANNEL = "single_channel"
    SOBEL = "sobel"
    STAIN_SEPARATION = "stain-separation"
    TEXTURE = "texture"
    THRESHOLD = "threshold"
    TIME_SERIES = "time-series"


#: Declared params per shader type. Every layer also accepts `opacity`, which
#: v3 injects in `ShaderLayer._buildControls()` unless the layer overrides it.
SHADER_PARAMS: Dict[ShaderType, frozenset] = {
    ShaderType.ADAPTIVE_THRESHOLD: frozenset(
        {"block_size", "c_value", "gaussian", "invert", "fg_color", "bg_color"}
    ),
    ShaderType.BIPOLAR_HEATMAP: frozenset({"colorHigh", "colorLow", "threshold"}),
    ShaderType.CHANNEL_SERIES: frozenset({"channel_offset"}),
    ShaderType.COLORMAP: frozenset({"color", "threshold", "connect"}),
    ShaderType.EDGE: frozenset(
        {"use_channel0", "threshold", "outer_color", "inner_color", "edgeThickness"}
    ),
    ShaderType.FISHEYE_LENS: frozenset(
        {
            "use_channel0",
            "radiusPx",
            "zoom",
            "featherPx",
            "falloffPower",
            "buttonMask",
            "showGuides",
            "guideOpacity",
            "guideWidthPx",
            "guideColor",
        }
    ),
    ShaderType.GRID: frozenset(
        {
            "use_channel0",
            "color",
            "cell_x",
            "cell_y",
            "offset_x",
            "offset_y",
            "line_width",
            "adaptive_lod",
        }
    ),
    ShaderType.GRIDHEATMAP: frozenset(
        {
            "color",
            "threshold",
            "connect",
            "cell",
            "offset_x",
            "offset_y",
            "solid_px",
            "boundary_px",
            "adaptive_lod",
        }
    ),
    ShaderType.GROUP: frozenset(),
    ShaderType.HEATMAP: frozenset({"use_channel0", "color", "threshold", "inverse"}),
    ShaderType.ICONMAP: frozenset(
        {
            "use_channel0",
            "threshold",
            "grid_layout",
            "cell_size",
            "jitter",
            "icon_scale",
            "clip_icons",
        }
    ),
    ShaderType.IDENTITY: frozenset({"use_channel0"}),
    ShaderType.INTERACTION_DEBUG: frozenset(),
    ShaderType.PATTERNMAP: frozenset(
        {
            "use_channel0",
            "color",
            "threshold",
            "inverse",
            "pattern_type",
            "spacing",
            "line_width",
            "offset_x",
            "offset_y",
            "rotation",
        }
    ),
    ShaderType.SINGLE_CHANNEL: frozenset(
        {
            "use_channel0",
            "color",
            "window_low",
            "window_high",
            "opaque",
            "threshold",
        }
    ),
    ShaderType.SOBEL: frozenset({"use_channel0"}),
    ShaderType.STAIN_SEPARATION: frozenset(
        {
            "use_channel0",
            "preset",
            "stain",
            "style",
            "tintColor",
            "intensity",
        }
    ),
    ShaderType.TEXTURE: frozenset({"use_channel0", "texture"}),
    ShaderType.THRESHOLD: frozenset(
        {
            "threshold",
            "max_value",
            "version",
            "colorize_binary",
            "fg_color",
            "bg_color",
        }
    ),
    ShaderType.TIME_SERIES: frozenset({"timeline"}),
}

#: Param every layer accepts regardless of type.
COMMON_PARAMS = frozenset({"opacity"})

#: Layers whose params are free-form descriptors rather than scalar controls: a
#: `group` names member layers, `interaction-debug` carries one opaque config
#: block. Neither has a closed param vocabulary, so filtering their keys would
#: delete the very feature they exist to reach.
UNFILTERED_PARAM_TYPES = frozenset({ShaderType.GROUP, ShaderType.INTERACTION_DEBUG})

#: Filter/blend flags rather than UI controls; `_buildControls` skips every
#: `use_`-prefixed key, so they carry values the shader reads without ever
#: appearing in a layer's `controls`.
FILTER_PARAM_PREFIX = "use_"


@dataclass(frozen=True)
class ParamFamily:
    """Controls the viewer *generates* rather than declares.

    `_expandControlDefinitions` (flex-renderer.js) turns an `array:` control
    definition into one real control per data interval, named by a template:
    `icons` becomes `icon0`, `icon1`, … up to the layer's class count. Neither the
    definition's key (`icons`) nor its docs spelling (`iconN`) is a param a session
    ever carries, so neither belongs in `SHADER_PARAMS` -- what belongs here is the
    shape of the names the viewer actually reads.

    `template` is spelled the way `derive_schema.py` publishes it (`icon{N}` for
    the viewer's ``icon${index}``), so the two can be compared literally instead of
    matching one regex against another -- which would be two unfalsifiable claims
    about each other. The three sources name one family three ways (`icons` /
    `iconN` / `icon{N}`); `derive_schema.py` folds those when it diffs this table
    against the viewer, so nothing here has to.
    """

    template: str

    @property
    def pattern(self) -> str:
        prefix, _, suffix = self.template.partition("{N}")
        return f"^{re.escape(prefix)}\\d+{re.escape(suffix)}$"

    def accepts(self, key: str) -> bool:
        return bool(re.fullmatch(self.pattern, key))


#: Per-type generated control families, keyed by the type that produces them.
SHADER_PARAM_FAMILIES: Dict[ShaderType, ParamFamily] = {
    ShaderType.ICONMAP: ParamFamily(template="icon{N}"),
}


def accepts_param(shader_type: Union[ShaderType, str], key: str) -> bool:
    """Whether a layer of `shader_type` may carry the param `key`.

    The single answer to a question three call sites used to each reimplement --
    and each was free to drift. Union of the two sources, because each carries
    something the other structurally cannot: the hand table states what we have
    reviewed and chosen to emit, the generated contract knows what the viewer
    declares under spellings (`edgeThickness` beside `outer_color`) and shapes
    (one control per class interval) no fixed list can hold.
    """
    key_type = _coerce_shader_type(shader_type)
    if key in allowed_params(key_type) or key.startswith(FILTER_PARAM_PREFIX):
        return True
    family = SHADER_PARAM_FAMILIES.get(key_type)
    if family and family.accepts(key):
        return True
    return contract.accepts_layer_param(key_type.value, key)


def declared_params(shader_type: Union[ShaderType, str]) -> frozenset:
    """Every param name a layer of this type accepts, spelled as the sources spell it.

    The list an error message prints, so it has to be a *union* like
    `accepts_param` is -- a message that says "declares [threshold]" while
    `accepts_param` also permits `thresholdLow` teaches the reader to ignore the
    message. Generated families appear as their template (`icon{N}`), since their
    members are per-slide and no fixed list of them exists to print. `use_*`
    filter flags are accepted by prefix rather than by name, so they are not
    listed here; the caller says so in prose.
    """
    key = _coerce_shader_type(shader_type)
    names = set(allowed_params(key))
    names.update(contract.layer_field_names(key.value))
    names.update(contract.layer_param_aliases(key.value))
    family = SHADER_PARAM_FAMILIES.get(key)
    if family:
        names.add(family.template)
    return frozenset(names)


#: Layers whose colour palette size is pinned to `threshold.breaks.length + 1`
#: by the `colormap_class_count` control coupling.
PALETTE_COUPLED_TYPES = frozenset({ShaderType.COLORMAP, ShaderType.GRIDHEATMAP})

#: Maximum palette length accepted by `custom_colormap` (`MAX_SAMPLES`).
MAX_PALETTE_COLORS = 32

#: v2 shader names and the v3 layer that replaces them.
RETIRED_SHADER_TYPES: Dict[str, str] = {
    "classify": ShaderType.COLORMAP.value,
    "segmentation": ShaderType.COLORMAP.value,
    "bounding_box": ShaderType.ICONMAP.value,
}


class UnknownShaderTypeError(ValueError):
    """Raised for a shader type xOpat v3 does not register."""


def allowed_params(shader_type: Union[ShaderType, str]) -> frozenset:
    """Params a layer of `shader_type` may carry, including the shared `opacity`.

    `SHADER_PARAMS` is the runtime answer on purpose. The generated contract
    holds the same list parsed from the viewer, and `derive_schema.py --check`
    fails if the two disagree in either direction -- but a check only has teeth
    if both sides are independent, so the hand table is not a view onto the
    artifact. What the artifact knows and this table does not (camel/snake spellings,
    generated control families, the `ui` param tree) arrives through
    `accepts_param`, which consults both.
    """
    key = _coerce_shader_type(shader_type)
    return SHADER_PARAMS[key] | COMMON_PARAMS


def _coerce_shader_type(shader_type: Union[ShaderType, str]) -> ShaderType:
    if isinstance(shader_type, ShaderType):
        return shader_type
    value = str(shader_type)
    if value in RETIRED_SHADER_TYPES:
        raise UnknownShaderTypeError(
            f"Shader type {value!r} was removed in xOpat v3. "
            f"Use {RETIRED_SHADER_TYPES[value]!r} instead."
        )
    try:
        return ShaderType(value)
    except ValueError as exc:
        raise UnknownShaderTypeError(
            f"Unknown shader type {value!r}; v3 registers "
            f"{sorted(t.value for t in ShaderType)}."
        ) from exc


def _to_hex(color: Union[str, Sequence[float]]) -> str:
    """Normalise a hex string or an RGB(A) tuple in 0..1 to `#rrggbb`.

    xOpat accepts 3-digit shorthand but hands palette strings straight to its
    colour parser, so expand them here rather than relying on the viewer.
    """
    if isinstance(color, str):
        value = color if color.startswith("#") else f"#{color}"
        if len(value) == 4:
            return "#" + "".join(character * 2 for character in value[1:])
        return value
    channels = [
        max(0, min(255, round(float(component) * 255))) for component in color[:3]
    ]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


class ShaderDataType(str, Enum):
    """Value shapes xOpat shader params may take."""

    FLOAT = "float"
    INT = "int"
    BOOL = "bool"
    STRING = "string"
    RGB = "vec3"
    RGBA = "vec4"
    LIST_RGB = "list_vec3"  # Palette: list of colors (custom_colormap)
    OBJECT = "object"  # Nested UI-control descriptor (e.g. colormap params)


def _inferred_type(value: Any) -> ShaderDataType:
    """The declared type a raw value looks like, for `with_params`.

    Only a guess, and only ever used for a parameter this library does not
    model -- a modelled one states its type. A bool is tested before int
    because bool is a subclass of int, and a list of hex strings before the
    plain list, since a palette is the commonest list a layer carries.
    """
    if isinstance(value, bool):
        return ShaderDataType.BOOL
    if isinstance(value, int):
        return ShaderDataType.INT
    if isinstance(value, float):
        return ShaderDataType.FLOAT
    if isinstance(value, str):
        return ShaderDataType.RGB if value.startswith("#") else ShaderDataType.STRING
    if isinstance(value, Mapping):
        return ShaderDataType.OBJECT
    if isinstance(value, (list, tuple)):
        every = list(value)
        if every and all(isinstance(item, str) and item.startswith("#") for item in every):
            return ShaderDataType.LIST_RGB
        return ShaderDataType.OBJECT
    return ShaderDataType.OBJECT


@dataclass
class ShaderParameter:
    """A single shader parameter with validation rules."""

    name: str
    shader_type: ShaderType
    data_type: ShaderDataType
    value: Any
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    allowed_values: Optional[List[Any]] = None
    required: bool = True
    description: str = ""

    def validate(self) -> List[str]:
        """Validate this parameter. Returns list of error messages (empty if valid)."""
        errors: List[str] = []

        if self.required and self.value is None:
            return [f"Parameter '{self.name}' is required but got None"]
        if self.value is None:
            return errors

        if self.data_type in (ShaderDataType.FLOAT, ShaderDataType.INT):
            if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
                errors.append(
                    f"Parameter '{self.name}' must be numeric, got {type(self.value).__name__}"
                )
            else:
                if self.min_value is not None and self.value < self.min_value:
                    errors.append(
                        f"Parameter '{self.name}' value {self.value} below minimum {self.min_value}"
                    )
                if self.max_value is not None and self.value > self.max_value:
                    errors.append(
                        f"Parameter '{self.name}' value {self.value} above maximum {self.max_value}"
                    )

        elif self.data_type == ShaderDataType.BOOL:
            if not isinstance(self.value, bool):
                errors.append(
                    f"Parameter '{self.name}' must be bool, got {type(self.value).__name__}"
                )

        elif self.data_type == ShaderDataType.STRING:
            if not isinstance(self.value, str):
                errors.append(
                    f"Parameter '{self.name}' must be a string, got {type(self.value).__name__}"
                )

        elif self.data_type in (ShaderDataType.RGB, ShaderDataType.RGBA):
            if not isinstance(self.value, (list, tuple, str)):
                errors.append(
                    f"Parameter '{self.name}' must be a color tuple or hex string, "
                    f"got {type(self.value).__name__}"
                )
            elif isinstance(self.value, (list, tuple)):
                expected_len = 3 if self.data_type == ShaderDataType.RGB else 4
                if len(self.value) != expected_len:
                    errors.append(
                        f"Parameter '{self.name}' must have {expected_len} components, "
                        f"got {len(self.value)}"
                    )
                for i, component in enumerate(self.value):
                    if not isinstance(component, (int, float)) or isinstance(
                        component, bool
                    ):
                        errors.append(f"Parameter '{self.name}[{i}]' must be numeric")
                    elif not 0 <= component <= 1:
                        errors.append(
                            f"Parameter '{self.name}[{i}]' value {component} outside 0..1 "
                            "(xOpat converts colors as normalized floats)"
                        )

        elif self.data_type == ShaderDataType.LIST_RGB:
            if not isinstance(self.value, (list, tuple)):
                errors.append(
                    f"Parameter '{self.name}' must be a list of colors, "
                    f"got {type(self.value).__name__}"
                )
            elif len(self.value) > MAX_PALETTE_COLORS:
                errors.append(
                    f"Parameter '{self.name}' has {len(self.value)} colors; "
                    f"custom_colormap supports at most {MAX_PALETTE_COLORS}"
                )
            else:
                for j, color in enumerate(self.value):
                    if isinstance(color, str):
                        continue
                    if not isinstance(color, (list, tuple)) or len(color) < 3:
                        errors.append(
                            f"Parameter '{self.name}[{j}]' must be a hex string or an "
                            f"RGB tuple of length >= 3, got {color}"
                        )

        elif self.data_type == ShaderDataType.OBJECT:
            if not isinstance(self.value, dict):
                errors.append(
                    f"Parameter '{self.name}' must be a UI-control descriptor, "
                    f"got {type(self.value).__name__}"
                )

        if self.allowed_values is not None and self.value not in self.allowed_values:
            errors.append(
                f"Parameter '{self.name}' value {self.value} not in allowed values: "
                f"{self.allowed_values}"
            )

        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a dictionary suitable for xOpat configuration."""
        return {"name": self.name, "type": self.data_type.value, "value": self.value}


@dataclass
class ShaderConfig:
    """One xOpat v3 shader layer, validated before it reaches a session.

    Responsibilities:
    1. Reject params the layer does not declare (v3 would drop them silently).
    2. Enforce cross-control couplings (palette size vs. threshold breaks).
    3. Serialise to the v3 layer shape consumed by `report_fast.xopat`.
    """

    shader_type: ShaderType
    name: str = "Unnamed Layer"
    params: List[ShaderParameter] = field(default_factory=list)
    data_source: Optional[str] = None  # Path/URL to the overlay's tile data
    opacity: float = 1.0
    visible: bool = True
    fixed: bool = False
    #: `False` when the caller added params we do not model, which
    #: :meth:`with_params` sets. See the note in :meth:`validate`.
    strict_params: bool = True

    def __post_init__(self):
        self.shader_type = _coerce_shader_type(self.shader_type)
        self._validated = False

    def add_param(self, param: ShaderParameter) -> "ShaderConfig":
        """Add a parameter (fluent API)."""
        self.params.append(param)
        self._validated = False
        return self

    def with_params(self, params: Mapping[str, Any]) -> "ShaderConfig":
        """Add parameters this library does not model.

        The value is either the parameter's value or the full descriptor form
        (`{"default": …, "interactive": …}`) as a viewer export writes it. This
        is the door for a v3 field we have not got to yet, so it opts out of the
        declared-parameter check -- see `strict_params`.

        `data_type` is inferred from the value when the descriptor omits it. It
        is a required field of a *declared* parameter because a declared one has
        a validator to run; for an unmodelled field there is nothing to validate
        against, and demanding the type would mean the door only opens for
        someone who already knows the v3 schema -- which is the thing they are
        here to avoid.
        """
        for name, spec in params.items():
            # A mapping *is* the descriptor only when it says so with a `value`
            # key; otherwise it is the value itself (a colormap descriptor, a
            # nested control), and guessing otherwise would quietly rewrite the
            # one payload shape this door exists to pass through untouched.
            fields = dict(spec) if isinstance(spec, Mapping) and "value" in spec else {"value": spec}
            fields.setdefault("data_type", _inferred_type(fields["value"]))
            self.add_param(
                ShaderParameter(name=name, shader_type=self.shader_type, **fields)
            )
        self.strict_params = False
        return self

    def param_values(self) -> Dict[str, Any]:
        """Params as xOpat reads them, with the layer's opacity folded in."""
        values = {param.name: param.value for param in self.params}
        values.setdefault("opacity", self.opacity)
        return values

    def validate(self) -> "ShaderConfig":
        """Validate the layer. Raises ValueError listing every problem found."""
        errors: List[str] = [
            error for param in self.params for error in param.validate()
        ]

        if not 0.0 <= float(self.opacity) <= 1.0:
            errors.append(f"Opacity {self.opacity} outside 0..1")

        unknown = sorted(
            key for key in self.param_values() if not accepts_param(self.shader_type, key)
        )
        if unknown and self.strict_params:
            family = SHADER_PARAM_FAMILIES.get(self.shader_type)
            plus_family = (
                f" plus the generated family {family.pattern!r}" if family else ""
            )
            errors.append(
                f"Params not declared by '{self.shader_type.value}': {unknown}. "
                f"Declared: {sorted(allowed_params(self.shader_type))}{plus_family}. "
                "xOpat v3 drops undeclared params without warning, so the layer would "
                "silently use its defaults."
            )
        elif unknown:
            warnings.warn(
                f"Layer {self.name!r} carries params '{self.shader_type.value}' does not "
                f"declare: {unknown}. Emitted because they were added deliberately "
                "(`params:` in a manifest row / ShaderConfig.with_params); v3 drops "
                "undeclared params silently, so if the layer ignores one, that is why.",
                stacklevel=3,
            )

        errors.extend(self._validate_palette_coupling())

        if errors:
            raise ValueError(
                f"ShaderConfig validation failed for '{self.name}':\n"
                + "\n".join(f"  - {error}" for error in errors)
            )

        self._validated = True
        return self

    def _validate_palette_coupling(self) -> List[str]:
        """`colormap_class_count`: palette length must be `threshold.breaks.length + 1`."""
        if self.shader_type not in PALETTE_COUPLED_TYPES:
            return []
        values = self.param_values()
        color, threshold = values.get("color"), values.get("threshold")
        if not isinstance(color, dict) or not isinstance(threshold, dict):
            return []
        breaks = threshold.get("breaks", threshold.get("default"))
        if not isinstance(breaks, list):
            return []
        palette = color.get("default")
        steps = len(palette) if isinstance(palette, list) else color.get("steps")
        if not isinstance(steps, int):
            return []
        if steps != len(breaks) + 1:
            return [
                f"'{self.shader_type.value}': color palette has {steps} classes but "
                f"threshold.breaks has {len(breaks)} (needs {steps - 1}). The viewer's "
                "colormap_class_count coupling rejects this combination."
            ]
        return []

    def to_xopat_layer(
        self, data_references: Optional[Sequence[int]] = None
    ) -> Dict[str, Any]:
        """Serialise to the v3 shader-layer shape.

        Args:
            data_references: Indices into the session's `data` array this layer
                samples. v3 reads `dataReferences` (plural); the v2 singular
                `dataReference` key is ignored by the renderer.
        """
        if not self._validated:
            self.validate()

        layer: Dict[str, Any] = {
            "type": self.shader_type.value,
            "name": self.name,
            "visible": 1 if self.visible else 0,
            "fixed": bool(self.fixed),
            "params": self.param_values(),
        }
        if data_references is not None:
            layer["dataReferences"] = [int(index) for index in data_references]
        return layer

    def to_json(self) -> str:
        """Serialize to JSON string for embedding in HTML."""
        return json.dumps(self.to_xopat_layer())


def heatmap_shader(
    name: str,
    data_source: str,
    color: Union[str, Sequence[float]] = "#fff700",
    threshold: float = 1,
    inverse: bool = False,
    opacity: float = 0.7,
    **extra_params: Any,
) -> ShaderConfig:
    """Build a validated `heatmap` layer (scalar value drives alpha).

    Args:
        name: Layer display name.
        data_source: Overlay data (single-channel scalar image).
        color: Tint, as `#rrggbb` or an `(r, g, b)` tuple in 0..1.
        threshold: 1..100; values below it are not drawn.
        inverse: Draw values below the threshold at full alpha instead.
        opacity: Layer alpha, 0..1.
        **extra_params: Further declared params, e.g. `use_channel0="g"`.
    """
    config = ShaderConfig(
        shader_type=ShaderType.HEATMAP,
        name=name,
        data_source=data_source,
        opacity=opacity,
    )
    config.add_param(
        ShaderParameter(
            name="color",
            shader_type=ShaderType.HEATMAP,
            data_type=ShaderDataType.RGB,
            value=color,
            description="Tint applied to visible values",
        )
    )
    config.add_param(
        ShaderParameter(
            name="threshold",
            shader_type=ShaderType.HEATMAP,
            data_type=ShaderDataType.FLOAT,
            value=float(threshold),
            min_value=1,
            max_value=100,
            description="Values below this are not drawn",
        )
    )
    config.add_param(
        ShaderParameter(
            name="inverse",
            shader_type=ShaderType.HEATMAP,
            data_type=ShaderDataType.BOOL,
            value=bool(inverse),
            description="Invert the threshold gate",
        )
    )
    _add_extra_params(config, extra_params)
    return config.validate()


def classify_shader(
    name: str,
    classes: int,
    colors: List[Union[str, Sequence[float]]],
    data_source: str,
    opacity: float = 1.0,
    breaks: Optional[List[float]] = None,
    mask: Optional[List[int]] = None,
    **extra_params: Any,
) -> ShaderConfig:
    """Build a class map from class-encoded overlay data.

    xOpat v3 has no `classify` layer: a class map is a `colormap` layer whose
    palette is the class colours (`custom_colormap`) and whose
    `threshold.breaks` separate the class bins. The viewer's
    `colormap_class_count` coupling requires the palette length to be
    `breaks + 1`, which this factory keeps consistent for you.

    Args:
        name: Layer display name.
        classes: Number of classes (palette length).
        colors: One colour per class; hex strings or RGB tuples in 0..1.
        data_source: Class-encoded overlay data.
        opacity: Layer alpha, 0..1.
        breaks: `classes - 1` bin boundaries, in whatever units the overlay
            samples in. Defaults to evenly spaced cuts over the normalized 0..1
            range, matching flex-renderer's own 3-class example ([0.33, 0.66]).
            Pass this when the overlay encodes classes differently.
        mask: One ``0``/``1`` per class, selecting which classes are drawn.
            Defaults to all of them; ``[0, 1, 1]`` leaves class 0 — usually the
            background — unpainted.
        **extra_params: Further declared `colormap` params, e.g. `connect=False`.
    """
    if len(colors) != classes:
        raise ValueError(
            f"classify_shader({name!r}): got {len(colors)} colors for {classes} classes"
        )
    if classes < 2:
        raise ValueError(
            f"classify_shader({name!r}): need at least 2 classes to colour a map"
        )
    if breaks is not None and len(breaks) != classes - 1:
        raise ValueError(
            f"classify_shader({name!r}): {classes} classes need {classes - 1} breaks, "
            f"got {len(breaks)}"
        )
    if mask is not None and len(mask) != classes:
        raise ValueError(
            f"classify_shader({name!r}): {classes} classes need {classes} mask "
            f"flags, got {len(mask)}"
        )

    palette = [_to_hex(color) for color in colors]
    cuts = (
        list(breaks)
        if breaks is not None
        else [round(index / classes, 4) for index in range(1, classes)]
    )
    config = ShaderConfig(
        shader_type=ShaderType.COLORMAP,
        name=name,
        data_source=data_source,
        opacity=opacity,
    )
    config.add_param(
        ShaderParameter(
            name="color",
            shader_type=ShaderType.COLORMAP,
            data_type=ShaderDataType.OBJECT,
            value={"type": "custom_colormap", "default": palette, "steps": classes},
            description="One palette entry per class",
        )
    )
    config.add_param(
        ShaderParameter(
            name="threshold",
            shader_type=ShaderType.COLORMAP,
            data_type=ShaderDataType.OBJECT,
            value={
                "type": "advanced_slider",
                "breaks": cuts,
                "mask": list(mask) if mask is not None else [1] * classes,
            },
            description="Class bin boundaries and which classes are drawn",
        )
    )
    config.add_param(
        ShaderParameter(
            name="connect",
            shader_type=ShaderType.COLORMAP,
            data_type=ShaderDataType.BOOL,
            value=True,
            description="Keep palette steps aligned with break positions",
        )
    )
    _add_extra_params(config, extra_params)
    return config.validate()


def _add_extra_params(config: ShaderConfig, extra_params: Dict[str, Any]) -> None:
    for key, value in extra_params.items():
        if isinstance(value, bool):
            data_type = ShaderDataType.BOOL
        elif isinstance(value, int):
            data_type = ShaderDataType.INT
        elif isinstance(value, float):
            data_type = ShaderDataType.FLOAT
        elif isinstance(value, dict):
            data_type = ShaderDataType.OBJECT
        elif isinstance(value, str):
            data_type = ShaderDataType.STRING
        else:
            data_type = ShaderDataType.OBJECT
        config.add_param(
            ShaderParameter(
                name=key,
                shader_type=config.shader_type,
                data_type=data_type,
                value=value,
            )
        )


__all__ = [
    "ShaderType",
    "ShaderDataType",
    "ShaderParameter",
    "ShaderConfig",
    "SHADER_PARAMS",
    "COMMON_PARAMS",
    "PALETTE_COUPLED_TYPES",
    "RETIRED_SHADER_TYPES",
    "MAX_PALETTE_COLORS",
    "UnknownShaderTypeError",
    "allowed_params",
    "heatmap_shader",
    "classify_shader",
]
