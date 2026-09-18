"""xOpat v3 session and URL construction.

xOpat is a *deployed web application*, not a JS library: a report hands the
viewer a **session** ("dynamic configuration") and the viewer builds its own
OpenSeadragon instance from it. There is no viewer object to instantiate from
Python, so the whole integration surface is (a) a session dict and (b) a URL.

Reference points in xopat 3.1.0 that constrain what this module emits:

  session parsing .... src/parse-input.js (`xOpatParseConfiguration`)
  session types ...... src/types/app.d.ts (`DataID` / `DataOverride` /
                       `BackgroundItem` / `VisualizationItem`)
  params ............. src/types/config.d.ts (`XOpatSetup`), defaulted by
                       src/config.json (the `setup` block)
  shader layers ...... src/libs/flex-renderer/flex-renderer.js
  tile/thumbnail ..... modules/rationai-wsi-tile-source/tile-source.js

This module builds; it does not judge. There is no allowlist of params or
shader types here, and no check that a session key exists upstream -- keeping
one would mean this library had to change every time the viewer does. A session
goes into the fragment exactly as it was assembled; the viewer decides what it
means. The vocabulary lives where the viewer puts it: the files above, read at
build time by whoever authors the session.

v2 -> v3 differences encoded here:

  * `redirect.php` was deleted upstream (xopat 25a0799d, 2024-10-25). v3 reads
    `location.hash` itself, so the session goes on the viewer root directly --
    the *v3* root: where a host serves both majors, the mount picks the version
    (`DEFAULT_BASE_URL`), and a v3 session handed to the v2 mount is parsed by
    v2.
  * `lossless` on `background[]` / `visualizations[]` is gone; v3 asks for the
    tile format per data entry instead (`LOSSLESS_TILE_FORMAT`). Carrying the v2
    key over would be silently ignored.
  * Chrome flags live under `params.ui.*`; the flat `params.toolBar` is a
    deprecated alias the viewer still folds.
  * A shader layer binds to data via **`dataReferences`** (a list), not
    v2's singular `dataReference`, which v3 ignores without warning.
  * A background entry selects its overlay via `visualizationIndex`.
  * A session may only name a **registered** slide protocol. Inline JS
    templates in `protocol` are rejected unconditionally by
    `src/classes/slide-protocols.ts` (an RCE sink), so the v2
    `` `^ ({type:'image',url:...}) ` `` trick is gone.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import warnings
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Mapping, Optional, Sequence, Union

from .layer import DEFAULT_LAYER_TYPE

XOPAT_MAJOR = 3

#: The RationAI host serves the majors side by side under separate mounts — v2
#: at `/xopat/`, where the deleted `redirect.php` relay used to live, and v3 at
#: `/v3/`. The mount selects the viewer version, so a session sent to `/xopat/`
#: is parsed by the v2 viewer.
DEFAULT_BASE_URL = "https://xopat.rationai.cloud.trusted.e-infra.cz/v3/"
DEFAULT_WSI_BASE_URL = "https://xopat.rationai.cloud.trusted.e-infra.cz/wsi-service/"
DEFAULT_MOUNT_ROOT = "/mnt"
DEFAULT_THUMBNAIL_SIZE = 500

TIFF_SUFFIXES = frozenset({".tif", ".tiff"})

#: Tile format requested for overlay data, which is what v2's
#: `visualizations[].lossless` asked the server for. The v3 spelling is a
#: `DataOverride` on the data entry (`{dataID, options: {format}}`), which
#: reaches the tile server as `&image_format=`.
LOSSLESS_TILE_FORMAT = "png"


class XopatError(ValueError):
    """Raised when a session we are about to emit could not load in xOpat v3."""


@dataclass(frozen=True)
class XopatEndpoint:
    """Where the v3 viewer and its image server live.

    Attributes:
        base_url: Viewer root. The session is appended as
            `#<percent-encoded JSON>`.
        wsi_base_url: WSI-Service root that answers `/v3/slides/...`.
        image_protocol: Name of a `slide_protocols` entry registered in the
            deployment's `env.json`, emitted on the background's data entry
            whatever its format. `None` leaves resolution to the deployment's
            `default_background_protocol`.
        mount_root: Filesystem prefix stripped from absolute slide paths to
            form the DataID the image server addresses them by. Pass `""` to
            keep paths absolute, which is what protocols templating a server
            path (an IIP-style entry, say) expect.
    """

    base_url: str = DEFAULT_BASE_URL
    wsi_base_url: str = DEFAULT_WSI_BASE_URL
    image_protocol: Optional[str] = None
    mount_root: str = DEFAULT_MOUNT_ROOT

    @classmethod
    def from_env(cls, **overrides: Any) -> "XopatEndpoint":
        """Read `XOPAT_BASE_URL` / `XOPAT_WSI_BASE_URL` / `XOPAT_IMAGE_PROTOCOL` / `XOPAT_MOUNT_ROOT`."""
        values: Dict[str, Any] = {
            "base_url": os.environ.get("XOPAT_BASE_URL", DEFAULT_BASE_URL),
            "wsi_base_url": os.environ.get("XOPAT_WSI_BASE_URL", DEFAULT_WSI_BASE_URL),
            "image_protocol": os.environ.get("XOPAT_IMAGE_PROTOCOL") or None,
            "mount_root": os.environ.get("XOPAT_MOUNT_ROOT", DEFAULT_MOUNT_ROOT),
        }
        values.update(
            {key: value for key, value in overrides.items() if value is not None}
        )
        return cls(**values)


DEFAULT_ENDPOINT = XopatEndpoint.from_env()


def resolve_endpoint(endpoint: Optional[XopatEndpoint]) -> XopatEndpoint:
    """`endpoint` if given, else the environment-derived default."""
    return endpoint if endpoint is not None else DEFAULT_ENDPOINT


def mount_path(
    path: Union[str, Path], mount_root: Optional[str] = DEFAULT_MOUNT_ROOT
) -> str:
    """Turn a filesystem path into the DataID the image server addresses it by.

    Slides are served from a directory mounted at `mount_root` on both the
    report host and the image server, so the DataID is the path relative to
    that root. Paths already relative (or outside the root) pass through, as do
    all paths when `mount_root` is empty — the form for deployments whose
    protocol template interpolates a whole server-side path.

    `mount_root=None` means "no endpoint was chosen", and takes the default
    root; pass `""` to mean "strip nothing".
    """
    file_path = PurePosixPath(Path(path).as_posix())
    root = PurePosixPath(DEFAULT_MOUNT_ROOT if mount_root is None else mount_root)
    try:
        return str(file_path.relative_to(root))
    except ValueError:
        return file_path.as_posix()


def thumbnail_url(
    slide: Union[str, Path],
    endpoint: Optional[XopatEndpoint] = None,
    size: int = DEFAULT_THUMBNAIL_SIZE,
) -> str:
    """WSI-Service v3 thumbnail URL for a slide.

    The endpoint shape is `{tiles}/thumbnail/max_size/{w}/{h}?slide_id={id}` —
    the same one `RationaiStandaloneV3TileSource.getThumbnail()` builds.
    """
    target = resolve_endpoint(endpoint)
    slide_id = mount_path(slide, target.mount_root)
    return (
        f"{target.wsi_base_url.rstrip('/')}/v3/slides/thumbnail"
        f"/max_size/{size}/{size}?{urllib.parse.urlencode({'slide_id': slide_id})}"
    )


def _reject_inline_protocol(protocol: str) -> None:
    # v3's registry refuses any session-supplied protocol that looks like JS
    # (`INLINE_JS_HINT` in src/classes/slide-protocols.ts) and falls back to the
    # deployment default without an actionable error, so catch it here instead.
    if "`" in protocol or "${" in protocol:
        raise XopatError(
            f"Slide protocol {protocol!r} looks like an inline template. xOpat v3 accepts "
            "only the *name* of an entry registered in the deployment's env.json "
            "(core.client.<active>.slide_protocols) — inline JS was removed as an RCE sink."
        )


def normalise_layer(layer: Any) -> Dict[str, Any]:
    """A layer the caller wrote, as the spec `shader_layer` consumes.

    Accepts a bare path, a plain dict, or the legacy `{'shader_conf': {...}}`
    shape a v2 session carried. The `type` is whatever the caller wrote -- it
    is not checked against a registry, because the registry is the viewer's
    and this library does not keep a copy of it. A type the viewer does not
    register kills its layer in the browser; read
    `src/libs/flex-renderer/flex-renderer.js` for what exists today.
    """
    if isinstance(layer, (str, Path)):
        # `masks=["tumor.tif"]`: the file stem becomes the layer's name.
        layer = {"path": layer}

    if not isinstance(layer, Mapping):
        raise XopatError(
            f"Shader layer must be a path or a mapping, got {type(layer).__name__}"
        )

    conf = layer.get("shader_conf") or {}

    # v2 put params beside `type` inside shader_conf; v3 nests them under `params`.
    conf_structural = {"type", "name", "visible", "fixed", "data", "params"}
    conf_params = {
        key: value for key, value in conf.items() if key not in conf_structural
    }

    spec = {
        "path": layer.get("path") or conf.get("data"),
        "type": layer.get("type") or conf.get("type") or DEFAULT_LAYER_TYPE,
        "name": layer.get("name") or conf.get("name"),
        "visible": layer.get("visible", conf.get("visible", 1)),
        "fixed": layer.get("fixed", conf.get("fixed", False)),
        "params": {
            **conf_params,
            **(conf.get("params") or {}),
            **(layer.get("params") or {}),
        },
    }
    # `group` layers name member layers instead of carrying scalar controls.
    for key in ("shaders", "order"):
        if key in layer:
            spec[key] = layer[key]
    # Per-source tile handling lives on the data entry, not the layer.
    for key in ("options", "smoothing"):
        if key in layer:
            spec[key] = layer[key]
    return spec


def data_entry(
    data_id: str,
    *,
    lossless: bool = False,
    protocol: Optional[str] = None,
    options: Optional[Mapping[str, Any]] = None,
    microns: Optional[float] = None,
    smoothing: Optional[bool] = None,
) -> Any:
    """A `data[]` entry: a bare DataID, or a `DataOverride` carrying per-entry overrides.

    Args:
        data_id: The DataID itself, already mounted.
        lossless: Shorthand for `options={"format": "png"}`.
        protocol: Name of a registered `slide_protocols` entry. It owns the
            `HttpClient`, so per-entry auth is per-entry `protocol`.
        options: `SlideSourceOptions` for the tile source: `plugin` (the
            WSI-Service reader), `format`, `quality`, `channels`.
        microns: Pixel size in micrometers; the scale bar needs it.
        smoothing: `False` samples this source with `gl.NEAREST` instead of
            `gl.LINEAR`, keeping integer label values exact.
    """
    override: Dict[str, Any] = {}
    if protocol:
        # Checked here rather than in `background_protocol()` so a protocol name
        # from any source -- endpoint, preset, explicit argument -- is refused.
        _reject_inline_protocol(protocol)
        override["protocol"] = protocol
    merged_options = dict(options or {})
    if lossless:
        merged_options.setdefault("format", LOSSLESS_TILE_FORMAT)
    if merged_options:
        override["options"] = merged_options
    if microns is not None:
        override["microns"] = microns
    if smoothing is not None:
        override["imageSmoothingEnabled"] = bool(smoothing)
    if not override:
        return data_id
    return {"dataID": data_id, **override}


def background_protocol(background_id: str, target: XopatEndpoint) -> Optional[str]:
    """Protocol name for the background, on its `DataOverride`.

    `background[].protocol` is deprecated in v3 (`src/types/app.d.ts`); the
    current home for the same override is the data entry it points at.

    A configured protocol is honored whatever the extension — the viewer itself
    records one on TIFF data entries — while an unset one leaves the background
    to the deployment's `default_background_protocol`, which reads TIFFs
    natively and so only needs a warning for the formats it may not.
    """
    if target.image_protocol:
        return target.image_protocol
    if Path(background_id).suffix.lower() in TIFF_SUFFIXES:
        return None
    warnings.warn(
        f"Non-TIFF background {background_id!r} resolves through the deployment's "
        "default_background_protocol. If the image server cannot read it, register a "
        "protocol in env.json and point XOPAT_IMAGE_PROTOCOL at its name.",
        stacklevel=3,
    )
    return None


def shader_layer(spec: Mapping[str, Any], data_index: int) -> Dict[str, Any]:
    """Build one v3 shader-layer entry from a normalised layer spec.

    The `type` and `params` are the caller's and go through untouched. What
    this function owns is the v3 wiring: the layer references data by **list**
    index (`dataReferences`; the singular v2 `dataReference` is ignored without
    a word), and `visible` is 1/0.
    """
    if not spec.get("path"):
        raise XopatError(
            f"Shader layer {spec.get('name')!r} needs a path to bind to data."
        )

    name = spec.get("name") or Path(str(spec["path"])).stem
    layer = {
        "type": str(spec["type"]),
        "name": name,
        "visible": 1 if spec.get("visible", 1) else 0,
        "fixed": bool(spec.get("fixed", False)),
        "dataReferences": [data_index],
        "params": dict(spec.get("params") or {}),
    }
    # `group` layers name member layers instead of carrying scalar controls.
    if layer["type"] == "group":
        layer["shaders"] = dict(spec.get("shaders") or {})
        layer["order"] = list(spec.get("order") or [])
    return layer


def build_session(
    slide: Union[str, Path],
    layers: Sequence[Any] = (),
    name: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    endpoint: Optional[XopatEndpoint] = None,
    visualization_name: Optional[str] = None,
    lossless: bool = True,
) -> Dict[str, Any]:
    """Build a v3 session dict that opens `slide` with optional overlay layers.

    Args:
        slide: Background slide, absolute path or DataID.
        layers: Overlay layers. Each is a mapping (`path`, `type`, `name`,
            `params`, optional `visible`/`fixed`; the v2 `shader_conf` wrapper is
            accepted). A mapping that already carries `dataReferences` is
            emitted verbatim.
        name: Display name for the background in the viewer.
        params: Viewer `params` overrides, kept as written.
        endpoint: Deployment coordinates; defaults to `DEFAULT_ENDPOINT`.
        visualization_name: Display name for the visualization.
        lossless: Ask the tile server for lossless overlay tiles, the v3 form of
            v2's `visualizations[].lossless`. Lossy tiles shift the colours of a
            class map, so this is on by default; pass False to accept the
            deployment default. The background keeps the default either way.

    Returns:
        A session dict ready for `viewer_url()`.

    Deprecated:
        `report_fast.session.XopatSession` is the object now -- it builds the
        same session, accepts one someone else wrote, and renders itself to a
        link. This wrapper stays for a release so existing calls keep working.
    """
    from .session import XopatSession

    return XopatSession.from_slide(
        slide,
        layers,
        name=name,
        params=params,
        endpoint=endpoint,
        visualization_name=visualization_name,
        lossless=lossless,
    ).to_config()


def viewer_url(
    session: Mapping[str, Any], endpoint: Optional[XopatEndpoint] = None
) -> str:
    """Full viewer URL carrying `session` in the fragment.

    v3 parses the fragment client-side (`src/parse-input.js`), which is why the
    v2 `redirect.php` relay is gone. Percent-encoding keeps `{ } " ,` out of the
    URL, and the fragment never reaches the server, so sessions stay out of
    access logs.
    """
    target = resolve_endpoint(endpoint)
    payload = json.dumps(session, separators=(",", ":"), ensure_ascii=False)
    return f"{target.base_url.rstrip('/')}/#{urllib.parse.quote(payload)}"


def session_fragment(session: Mapping[str, Any]) -> str:
    """The percent-encoded fragment alone — for assembling links by hand."""
    payload = json.dumps(session, separators=(",", ":"), ensure_ascii=False)
    return urllib.parse.quote(payload)


__all__ = [
    "XOPAT_MAJOR",
    "XopatError",
    "XopatEndpoint",
    "DEFAULT_BASE_URL",
    "DEFAULT_WSI_BASE_URL",
    "DEFAULT_MOUNT_ROOT",
    "DEFAULT_THUMBNAIL_SIZE",
    "DEFAULT_ENDPOINT",
    "resolve_endpoint",
    "mount_path",
    "thumbnail_url",
    "data_entry",
    "background_protocol",
    "normalise_layer",
    "shader_layer",
    "build_session",
    "viewer_url",
    "session_fragment",
]
