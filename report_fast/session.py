"""The xOpat v3 session as a value object -- the base of the whole tool.

A `XopatSession` *builds* a session from a slide path (`from_slide`) or
*accepts* one someone else authored (`from_config`, `from_url`), and hands back
the config dict or the viewer link. It imports no web framework and touches no
I/O beyond reading a preset file, so a user script can build its own HTML from
`.url()` without the rest of this package.

Two invariants carry the design:

  * **`data[]` is append-only.** Every reference into it is a positional index
    (`background[].dataReference`, `visualizations[].shaders[*].dataReferences`),
    so the only safe mutation is an append through `add_data`, which returns the
    index to reference. Joining two sessions goes through `merge`, which
    renumbers; lists are never deep-merged.
  * **An imported config survives verbatim.** Fields this module does not model
    are kept on their entries (and unknown top-level keys on `.extra`), because
    accepting a user's own session exists precisely to reach features the tool
    does not have. `strict=True` opts into rejecting them instead of warning.
"""

from __future__ import annotations

import copy
import json
import urllib.parse
import warnings
from pathlib import Path, PurePosixPath
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from .config import SessionPreset, deep_merge, resolve_preset
from .xopat import (
    DEFAULT_THUMBNAIL_SIZE,
    PARAM_KEYS,
    XopatEndpoint,
    XopatError,
    background_protocol,
    data_entry,
    mount_path,
    normalise_layer,
    resolve_endpoint,
    session_fragment,
    shader_layer,
    thumbnail_url,
    viewer_url,
)

#: `params` that record *where the author was looking* rather than what a report
#: should show. Dropped on import by default so a pasted config cannot dictate
#: where a new report opens.
STATE_KEYS = frozenset(
    {"viewport", "activeBackgroundIndex", "activeVisualizationIndex"}
)

#: Fields the viewer bolts onto a session at runtime (`__age` and friends in
#: `src/parse-input.js`). Carrying them over is stale state, not configuration.
RUNTIME_KEYS = frozenset(
    {"__age", "__envKey", "__fromLocalStorage", "__fromSessionStorage"}
)

#: The known top-level session keys; the rest are kept on `.extra`.
SESSION_KEYS = ("params", "data", "background", "visualizations", "plugins")

#: Extensions treated as slides when scanning a folder. The image server reads
#: all of these; `*.czi` is a background, not a mask, but it is scanned as one.
SLIDE_PATTERNS = (
    "*.tif",
    "*.tiff",
    "*.svs",
    "*.ndpi",
    "*.jpeg",
    "*.jpg",
    "*.png",
    "*.czi",
)

Slide = Union[str, Path]


def _data_id(entry: Any) -> Any:
    """The DataID carried by a `data[]` entry, bare or `DataOverride`."""
    if isinstance(entry, Mapping):
        return entry.get("dataID")
    return entry


def _as_id_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _shift_references(obj: Any, offset: int, viz_offset: int = 0) -> Any:
    """Renumber every index in a copied session fragment.

    Walks the structure and shifts `dataReference` / `dataReferences` by
    `offset` (and `visualizationIndex` by `viz_offset`), so a grafted session's
    references keep pointing at its own entries.
    """
    if isinstance(obj, list):
        return [_shift_references(item, offset, viz_offset) for item in obj]
    if not isinstance(obj, dict):
        return obj
    shifted = {}
    for key, value in obj.items():
        if key == "dataReferences":
            shifted[key] = [
                ref + offset if isinstance(ref, int) else ref
                for ref in _as_id_list(value)
            ]
        elif key == "dataReference":
            shifted[key] = value + offset if isinstance(value, int) else value
        elif key == "visualizationIndex":
            shifted[key] = value + viz_offset if isinstance(value, int) else value
        else:
            shifted[key] = _shift_references(value, offset, viz_offset)
    return shifted


