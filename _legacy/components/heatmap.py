"""Heatmap Overlay Component.

Renders a standalone heatmap visualization (not embedded in xOpat)
using D3.js or a lightweight canvas renderer.
"""

from typing import Any, Dict, List, Optional
from fasthtml.common import FT, Div, Canvas, Script, H3
from ..core import BaseComponent, ComponentRegistry
from ..shader import heatmap_shader, ShaderConfig


class HeatmapOverlay(BaseComponent):
    """Component for rendering heatmap overlays.
    
    Can be used standalone or linked with an xOpatViewer for synchronized views.
    
    Usage:
        heatmap = HeatmapOverlay(
            id="tumor_pred_001",
            data_source="/mnt/data/predictions/slide_001_heatmap.npy",
            colormap="viridis",
            width=512,
            height=512
        )
    """
    
    component_type = "heatmap"
    
    def __init__(
        self,
        id: Optional[str] = None,
        data_source: Optional[str] = None,
        resolver=None,
        width: int = 512,
        height: int = 512,
        colormap: str = "viridis",
        min_value: float = 0.0,
        max_value: float = 1.0,
        **kwargs
    ):
        super().__init__(id=id, resolver=resolver)
        self.data_source = data_source
        self.width = width
        self.height = height
        self.colormap = colormap
        self.min_value = min_value
        self.max_value = max_value
    
    def assets(self) -> List[Dict[str, str]]:
        return [
            {
                "asset_type": "js",
                "src": "https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js",
                "priority": 1,
            }
        ]
    
    def resolve(self, **kwargs) -> Dict[str, Any]:
        if self.resolver is None:
            return {"data_source": self.data_source}
        
        data = self.resolver.fetch(path=self.data_source)
        
        # If it's a numpy file, we might generate a tile URL or pre-rendered PNG
        if self.data_source.endswith(".npy"):
            data["tile_url"] = f"/api/tiles/heatmap/{self.id}"
        
        return data
    
    def render(self, data: Any = None, **kwargs) -> FT:
        if data is None:
            data = self.resolve()
        
        canvas_id = f"heatmap_{self.id}"
        
        # Simple canvas-based heatmap renderer
        # For production, this would fetch tiles or a full array
        init_script = f"""
        (function() {{
            var canvas = document.getElementById({canvas_id!r});
            if (!canvas) return;
            
            var ctx = canvas.getContext('2d');
            canvas.width = {self.width};
            canvas.height = {self.height};
            
            // Placeholder: draw a gradient heatmap
            // In production, fetch data from {self.data_source!r}
            var gradient = ctx.createLinearGradient(0, 0, {self.width}, {self.height});
            gradient.addColorStop(0, "rgba(68, 1, 84, 0.5)");
            gradient.addColorStop(0.5, "rgba(59, 82, 139, 0.5)");
            gradient.addColorStop(1, "rgba(33, 144, 140, 0.5)");
            
            ctx.fillStyle = gradient;
            ctx.fillRect(0, 0, {self.width}, {self.height});
            
            // Draw legend
            ctx.fillStyle = "#333";
            ctx.font = "12px sans-serif";
            ctx.fillText("Heatmap: {self.id}", 10, 20);
            ctx.fillText("Min: {self.min_value}", 10, {self.height} - 10);
            ctx.fillText("Max: {self.max_value}", {self.width} - 80, {self.height} - 10);
        }})();
        """
        
        return Div(
            H3(f"Heatmap: {self.id}", cls="heatmap-title"),
            Canvas(
                id=canvas_id,
                width=self.width,
                height=self.height,
                cls="heatmap-canvas",
                style=f"width: {self.width}px; height: {self.height}px; border: 1px solid #ddd;"
            ),
            Script(init_script),
            id=f"heatmap_container_{self.id}",
            cls="heatmap-container",
            **{"data-component-type": "heatmap", "data-source": self.data_source or ""}
        )


ComponentRegistry.register("heatmap", HeatmapOverlay)
