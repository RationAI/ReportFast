"""MLflow Metric Table Component.

Renders interactive tables of MLflow metrics with sorting and filtering.
"""

from typing import Any, Dict, List, Optional
from fasthtml.common import FT, Div, Table, Tr, Th, Td, Thead, Tbody, H3, Span
from ..core import BaseComponent, ComponentRegistry
from ..resolvers import MLflowResolver


class MetricTable(BaseComponent):
    """Component for displaying MLflow metrics in a sortable table.
    
    Usage:
        table = MetricTable(
            run_id="abc123",
            metrics=["accuracy", "f1_score", "precision", "recall"],
            resolver=MLflowResolver(tracking_uri="http://mlflow:5000")
        )
    """
    
    component_type = "metric_table"
    
    def __init__(
        self,
        id: Optional[str] = None,
        run_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        metrics: Optional[List[str]] = None,
        resolver: Optional[MLflowResolver] = None,
        max_history: int = 10,
        **kwargs
    ):
        super().__init__(id=id, resolver=resolver)
        self.run_id = run_id
        self.experiment_id = experiment_id
        self.metric_names = metrics or []
        self.max_history = max_history
    
    def resolve(self, **kwargs) -> Dict[str, Any]:
        if self.resolver is None:
            return {"error": "No resolver configured"}
        
        return self.resolver.fetch(
            run_id=self.run_id,
            experiment_id=self.experiment_id,
            metric_names=self.metric_names
        )
    
    def apply_filter(self, filters: List) -> Dict[str, Any]:
        """Apply state filters to the metric data."""
        data = self.resolve()
        
        # Metric tables don't filter by slide metadata typically,
        # but we could filter by run params
        if "run" in data and "params" in data["run"]:
            for f in filters:
                if f.field in data["run"]["params"]:
                    if not f.evaluate(data["run"]["params"]):
                        return {"filtered_out": True}
        
        return data
    
    def render(self, data: Any = None, **kwargs) -> FT:
        if data is None:
            data = self.resolve()
        
        if data.get("filtered_out"):
            return Div("Run filtered out by current filters", cls="metric-table-filtered")
        
        if "error" in data:
            return Div(f"Error loading metrics: {data['error']}", cls="metric-table-error")
        
        run_data = data.get("run", {})
        metrics = run_data.get("metrics", {})
        history = run_data.get("metric_history", {})
        params = run_data.get("params", {})
        
        # Header row with run info
        header = Div(
            H3(f"Metrics: {self.run_id or 'Unknown'}", cls="metric-table-title"),
            Span(f"Status: {run_data.get('status', 'unknown')}", cls=f"status-badge status-{run_data.get('status', 'unknown')}"),
            cls="metric-table-header"
        )
        
        # Current metrics summary table
        summary_rows = []
        for metric_name in self.metric_names:
            value = metrics.get(metric_name, "N/A")
            summary_rows.append(Tr(
                Td(metric_name, cls="metric-name"),
                Td(f"{value:.4f}" if isinstance(value, float) else str(value), cls="metric-value"),
                cls="metric-summary-row"
            ))
        
        summary_table = Table(
            Thead(Tr(
                Th("Metric", cls="metric-header"),
                Th("Latest Value", cls="metric-header")
            )),
            Tbody(*summary_rows),
            cls="metric-summary-table"
        )
        
        # History table (if available)
        history_section = Div(cls="metric-history")
        for metric_name in self.metric_names:
            if metric_name in history and history[metric_name]:
                hist_rows = []
                for point in history[metric_name][-self.max_history:]:
                    hist_rows.append(Tr(
                        Td(str(point.get("step", "")), cls="hist-step"),
                        Td(f"{point.get('value', 0):.6f}", cls="hist-value"),
                        cls="metric-history-row"
                    ))
                
                history_section.children.append(
                    Div(
                        H3(f"{metric_name} History", cls="history-title"),
                        Table(
                            Thead(Tr(
                                Th("Step"),
                                Th("Value")
                            )),
                            Tbody(*hist_rows),
                            cls="metric-history-table"
                        ),
                        cls="metric-history-group"
                    )
                )
        
        return Div(
            header,
            summary_table,
            history_section,
            id=f"metric_table_{self.id}",
            cls="metric-table-container",
            **{"data-component-type": "metric_table", "data-run-id": self.run_id or ""}
        )


ComponentRegistry.register("metric_table", MetricTable)