class XopatSession:
    """One xOpat v3 session: what the viewer opens, as a Python object.

    Construct directly to take a config verbatim, or through `from_slide` /
    `from_config` / `from_url`. The five session lists are plain attributes --
    `session.background.append({...})` is a supported way to reach a field this
    class does not model.

    Attributes:
        params: Viewer settings (allowlisted against `PARAM_KEYS`).
        data: The data pool every other list indexes into. Append-only.
        background: One entry per image group / viewport.
        visualizations: Overlay compositions, referenced from `background`.
        plugins: Plugin id -> config.
        extra: Top-level keys this class does not model, kept for round-tripping.
        endpoint: Deployment to link against; `None` uses the environment default.
    """

    def __init__(
        self,
        *,
        params: Optional[Mapping[str, Any]] = None,
        data: Optional[Iterable[Any]] = None,
        background: Optional[Iterable[Mapping[str, Any]]] = None,
        visualizations: Optional[Iterable[Mapping[str, Any]]] = None,
        plugins: Optional[Mapping[str, Any]] = None,
        extra: Optional[Mapping[str, Any]] = None,
        endpoint: Optional[XopatEndpoint] = None,
    ):
        self.params: Dict[str, Any] = dict(params or {})
        self.data: List[Any] = list(data or [])
        self.background: List[Dict[str, Any]] = [
            dict(entry) for entry in background or ()
        ]
        self.visualizations: List[Dict[str, Any]] = [
            dict(entry) for entry in visualizations or ()
        ]
        self.plugins: Dict[str, Any] = dict(plugins or {})
        self.extra: Dict[str, Any] = dict(extra or {})
        self.endpoint = endpoint

    # ------------------------------------------------------------------ value

    def to_config(self) -> Dict[str, Any]:
        """The session dict, ready for `json.dumps` or a URL fragment."""
        config = {
            "params": copy.deepcopy(self.params),
            "data": copy.deepcopy(self.data),
            "background": copy.deepcopy(self.background),
            "visualizations": copy.deepcopy(self.visualizations),
            "plugins": copy.deepcopy(self.plugins),
        }
        config.update(copy.deepcopy(self.extra))
        return config

    def to_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("separators", (",", ":"))
        kwargs.setdefault("ensure_ascii", False)
        return json.dumps(self.to_config(), **kwargs)

    def copy(self) -> "XopatSession":
        """A deep copy, so editing the copy cannot reach the original.

        One level is not enough: `add_layer` writes into a visualization's
        `shaders` map, and a pasted session is the caller's property, not ours.
        """
        return XopatSession(
            params=copy.deepcopy(self.params),
            data=copy.deepcopy(self.data),
            background=copy.deepcopy(self.background),
            visualizations=copy.deepcopy(self.visualizations),
            plugins=copy.deepcopy(self.plugins),
            extra=copy.deepcopy(self.extra),
            endpoint=self.endpoint,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, XopatSession):
            return NotImplemented
        return self.to_config() == other.to_config()

    def __repr__(self) -> str:
        return (
            f"XopatSession(backgrounds={len(self.background)}, data={len(self.data)}, "
            f"visualizations={len(self.visualizations)})"
        )

    def endpoint_for(self, endpoint: Optional[XopatEndpoint] = None) -> XopatEndpoint:
        """Explicit endpoint, else the session's, else the environment default."""
        return resolve_endpoint(endpoint or self.endpoint)

    # ------------------------------------------------------------- accessors

    @property
    def data_ids(self) -> List[Any]:
        """The DataID of every `data[]` entry, in order."""
        return [_data_id(entry) for entry in self.data]

    @property
    def names(self) -> List[str]:
        """Background labels, falling back to the DataID's stem."""
        labels = []
        for index, entry in enumerate(self.background):
            name = entry.get("name")
            if name:
                labels.append(str(name))
                continue
            data_id = (
                _data_id(self.data[entry["dataReference"]])
                if index < len(self.data)
                else ""
            )
            labels.append(PurePosixPath(str(data_id)).stem or f"background {index}")
        return labels

    def background_for(self, slot: int = 0) -> Dict[str, Any]:
        """The background a viewer slot opens (`params.activeBackgroundIndex`)."""
        active = self.params.get("activeBackgroundIndex", 0)
        index = _as_id_list(active)[0] if _as_id_list(active) else 0
        if not isinstance(index, int) or not 0 <= index < len(self.background):
            index = 0
        return self.background[index]

    def bind_name(self, name: str) -> "XopatSession":
        """Label the first background, and the visualization it opens.

        This is the label a card prints and the viewer's background menu lists.
        `params.sessionName` is deliberately left alone: it is the viewer's
        persistence namespace, not a title.
        """
        if not self.background:
            return self
        self.background[0]["name"] = name
        visualization = self.background[0].get("visualizationIndex")
        if (
            isinstance(visualization, int)
            and not isinstance(visualization, bool)
            and 0 <= visualization < len(self.visualizations)
        ):
            self.visualizations[visualization]["name"] = name
        return self

    # -------------------------------------------------------------- linking

    def url(self, endpoint: Optional[XopatEndpoint] = None) -> str:
        """Viewer URL with the session in the fragment."""
        return viewer_url(self.to_config(), self.endpoint_for(endpoint))

    def fragment(self) -> str:
        """The percent-encoded fragment alone."""
        return session_fragment(self.to_config())

    def thumbnail(
        self,
        endpoint: Optional[XopatEndpoint] = None,
        size: int = DEFAULT_THUMBNAIL_SIZE,
        background: Optional[int] = None,
    ) -> str:
        """WSI-Service thumbnail for a background (the first by default)."""
        if not self.data or not self.background:
            return ""
        index = background if background is not None else 0
        reference = self.background[index].get("dataReference", 0)
        data_id = (
            _data_id(self.data[reference]) if isinstance(reference, int) else reference
        )
        return thumbnail_url(str(data_id), self.endpoint_for(endpoint), size)

    def has_thumbnail(self, background: int = 0, endpoint=None) -> bool:
        """Whether a preview of `background` can be fetched at all.

        Only the WSI-Service tile source answers `/v3/slides/thumbnail`. A
        session pasted from elsewhere can name another protocol on its data
        entry (`iipimage` is the common one), and the WSI server answers those
        with a 404 -- which a report would otherwise print as a broken image.
        """
        target = self.endpoint_for(endpoint)
        if not target.image_protocol or not self.background:
            return True
        entry = self.background_for(background)
        reference = entry.get("dataReference")
        data = self.data[reference] if isinstance(reference, int) else {}
        for source in (data, entry):
            protocol = source.get("protocol") if isinstance(source, Mapping) else None
            if protocol:
                return str(protocol) == target.image_protocol
        return True  # nothing named: the deployment default serves it

    # ------------------------------------------------------------- mutation

    def add_data(
        self,
        data_id: Slide,
        *,
        protocol: Optional[str] = None,
        options: Optional[Mapping[str, Any]] = None,
        microns: Optional[float] = None,
        smoothing: Optional[bool] = None,
        lossless: bool = False,
        endpoint: Optional[XopatEndpoint] = None,
    ) -> int:
        """Append one `data[]` entry and return its index.

        The only safe way to grow the pool: the index this returns is what
        `background[].dataReference` and `dataReferences` have to carry.

        Args:
            data_id: Path or DataID; mounted against the endpoint's root.
            protocol: Registered slide-protocol name for this entry.
            options: `DataOverride.options` (`plugin`, `format`, `quality`,
                `channels`) forwarded to the tile source.
            microns: Pixel size in micrometers, for the scale bar.
            smoothing: `False` samples tiles with `gl.NEAREST` -- what an
                integer label map wants, so its class values stay exact.
            lossless: Ask for lossless tiles (`options.format = "png"`).
        """
        target = self.endpoint_for(endpoint)
        entry = data_entry(
            mount_path(data_id, target.mount_root),
            lossless=lossless,
            protocol=protocol,
            options=options,
            microns=microns,
            smoothing=smoothing,
        )
        self.data.append(entry)
        return len(self.data) - 1

    def add_visualization(
        self,
        name: Optional[str] = None,
        shaders: Optional[Mapping[str, Mapping[str, Any]]] = None,
        order: Optional[Sequence[str]] = None,
    ) -> int:
        """Append a `visualizations[]` entry and return its index."""
        visualization: Dict[str, Any] = {"name": name, "shaders": dict(shaders or {})}
        if order:
            visualization["order"] = list(order)
        self.visualizations.append(visualization)
        return len(self.visualizations) - 1

    def add_background(
        self,
        slide: Slide,
        *,
        name: Optional[str] = None,
        id: Optional[str] = None,
        visualization_index: Optional[int] = 0,
        protocol: Optional[str] = None,
        options: Optional[Mapping[str, Any]] = None,
        microns: Optional[float] = None,
        smoothing: Optional[bool] = None,
        background_shaders: Optional[Sequence[Mapping[str, Any]]] = None,
        endpoint: Optional[XopatEndpoint] = None,
    ) -> int:
        """Append a slide to `data[]` and mount it as a background.

        Returns:
            The index of the appended **`data[]`** entry -- what an overlay
            shader needs. The background itself is `session.background[-1]`.
        """
        target = self.endpoint_for(endpoint)
        index = self.add_data(
            slide,
            protocol=protocol
            if protocol is not None
            else background_protocol(_as_str(slide), target),
            options=options,
            microns=microns,
            smoothing=smoothing,
            endpoint=target,
        )
        entry: Dict[str, Any] = {"dataReference": index}
        if id:
            entry["id"] = id
        if name:
            entry["name"] = name
        if visualization_index is not None:
            entry["visualizationIndex"] = visualization_index
        if background_shaders:
            entry["shaders"] = [dict(shader) for shader in background_shaders]
        self.background.append(entry)
        return index

    def add_layer(
        self,
        layer: Any,
        *,
        visualization: int = 0,
        shader_id: Optional[str] = None,
        lossless: bool = True,
        endpoint: Optional[XopatEndpoint] = None,
    ) -> Optional[int]:
        """Append an overlay layer to a visualization.

        Args:
            layer: `ShaderConfig`, or a mapping with `path` / `type` / `name` /
                `params`. A mapping that already carries `dataReferences` and no
                `path` is inserted verbatim -- the form an imported session uses.
            visualization: Index into `visualizations`.
            shader_id: Key under that visualization's `shaders` map; a template's
                `order` list refers to it by name.
            lossless: Lossless tiles for the layer's data, so class-map colours
                survive tiling.

        Returns:
            The appended `data[]` index, or `None` when the layer arrived with
            its own `dataReferences` and added nothing to the pool.
        """
        if visualization >= len(self.visualizations):
            raise XopatError(
                f"No visualizations[{visualization}] to attach a layer to; "
                "call add_visualization() first."
            )
        if (
            isinstance(layer, Mapping)
            and "dataReferences" in layer
            and "path" not in layer
        ):
            key = (
                shader_id
                or f"layer_shader_{len(self.visualizations[visualization]['shaders'])}"
            )
            self.visualizations[visualization]["shaders"][key] = dict(layer)
            return None

        spec = normalise_layer(layer)
        if not spec.get("path"):
            raise XopatError(
                f"Shader layer {spec.get('name')!r} needs a path to bind to data."
            )
        index = self.add_data(
            spec["path"],
            options=spec.get("options"),
            smoothing=spec.get("smoothing"),
            lossless=lossless,
            endpoint=endpoint,
        )
        key = (
            shader_id
            or f"layer_shader_{len(self.visualizations[visualization]['shaders'])}"
        )
        self.visualizations[visualization]["shaders"][key] = shader_layer(spec, index)
        return index

    def merge(self, other: "XopatSession") -> "XopatSession":
        """Return a new session holding both, with `other`'s indices renumbered.

        Explicit, index-shifting concatenation -- never a deep merge of the
        positional lists, which would repoint every overlay at the wrong slide.
        Conflicting `params` keep this session's value; conflicting `plugins`
        keep this session's config.
        """
        if not isinstance(other, XopatSession):
            raise XopatError(
                f"Can only merge a XopatSession, got {type(other).__name__}"
            )

        data_offset = len(self.data)
        viz_offset = len(self.visualizations)
        merged = self.copy()
        merged.data.extend(copy.deepcopy(other.data))
        merged.visualizations.extend(
            _shift_references(copy.deepcopy(entry), data_offset)
            for entry in other.visualizations
        )
        merged.background.extend(
            _shift_references(copy.deepcopy(entry), data_offset, viz_offset)
            for entry in other.background
        )
        for key, value in other.params.items():
            if key in merged.params and merged.params[key] != value:
                warnings.warn(
                    f"merge() kept params.{key}={merged.params[key]!r} over {value!r}.",
                    stacklevel=2,
                )
                continue
            merged.params[key] = copy.deepcopy(value)
        for key, value in other.plugins.items():
            merged.plugins.setdefault(key, copy.deepcopy(value))
        merged.extra.update(copy.deepcopy(other.extra))
        return merged

    @classmethod
    def merge_all(cls, sessions: Iterable["XopatSession"]) -> "XopatSession":
        """Merge any number of sessions left to right."""
        sessions = list(sessions)
        if not sessions:
            raise XopatError("merge_all() needs at least one session.")
        merged = sessions[0].copy()
        for session in sessions[1:]:
            merged = merged.merge(session)
        return merged

    # ---------------------------------------------------------- constructors

    @classmethod
    def from_slide(
        cls,
        slide: Slide,
        layers: Sequence[Any] = (),
        *,
        name: Optional[str] = None,
        params: Optional[Mapping[str, Any]] = None,
        plugins: Optional[Mapping[str, Any]] = None,
        endpoint: Optional[XopatEndpoint] = None,
        visualization_name: Optional[str] = None,
        lossless: Optional[bool] = None,
        protocol: Optional[str] = None,
        options: Optional[Mapping[str, Any]] = None,
        preset: Union[None, SessionPreset, Mapping[str, Any], str, Path] = None,
    ) -> "XopatSession":
        """Build the common session: one background plus its overlay layers.

        Args:
            slide: Background slide, absolute path or DataID. The report host
                never needs to read it -- the image server resolves it.
            layers: Overlay layers (`ShaderConfig` or mappings). Unset falls
                back to the preset's default layers.
            name: Background label shown in the viewer. Defaults to the file stem.
            params: Session `params`, merged over the preset's and checked
                against the viewer's allowlist.
            endpoint: Deployment coordinates; unset uses the preset's, else the
                environment default.
            lossless: Lossless overlay tiles; unset takes the preset's.
            protocol: Slide-protocol name for the background. Unset leaves the
                deployment's `default_background_protocol`, and a preset's value
                wins over the endpoint's.
            preset: Defaults to merge under everything, as a `SessionPreset`, a
                mapping, a file path, or `None` for `$XOPAT_SESSION_CONFIG` /
                the builtin.

        Returns:
            A session that renders as one viewer tab per call.
        """
        configuration = resolve_preset(preset)
        session = cls(
            params=deep_merge(configuration.params, params or {}),
            plugins=deep_merge(configuration.plugins, plugins or {}),
            endpoint=endpoint or configuration.endpoint,
        )
        target = session.endpoint_for()
        background_id = mount_path(slide, target.mount_root)
        session.add_visualization(
            visualization_name or name or PurePosixPath(background_id).stem
        )
        session.add_background(
            slide,
            name=name,
            visualization_index=0,
            protocol=protocol
            if protocol is not None
            else (configuration.protocol or target.image_protocol),
            options=options if options is not None else configuration.options,
            endpoint=target,
        )
        for layer in layers or configuration.layers:
            session.add_layer(
                layer,
                visualization=0,
                lossless=configuration.lossless if lossless is None else lossless,
                endpoint=target,
            )
        session.validate(strict=True)
        return session

    @classmethod
    def from_config(
        cls,
        config: Union[str, Mapping[str, Any]],
        *,
        endpoint: Optional[XopatEndpoint] = None,
        strict: bool = False,
        drop_state: bool = True,
    ) -> "XopatSession":
        """Take a session someone else authored, as a dict or JSON text.

        Unmodelled fields are kept as they arrived: the point of the paste path
        is that a user is not limited to what this class knows about.

        Args:
            config: Session dict, session JSON, or a viewer URL (dispatched to
                `from_url`).
            endpoint: Deployment to link against. An imported session keeps its
                own DataIDs and protocol names as authored -- only the base URL
                changes.
            strict: Raise on params/keys the viewer would not read instead of
                warning and keeping them.
            drop_state: Strip `params.viewport` / `activeBackgroundIndex`, which
                record where the author had navigated rather than what to show.
        """
        if isinstance(config, str):
            text = config.strip()
            if text[:5].lower() in ("http:", "https") or text.startswith("//"):
                return cls.from_url(
                    text, endpoint=endpoint, strict=strict, drop_state=drop_state
                )
            config = json.loads(text)
        if not isinstance(config, Mapping):
            raise XopatError(
                f"A session is a mapping or JSON object, got {type(config).__name__}."
            )

        body = copy.deepcopy(dict(config))
        for key in [key for key in body if key in RUNTIME_KEYS]:
            body.pop(key)

        unknown_top = sorted(set(body) - set(SESSION_KEYS))
        if unknown_top:
            if strict:
                raise XopatError(
                    f"Unknown session keys {unknown_top}. They are kept as-is otherwise -- "
                    "the viewer ignores what it does not know."
                )
            warnings.warn(
                f"Session carries keys the viewer does not read: {unknown_top}. Kept verbatim.",
                stacklevel=2,
            )

        params = dict(body.pop("params", None) or {})
        if drop_state:
            for key in sorted(STATE_KEYS.intersection(params)):
                params.pop(key)

        session = cls(
            params=params,
            data=body.pop("data", None) or [],
            background=body.pop("background", None) or [],
            visualizations=body.pop("visualizations", None) or [],
            plugins=body.pop("plugins", None) or {},
            extra=body,
            endpoint=endpoint,
        )
        session.validate(strict=strict)
        return session

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        endpoint: Optional[XopatEndpoint] = None,
        strict: bool = False,
        drop_state: bool = True,
    ) -> "XopatSession":
        """Recover the session a viewer link carries.

        Reads the fragment v3 parses (`src/parse-input.js`), then the legacy
        `?visualization=` query form. With no explicit `endpoint`, the link's own
        origin becomes the base URL, so a link from another deployment round-trips
        against that deployment.
        """
        parts = urllib.parse.urlsplit(url)
        payload = urllib.parse.unquote(parts.fragment) if parts.fragment else ""
        if not payload:
            payload = (urllib.parse.parse_qs(parts.query).get("visualization") or [""])[
                0
            ]
        if not payload:
            raise XopatError(
                f"No session found in {url!r}. v3 carries it in the fragment: "
                "<viewer root>#<urlencoded json>."
            )
        if endpoint is None and parts.scheme and parts.netloc:
            endpoint = XopatEndpoint(
                base_url=f"{parts.scheme}://{parts.netloc}{parts.path}"
            )
        return cls.from_config(
            payload, endpoint=endpoint, strict=strict, drop_state=drop_state
        )

    @classmethod
    def from_file(
        cls,
        path: Union[str, Path],
        *,
        endpoint: Optional[XopatEndpoint] = None,
        strict: bool = False,
        drop_state: bool = True,
    ) -> "XopatSession":
        """Read a session saved to a `.json` file."""
        text = Path(path).expanduser().read_text(encoding="utf-8")
        return cls.from_config(
            text, endpoint=endpoint, strict=strict, drop_state=drop_state
        )

    # ------------------------------------------------------------ validation

    def validate(self, *, strict: bool = True) -> None:
        """Check what would fail in the browser, before a link leaves the tool.

        The viewer hard-fails on a `background[].dataReference` it cannot
        resolve and silently drops `params` keys outside its allowlist, so both
        are worth catching while there is still a Python traceback to attach
        them to.
        """
        problems = []
        unknown_params = sorted(set(self.params) - PARAM_KEYS)
        if unknown_params:
            problems.append(
                f"unknown params {unknown_params}: the viewer drops keys outside its "
                "`setup` allowlist without a word (see PARAM_KEYS in report_fast/xopat.py)"
            )
        for index, entry in enumerate(self.background):
            reference = entry.get("dataReference")
            if isinstance(reference, int):
                if not 0 <= reference < len(self.data):
                    problems.append(
                        f"background[{index}].dataReference={reference} is outside data[] "
                        f"(0..{len(self.data) - 1}) -- the viewer refuses to boot at all"
                    )
                continue
            if not reference:
                problems.append(
                    f"background[{index}] has no dataReference; one reference per background "
                    "is required"
                )
            visualization = entry.get("visualizationIndex")
            if isinstance(visualization, int) and not 0 <= visualization < len(
                self.visualizations
            ):
                problems.append(
                    f"background[{index}].visualizationIndex={visualization} is outside "
                    f"visualizations[] (0..{len(self.visualizations) - 1})"
                )
        for index, visualization in enumerate(self.visualizations):
            for shader_id, shader in (visualization.get("shaders") or {}).items():
                references = _as_id_list((shader or {}).get("dataReferences", []))
                dangling = [
                    ref
                    for ref in references
                    if isinstance(ref, int) and not 0 <= ref < len(self.data)
                ]
                if dangling:
                    problems.append(
                        f"visualizations[{index}].shaders[{shader_id!r}].dataReferences "
                        f"{dangling} point outside data[]"
                    )
        if problems:
            message = "; ".join(problems)
            if strict:
                raise XopatError(f"Session will not load in xOpat v3 -- {message}.")
            warnings.warn(
                f"Session may not load in xOpat v3 -- {message}.", stacklevel=3
            )


