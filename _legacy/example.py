"""Example: Build a WSI report for 10 slides in under 20 lines of code.

This demonstrates the Pythonic DSL for assembling reports.
"""

from report_fast import Report, LocalResolver, MLflowResolver
from report_fast.components import (
    xOpatViewer,
    SlideMetadataCard,
    MetricTable,
    HeatmapOverlay,
    AggregatedStatistics,
)
from report_fast.shader import classify_shader

# Line 1: Configure resolvers
slide_resolver = LocalResolver(base_path="/mnt/data/slides")
mlflow_resolver = MLflowResolver(tracking_uri="http://mlflow:5000")

# Lines 2-8: Define slide IDs and build components
slide_ids = [f"slide_{i:03d}" for i in range(1, 11)]

viewers = [
    xOpatViewer(id=f"v_{sid}", source=sid, resolver=slide_resolver) for sid in slide_ids
]
cards = [
    SlideMetadataCard(id=f"m_{sid}", slide_id=sid, resolver=slide_resolver)
    for sid in slide_ids
]

# Lines 9-15: Create a report with aggregated stats and MLflow metrics
report = Report(
    title="WSI Analysis Report: 10-Slide Cohort",
    components=[
        AggregatedStatistics(
            id="summary", resolver=slide_resolver, slide_ids=slide_ids
        ),
        MetricTable(
            id="metrics",
            run_id="run_abc123",
            metrics=["accuracy", "f1", "auc"],
            resolver=mlflow_resolver,
        ),
        *[
            # Interleave metadata cards and viewers
            item
            for pair in zip(cards, viewers)
            for item in pair
        ],
    ],
)

# Lines 16-18: Add a classification overlay to the first slide. A v3 shader
# layer samples tile data, so data_source names a class-encoded image, not JSON.
shader = classify_shader(
    name="Tumor Classification",
    classes=3,
    colors=["#ff0000", "#00ff00", "#0000ff"],
    data_source="/mnt/data/predictions/slide_001_class.tif",
)
report.add(
    xOpatViewer(
        id="v_classified",
        source="slide_001",
        resolver=slide_resolver,
        shader_layers=[shader],
    )
)

# Lines 19-20: Serve the report
if __name__ == "__main__":
    report.serve(port=8000)
