"""Slide Metadata Card Component.

Displays key metadata about a WSI slide in a compact card format.
Optimized for Big Data: only metadata is loaded, never the full image.
"""

from typing import Any, Dict, Optional
from fasthtml.common import FT, Div, H3, Span, P, Img
from ..core import BaseComponent, ComponentRegistry
from ..resolvers import LocalResolver


class SlideMetadataCard(BaseComponent):
    """Card component showing slide metadata.
    
    Displays:
    - Thumbnail (pre-generated)
    - Dimensions and magnification
    - Vendor/format info
    - Custom metadata fields
    
    Usage:
        card = SlideMetadataCard(
            slide_id="slide_001",
            resolver=LocalResolver(base_path="/mnt/data"),
            extra_fields=["patient_id", "stain", "date_captured"]
        )
    """
    
    component_type = "metadata_card"
    
    def __init__(
        self,
        id: Optional[str] = None,
        slide_id: Optional[str] = None,
        source: Optional[str] = None,
        resolver=None,
        extra_fields: Optional[list] = None,
        show_thumbnail: bool = True,
        **kwargs
    ):
        super().__init__(id=id, resolver=resolver)
        self.slide_id = slide_id or id
        self.source = source or slide_id
        self.extra_fields = extra_fields or []
        self.show_thumbnail = show_thumbnail
    
    def resolve(self, **kwargs) -> Dict[str, Any]:
        if self.resolver is None:
            return {"slide_id": self.slide_id, "source": self.source}
        
        return self.resolver.fetch(slide_id=self.source or self.slide_id)
    
    def render(self, data: Any = None, **kwargs) -> FT:
        if data is None:
            data = self.resolve()
        
        if "error" in data:
            return Div(
                f"Error loading metadata: {data['error']}",
                cls="metadata-card error",
                id=f"meta_{self.id}"
            )
        
        # Extract key metadata
        dims = data.get("dimensions", ("?", "?"))
        dim_str = f"{dims[0]} x {dims[1]}" if isinstance(dims, (list, tuple)) else str(dims)
        
        level_count = data.get("level_count", 1)
        mpp_x = data.get("mpp_x", 0)
        mpp_y = data.get("mpp_y", 0)
        vendor = data.get("vendor", "Unknown")
        fmt = data.get("format", "?").upper()
        
        # Thumbnail
        thumb = None
        if self.show_thumbnail and "thumbnail_path" in data:
            thumb = Img(
                src=f"/thumb/{self.slide_id}",
                alt=f"Thumbnail for {self.slide_id}",
                cls="slide-thumbnail",
                style="max-width: 180px; max-height: 180px; border-radius: 4px; margin-right: 1rem;"
            )
        
        # Core metadata fields
        fields = [
            ("ID", self.slide_id),
            ("Format", fmt),
            ("Vendor", vendor),
            ("Dimensions", dim_str),
            ("Levels", str(level_count)),
            ("MPP X", f"{mpp_x:.3f}" if mpp_x else "N/A"),
            ("MPP Y", f"{mpp_y:.3f}" if mpp_y else "N/A"),
        ]
        
        # Extra fields from properties
        props = data.get("properties", {})
        for field_name in self.extra_fields:
            if field_name in props:
                fields.append((field_name.replace("_", " ").title(), props[field_name]))
        
        # Build field elements
        field_elements = []
        for label, value in fields:
            field_elements.append(Div(
                Span(f"{label}:", cls="meta-label", style="font-weight: bold; margin-right: 0.5rem;"),
                Span(str(value), cls="meta-value"),
                cls="meta-field",
                style="margin-bottom: 0.3rem;"
            ))
        
        # Card layout
        content = Div(
            *field_elements,
            cls="meta-fields",
            style="flex: 1;"
        )
        
        return Div(
            H3(f"Slide: {self.slide_id}", cls="meta-title", style="margin-top: 0; margin-bottom: 0.5rem;"),
            Div(
                thumb if thumb else "",
                content,
                cls="meta-body",
                style="display: flex; align-items: flex-start;"
            ),
            id=f"meta_{self.id}",
            cls="metadata-card",
            style="border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin-bottom: 1rem; background: #fafafa;",
            **{"data-slide-id": self.slide_id, "data-vendor": vendor, "data-format": fmt}
        )


ComponentRegistry.register("metadata_card", SlideMetadataCard)