def _as_str(value: Slide) -> str:
    return str(value)


# ------------------------------------------------------------------- reuse


class SessionTemplate:
    """A session config with one or more data slots, for making many sessions.

    Slots are addressed by `data[]` index, which is what makes a *pasted* config
    reusable as-is: no placeholder syntax, no re-authoring. Binding swaps the
    DataID at that index and keeps everything else the entry carried -- its
    protocol, its options, its pixel size -- so a colleague's config becomes a
    form where only the slide changes.

        template = SessionTemplate.from_config(
            json.load(open("my_session.json")), slots={0: "slide", 1: "mask"}
        )
        sessions = [template.bind(slide=path, mask=f"{path}.prob.tif") for path in slides]
    """

    def __init__(
        self,
        session: XopatSession,
        slots: Optional[Mapping[int, str]] = None,
    ):
        if not isinstance(session, XopatSession):
            raise XopatError("SessionTemplate takes a XopatSession; use from_config().")
        self.session = session
        self.slots: Dict[int, str] = {}
        for index, name in (slots or {0: "slide"}).items():
            if not 0 <= index < len(session.data):
                raise XopatError(
                    f"Slot {name!r} names data[{index}], but the session has "
                    f"{len(session.data)} data entries."
                )
            self.slots[int(index)] = str(name)
        collisions = {
            name: [i for i, n in self.slots.items() if n == name]
            for name in self.slots.values()
        }
        duplicate = {name: idxs for name, idxs in collisions.items() if len(idxs) > 1}
        if duplicate:
            raise XopatError(f"Slot names must be unique; {duplicate} are repeated.")

    @property
    def slot_names(self) -> List[str]:
        return list(self.slots.values())

    @classmethod
    def from_config(
        cls,
        config: Union[str, Mapping[str, Any]],
        *,
        slots: Optional[Mapping[int, str]] = None,
        endpoint: Optional[XopatEndpoint] = None,
        strict: bool = False,
        drop_state: bool = True,
    ) -> "SessionTemplate":
        """Turn a user's session (or this tool's default) into a template."""
        return cls(
            XopatSession.from_config(
                config, endpoint=endpoint, strict=strict, drop_state=drop_state
            ),
            slots,
        )

    def bind(
        self,
        *,
        name: Optional[str] = None,
        params: Optional[Mapping[str, Any]] = None,
        partial: bool = False,
        **slots: Any,
    ) -> XopatSession:
        """Fill the slots and return a new session.

        Args:
            name: Background label; unset keeps the template's own.
            params: Extra `params`, merged over the template's.
            partial: Allow leaving slots unfilled (they keep the template's
                DataIDs) instead of requiring every one.
            **slots: One keyword per slot name, each a path or DataID.
        """
        unknown = sorted(set(slots) - set(self.slots.values()))
        if unknown:
            raise XopatError(
                f"Unknown slot(s) {unknown}. This template binds {self.slot_names}."
            )
        missing = sorted(set(self.slots.values()) - set(slots))
        if missing and not partial:
            raise XopatError(
                f"Slot(s) {missing} are not bound. Pass them, or partial=True to keep "
                "the template's own data there."
            )

        bound = self.session.copy()
        by_name = {name: index for index, name in self.slots.items()}
        for slot_name, value in slots.items():
            index = by_name[slot_name]
            target = bound.endpoint_for()
            data_id = mount_path(value, target.mount_root)
            original = bound.data[index]
            if isinstance(original, Mapping):
                # Keep the entry's protocol/options/microns; only the id changes.
                bound.data[index] = {**original, "dataID": data_id}
            else:
                bound.data[index] = data_id

        if name:
            bound.bind_name(name)
        if params:
            bound.params = deep_merge(bound.params, params)
        bound.validate(strict=True)
        return bound


