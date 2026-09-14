"""Aggregated Statistics Component.

Displays summary statistics across multiple slides or experiment runs.
"""

from typing import Any, Dict, List, Optional
from fasthtml.common import FT, Div, H3, Span, Table, Tr, Th, Td, Thead, Tbody
from ..core import BaseComponent, ComponentRegistry


class AggregatedStatistics(BaseComponent):
    """Component for displaying aggregated statistics.
    
    Can aggregate across:
    - Multiple slides (count, formats, total size)
    - Experiment runs (mean metrics, best/worst runs)
    
    Usage:
        stats = AggregatedStatistics(
            id="summary",
            resolver=LocalResolver(base_path="/mnt/data"),
            aggregation="slides"
        )
    """
    
    component_type = "statistics"
    
    def __init__(
        self,
        id: Optional[str] = None,
        resolver=None,
        aggregation: str = "slides",  # 'slides' or 'runs'
        slide_ids: Optional[List[str]] = None,
        metric_names: Optional[List[str]] = None,
        **kwargs
    ):
        super().__init__(id=id, resolver=resolver)
        self.aggregation = aggregation
        self.slide_ids = slide_ids or []
        self.metric_names = metric_names or []
    
    def resolve(self, **kwargs) -> Dict[str, Any]:
        if self.resolver is None:
            return {"aggregation": self.aggregation}
        
        if self.aggregation == "slides":
            # Resolve all slides and aggregate
            slides = []
            for slide_id in self.slide_ids:
                try:
                    data = self.resolver.fetch(slide_id=slide_id)
                    slides.append(data)
                except Exception as e:
                    slides.append({"error": str(e), "slide_id": slide_id})
            return {"slides": slides, "count": len(slides)}
        
        return self.resolver.fetch(**kwargs)
    
    def render(self, data: Any = None, **kwargs) -> FT:
        if data is None:
            data = self.resolve()
        
        if self.aggregation == "slides":
            return self._render_slide_stats(data)
        else:
            return self._render_metric_stats(data)
    
    def _render_slide_stats(self, data: Dict) -> FT:
        slides = data.get("slides", [])
        total = len(slides)
        
        # Count by format
        formats = {}
        errors = 0
        total_size_estimate = 0
        
        for slide in slides:
            if "error" in slide:
                errors += 1
                continue
            fmt = slide.get("format", "unknown").upper()
            formats[fmt] = formats.get(fmt, 0) + 1
            
            # Rough size estimate from level 0 dimensions
            dims = slide.get("dimensions", (0, 0))
            if isinstance(dims, (list, tuple)) and len(dims) == 2:
                # Assume 3 bytes per pixel for RGB
                size_mb = (dims[0] * dims[1] * 3) / (1024 * 1024)
                total_size_estimate += size_mb
        
        # Format breakdown table
        format_rows = []
        for fmt, count in sorted(formats.items()):
            format_rows.append(Tr(
                Td(fmt, cls="stat-format"),
                Td(str(count), cls="stat-count"),
                Td(f"{count/total*100:.1f}%" if total else "0%", cls="stat-pct")
            ))
        
        format_table = Table(
            Thead(Tr(
                Th("Format"),
                Th("Count"),
                Th("Percentage")
            )),
            Tbody(*format_rows),
            cls="stats-table"
        ) if format_rows else Div("No format data available", cls="stats-empty")
        
        return Div(
            H3("Slide Collection Summary", cls="stats-title"),
            Div(
                Span(str(total), cls="stat-big-number", style="font-size: 2rem; font-weight: bold; margin-right: 1rem;"),
                Span("slides", cls="stat-label"),
                cls="stat-header",
                style="margin-bottom: 1rem;"
            ),
            Div(
                Div(f"Successful: {total - errors}", cls="stat-item"),
                Div(f"Errors: {errors}", cls="stat-item"),
                Div(f"Estimated Total Size: {total_size_estimate/1024:.1f} GB", cls="stat-item"),
                cls="stats-summary",
                style="margin-bottom: 1rem;"
            ),
            format_table,
            id=f"stats_{self.id}",
            cls="statistics-container",
            **{"data-total-slides": str(total)}
        )
    
    def _render_metric_stats(self, data: Dict) -> FT:
        runs = data.get("runs", [])
        
        if not runs:
            return Div("No run data available", cls="stats-empty")
        
        # Aggregate metrics
        metric_summaries = {}
        for metric_name in self.metric_names:
            values = []
            for run in runs:
                val = run.get("metrics", {}).get(metric_name)
                if val is not None:
                    values.append(float(val))
            
            if values:
                import statistics
                metric_summaries[metric_name] = {
                    "mean": statistics.mean(values),
                    "stdev": statistics.stdev(values) if len(values) > 1 else 0,
                    "min": min(values),
                    "max": max(values),
                    "count": len(values)
                }
        
        rows = []
        for name, stats in metric_summaries.items():
            rows.append(Tr(
                Td(name, cls="stat-metric-name"),
                Td(f"{stats['mean']:.4f}", cls="stat-mean"),
                Td(f"{stats['stdev']:.4f}", cls="stat-stdev"),
                Td(f"{stats['min']:.4f}", cls="stat-min"),
                Td(f"{stats['max']:.4f}", cls="stat-max"),
                Td(str(stats["count"]), cls="stat-count")
            ))
        
        return Div(
            H3("Metric Aggregations", cls="stats-title"),
            Table(
                Thead(Tr(
                    Th("Metric"),
                    Th("Mean"),
                    Th("Std Dev"),
                    Th("Min"),
                    Th("Max"),
                    Th("Runs")
                )),
                Tbody(*rows),
                cls="stats-table"
            ),
            id=f"stats_{self.id}",
            cls="statistics-container"
        )


ComponentRegistry.register("statistics", AggregatedStatistics)
