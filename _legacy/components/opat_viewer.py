"""xOpat v3 viewer component.

xOpat v3 is a *deployed web application*, not an embeddable JS library: there is
no `xopat.min.js` to load and no viewer object to construct. A report instead
hands the viewer a session in the URL fragment (see `report_fast.xopat`) and
links to it, or frames it.

Nothing is downloaded by this component, so `assets()` only carries the card's
own CSS. Whether the slide actually opens is decided by the xOpat deployment:
this host never needs to read the image, only to name it.
"""

import os
from typing import Any, Dict, List, Optional, Sequence

from fasthtml.common import A, Div, H4, Iframe, Img, P, Span

from ..core import BaseComponent, ComponentRegistry
from ..resolvers import ResolutionContext, Resolver
from ..xopat import XopatEndpoint, build_session, thumbnail_url, viewer_url

# Grants the frame needs to behave like the standalone viewer.
IFRAME_ALLOW = "fullscreen; clipboard-write; clipboard-read"


class xOpatViewer(BaseComponent):
    """Link out to (or embed) an xOpat v3 session for one slide.

    Renders a card with the slide's WSI-Service thumbnail, its overlay layers,
    and a hyperlink carrying the session. With `embed=True` the same URL is
    also framed inline -- which only works when the deployment lists this
    report's origin in `core.server.security.frameAncestors`; the link is the
    fallback that always works.

    Usage:
        viewer = xOpatViewer(
            id="slide_001",
            source="/mnt/data/slide_001.tif",
            shader_layers=[heatmap_shader(name="Probability",
                                          data_source="/mnt/data/prob.tif")],
        )
    """

    component_type = "xopat_viewer"

    def __init__(
        self,
        id: Optional[str] = None,
        source: Optional[str] = None,
        resolver: Optional[Resolver] = None,
        width: str = "100%",
        height: str = "600px",
        shader_layers: Optional[Sequence[Any]] = None,
        show_toolbar: bool = True,
        endpoint: Optional[XopatEndpoint] = None,
        embed: bool = False,
        name: Optional[str] = None,
        session_params: Optional[Dict[str, Any]] = None,
        lossless: bool = True,
        **kwargs: Any,
    ):
        """
        Args:
            source: Slide path or DataID the *image server* resolves. The
                report host does not need to be able to open it.
            resolver: Optional metadata source; used for display only.
            shader_layers: Overlay layers (`ShaderConfig` or plain dicts).
            show_toolbar: Maps onto the viewer's `ui.toolBar` session param.
            endpoint: xOpat deployment; defaults to `DEFAULT_ENDPOINT`.
            embed: Also frame the viewer inline.
            name: Slide label; defaults to the file stem.
            session_params: Extra session `params` (validated against the
                viewer's allowlist by `build_session`).
            lossless: Request lossless overlay tiles, so class-map and heatmap
                colours survive tiling. Pass False for the deployment default.
        """
        super().__init__(id=id, resolver=resolver)
        self.source = source
        self.width = width
        self.height = height
        self.shader_layers = list(shader_layers or [])
        self.show_toolbar = show_toolbar
        self.endpoint = endpoint
        self.embed = embed
        self.name = name
        self.session_params = dict(session_params or {})
        self.lossless = lossless

    @property
    def slide_path(self) -> str:
        """Path/DataID handed to the viewer, preferring resolver output."""
        data = self.resolve()
        return data.get("path") or self.source or ""

    @property
    def session(self) -> Dict[str, Any]:
        """The v3 session this component links to."""
        return build_session(
            self.slide_path,
            layers=self.shader_layers,
            name=self.label,
            params=self._params(),
            endpoint=self.endpoint,
            lossless=self.lossless,
        )

    @property
    def url(self) -> str:
        """Viewer URL carrying `session` in the fragment."""
        return viewer_url(self.session, self.endpoint)

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        return os.path.basename(str(self.slide_path).rstrip("/")) or self.id

    def _params(self) -> Dict[str, Any]:
        """v3 nests chrome flags under `params.ui`; flat `toolBar` is a deprecated alias."""
        params = dict(self.session_params)
        ui = dict(params.get("ui") or {})
        ui.setdefault("toolBar", self.show_toolbar)
        params["ui"] = ui
        return params

    def assets(self) -> List[Dict[str, str]]:
        """The v3 viewer lives on its own host; only the card needs styling."""
        return [
            {
                "asset_type": "inline_css",
                "content": f"""
                .xopat-card {{
                    width: {self.width};
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    overflow: hidden;
                    background: #fff;
                    font-family: system-ui, sans-serif;
                }}
                .xopat-card .xopat-thumb {{
                    display: block;
                    width: 100%;
                    height: {self.height};
                    max-height: {self.height};
                    object-fit: cover;
                    background: #111;
                }}
                .xopat-card .xopat-body {{ padding: 0.75rem 1rem; }}
                .xopat-card .xopat-layers {{
                    color: #555; font-size: 0.85rem; margin: 0.25rem 0 0.5rem;
                }}
                .xopat-card iframe {{ width: 100%; height: {self.height}; border: 0; }}
                """,
                "priority": 3,
            }
        ]

    def resolve(self, **kwargs) -> Dict[str, Any]:
        """Fetch display metadata. Absent or failing resolution is not fatal:
        the session is resolved by the image server, not by this host."""
        if self.resolver is None or not self.source:
            return {"path": self.source or ""}

        context = ResolutionContext(slide_id=str(self.source))
        absolute = isinstance(self.source, str) and os.path.isabs(self.source)
        try:
            data = self.resolver.fetch(
                context=context,
                slide_id=str(self.source),
                path=self.source if absolute else "",
            )
        except Exception as exc:  # resolvers may raise on transport errors
            return {"path": self.source, "error": str(exc)}

        if not isinstance(data, dict):
            return {"path": self.source}
        data.setdefault("path", self.source)
        return data

    def render(self, data: Any = None, **kwargs) -> Div:
        """Render the link card (and optionally the framed viewer)."""
        if data is None:
            data = self.resolve()
        path = data.get("path") or self.source or ""
        layers = [
            getattr(layer, "name", None)
            or (layer.get("name") if isinstance(layer, dict) else None)
            for layer in self.shader_layers
        ]
        layer_names = [name for name in layers if name]

        body = [
            H4(self.label),
            P(
                f"{len(layer_names)} overlay layer(s): {', '.join(layer_names)}"
                if layer_names
                else "No overlay layers.",
                cls="xopat-layers",
            ),
            A("Open in xOpat", href=self.url, target="_blank", rel="noopener"),
            Span(f" {data['error']}", style="color:#a33;font-size:0.8rem;")
            if data.get("error")
            else "",
        ]

        if self.embed:
            body.append(Iframe(src=self.url, allow=IFRAME_ALLOW, loading="lazy"))

        return Div(
            Img(
                src=thumbnail_url(path, self.endpoint),
                alt=f"{self.label} thumbnail",
                loading="lazy",
                cls="xopat-thumb",
            ),
            Div(*body, cls="xopat-body"),
            id=f"container_{self.id}",
            cls="xopat-card",
            **{
                "data-slide-id": str(self.source or ""),
                "data-component-type": self.component_type,
            },
        )


ComponentRegistry.register("xopat_viewer", xOpatViewer)