def layers_from_files(
    slide: Slide,
    mask_sets: Mapping[str, Sequence[Slide]],
) -> List[Dict[str, Any]]:
    """Map `{label: mask path}` for one slide into overlay layers."""
    return [{"path": mask, "name": label} for label, mask in mask_sets.items()]


def sessions_from_paths(
    slides: Iterable[Slide],
    *,
    layers: Sequence[Any] = (),
    layers_for: Optional[Callable[[Slide], Sequence[Any]]] = None,
    masks: Optional[Sequence[Any]] = None,
    template: Optional[SessionTemplate] = None,
    template_slot: str = "slide",
    names: Optional[Callable[[Slide], str]] = None,
    params: Optional[Mapping[str, Any]] = None,
    endpoint: Optional[XopatEndpoint] = None,
    preset: Union[None, SessionPreset, Mapping[str, Any], str, Path] = None,
) -> List[XopatSession]:
    """One session per slide, from paths.

    Args:
        slides: Backgrounds, in report order.
        layers: Overlay layers for every slide.
        layers_for: Per-slide overlay layers, given the slide path. Wins over
            `layers`; this is where a `slide -> predictions` lookup lives.
        masks: Alias for `layers` that reads better with bare mask paths.
        template: Build each session by binding this template instead of the
            default single-slide construction.
        template_slot: Slot the slide path is bound to.
        names: Label for each slide; defaults to the file stem.
    """
    if template is not None and not isinstance(template, SessionTemplate):
        raise XopatError(
            "template must be a SessionTemplate (see SessionTemplate.from_config)."
        )

    shared = list(layers or ()) + list(masks or ())
    sessions = []
    for slide in slides:
        label = names(slide) if names else PurePosixPath(Path(slide).as_posix()).stem
        per_slide = list(shared)
        if layers_for is not None:
            per_slide.extend(layers_for(slide))
        if template is not None:
            session = template.bind(name=label, params=params, **{template_slot: slide})
        else:
            session = XopatSession.from_slide(
                slide,
                per_slide,
                name=label,
                params=params,
                endpoint=endpoint,
                preset=preset,
            )
        sessions.append(session)
    return sessions


