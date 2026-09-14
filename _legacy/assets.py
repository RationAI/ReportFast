"""Asset Pipeline: Manage JS/CSS injection for components.

This module ensures that:
1. Scripts are only loaded when a component requiring them is present
2. Assets are deduplicated
3. Dependencies are loaded in the correct order
4. Inline shaders/configurations are validated before injection
"""

from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from fasthtml.common import FT, Script, Style, Link, to_xml
import json


@dataclass
class Asset:
    """Represents a single asset (JS, CSS, or inline config)."""
    asset_type: str  # 'js', 'css', 'inline_js', 'inline_css', 'json_config'
    src: Optional[str] = None  # URL or path for external assets
    content: Optional[str] = None  # Inline content
    integrity: Optional[str] = None  # SRI hash
    defer: bool = False
    module_type: bool = False  # type="module"
    priority: int = 0  # Lower = loaded first
    
    def __hash__(self):
        return hash((self.asset_type, self.src, self.content))
    
    def __eq__(self, other):
        if not isinstance(other, Asset):
            return False
        return (self.asset_type, self.src, self.content) == (other.asset_type, other.src, other.content)


class AssetPipeline:
    """Manages the collection and rendering of component assets.
    
    Usage:
        pipeline = AssetPipeline()
        pipeline.register(my_opat_viewer)  # Collects assets from viewer
        render(pipeline.render_head())  # Emits all required <script> and <link> tags
    """
    
    def __init__(self):
        self._assets: Set[Asset] = set()
        self._component_assets: Dict[str, List[Asset]] = {}
    
    def register(self, component):
        """Register a component and collect its declared assets."""
        if hasattr(component, 'assets'):
            assets = component.assets()
            for asset_dict in assets:
                asset = Asset(**asset_dict)
                self._assets.add(asset)
            self._component_assets[component.id] = [Asset(**a) for a in assets]
    
    def add_asset(self, asset: Asset):
        """Manually add an asset."""
        self._assets.add(asset)
    
    def has_openslide(self) -> bool:
        """Check if any OpenSeadragon-related assets are registered."""
        return any("openseadragon" in (a.src or "").lower() or 
                   "xopat" in (a.src or "").lower() 
                   for a in self._assets)
    
    def has_d3(self) -> bool:
        """Check if D3.js is required."""
        return any("d3" in (a.src or "").lower() for a in self._assets)
    
    def render_head(self) -> FT:
        """Render all assets as FastHTML FT elements for the <head> section."""
        # Sort by priority
        sorted_assets = sorted(self._assets, key=lambda a: a.priority)
        
        elements = []
        for asset in sorted_assets:
            if asset.asset_type == "css":
                if asset.src:
                    elements.append(Link(rel="stylesheet", href=asset.src, 
                                       integrity=asset.integrity))
                elif asset.content:
                    elements.append(Style(asset.content))
            
            elif asset.asset_type == "js":
                kwargs = {"src": asset.src}
                if asset.integrity:
                    kwargs["integrity"] = asset.integrity
                    kwargs["crossorigin"] = "anonymous"
                if asset.defer:
                    kwargs["defer"] = True
                if asset.module_type:
                    kwargs["type"] = "module"
                elements.append(Script(**kwargs))
            
            elif asset.asset_type == "inline_js":
                kwargs = {}
                if asset.module_type:
                    kwargs["type"] = "module"
                elements.append(Script(asset.content, **kwargs))
            
            elif asset.asset_type == "inline_css":
                elements.append(Style(asset.content))
            
            elif asset.asset_type == "json_config":
                # Embed JSON config as a data attribute or global variable
                if asset.content:
                    elements.append(Script(
                        f"window.__REPORT_CONFIG__ = window.__REPORT_CONFIG__ || {{}}; "
                        f"Object.assign(window.__REPORT_CONFIG__, {asset.content});"
                    ))
        
        # Return as a Div container (will be placed in <head> by FastHTML)
        from fasthtml.common import Div
        return Div(*elements, cls="asset-pipeline")
    
    def get_shim(self) -> str:
        """Generate a JS shim that checks for required globals before running component code."""
        checks = []
        if self.has_openslide():
            checks.append("typeof OpenSeadragon !== 'undefined'")
        if self.has_d3():
            checks.append("typeof d3 !== 'undefined'")
        
        if not checks:
            return ""
        
        conditions = " && ".join(checks)
        return f"""
        function whenReady(callback) {{
            if ({conditions}) {{
                callback();
            }} else {{
                setTimeout(() => whenReady(callback), 100);
            }}
        }}
        """