def sessions_from_folder(
    directory: Union[str, Path],
    *,
    patterns: Sequence[str] = SLIDE_PATTERNS,
    recursive: bool = False,
    layers: Sequence[Any] = (),
    layers_for: Optional[Callable[[Slide], Sequence[Any]]] = None,
    masks: Optional[Sequence[Any]] = None,
    template: Optional[SessionTemplate] = None,
    names: Optional[Callable[[Slide], str]] = None,
    params: Optional[Mapping[str, Any]] = None,
    endpoint: Optional[XopatEndpoint] = None,
    preset: Union[None, SessionPreset, Mapping[str, Any], str, Path] = None,
    sort: Optional[Callable[[Path], Tuple]] = lambda path: path.name,
) -> List[XopatSession]:
    """One session per slide found in `directory`.

    Args:
        directory: Folder to scan. Must exist -- an empty list from a typo is a
            silently empty report.
        patterns: Glob patterns to accept, e.g. `"*.tif"` or a sequence.
        recursive: Descend into subfolders.
        layers/masks/layers_for/template/names/params/endpoint/preset:
            As `sessions_from_paths`.
        sort: Order key; `None` keeps the filesystem order.
    """
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise XopatError(f"Slide folder {root} does not exist.")

    pattern_list = (patterns,) if isinstance(patterns, str) else list(patterns)
    found = {
        path
        for pattern in pattern_list
        for path in (root.rglob(pattern) if recursive else root.glob(pattern))
        if path.is_file()
    }
    files = sorted(found, key=sort) if sort else list(found)
    if not files:
        warnings.warn(
            f"No slides matched {pattern_list} in {root}; the report will have no slides.",
            stacklevel=2,
        )
    return sessions_from_paths(
        files,
        layers=layers,
        layers_for=layers_for,
        masks=masks,
        template=template,
        names=names,
        params=params,
        endpoint=endpoint,
        preset=preset,
    )


def as_session(
    source: Union[XopatSession, SessionTemplate, Mapping[str, Any], str, Path],
    layers: Sequence[Any] = (),
    *,
    name: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    endpoint: Optional[XopatEndpoint] = None,
    preset: Union[None, SessionPreset, Mapping[str, Any], str, Path] = None,
    strict: bool = False,
) -> XopatSession:
    """Coerce anything session-shaped into a session.

    Lets a component accept a path, a link, a pasted config or a session it
    built itself without each of them re-implementing the dispatch:

    * :class:`XopatSession` -- copied, then `params`/`name` applied.
    * :class:`SessionTemplate` -- bound with `name`, its unfilled slots keeping
      the template's own data.
    * mapping or session JSON text -- :meth:`XopatSession.from_config`, verbatim.
    * ``http(s)`` link -- :meth:`XopatSession.from_url`.
    * existing ``.json`` file -- :meth:`XopatSession.from_file`.
    * anything else -- a path to a background, with `layers` over it.

    `layers`, `params` and the preset apply only on the last branch: a session
    that arrived already built is left as its author built it, which is the
    whole point of the paste path.
    """
    configuration = resolve_preset(preset)
    target = endpoint or configuration.endpoint

    if isinstance(source, XopatSession):
        session = source.copy()
        session.endpoint = target or session.endpoint
    elif isinstance(source, SessionTemplate):
        session = (
            source.bind(name=name, partial=True) if name else source.session.copy()
        )
        session.endpoint = target or session.endpoint
    elif isinstance(source, Mapping):
        session = XopatSession.from_config(source, endpoint=target, strict=strict)
    elif isinstance(source, str):
        text = source.strip()
        if text.lower().startswith(("http://", "https://")):
            session = XopatSession.from_url(text, endpoint=target, strict=strict)
        elif text.startswith("{"):  # session JSON pasted as text
            session = XopatSession.from_config(text, endpoint=target, strict=strict)
        elif Path(text).suffix.lower() == ".json" and Path(text).exists():
            session = XopatSession.from_file(text, endpoint=target, strict=strict)
        else:
            return XopatSession.from_slide(
                source,
                layers,
                name=name,
                params=params,
                endpoint=target,
                preset=configuration,
            )
    else:
        return XopatSession.from_slide(
            source,
            layers,
            name=name,
            params=params,
            endpoint=target,
            preset=configuration,
        )

    if params:
        session.params = deep_merge(session.params, params)
    if name:
        session.bind_name(name)
    return session


__all__ = [
    "STATE_KEYS",
    "RUNTIME_KEYS",
    "SESSION_KEYS",
    "SLIDE_PATTERNS",
    "XopatSession",
    "SessionTemplate",
    "as_session",
    "layers_from_files",
    "sessions_from_paths",
    "sessions_from_folder",
]
