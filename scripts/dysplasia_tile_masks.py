#!/usr/bin/env python3
"""The dysplasia report, rebuilt the way the Hydra config described it.

    cd /home/jovyan/report_fast && uv sync --extra mlflow
    uv run python scripts/dysplasia_tile_masks.py            # writes the HTML
    uv run python scripts/dysplasia_tile_masks.py --publish  # and logs it on the run

The background is a folder on the mount; the masks are ten directories in four
MLflow runs and one more folder. Each case is one `XopatSession`: its slide plus
every mask that holds a file named after it. `--publish` uploads the report to
the run the config attached it to, as `report/report.html`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

from report_fast import Drive, Mask, Mlflow, MlflowRun, XopatEndpoint, build_report
from report_fast.components import Prose

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "dysplasia_tile_masks.html"

TRACKING_URI = os.environ.get(
    "MLFLOW_TRACKING_URI", "http://mlflow.rationai-mlflow:5000/"
)
# .czi backgrounds go through the wsi_service protocol; everything else about
# the deployment (viewer, tile server, /mnt) is the default.
ENDPOINT = XopatEndpoint(image_protocol="wsi_service")

# Where each mask lives. The report is attached to TILES, which produced them.
PUBLISH_RUN = "e313fc2a62d54057b41b46057577c75d"
TILES = "ad84f424e21742868a746c5fd8a9978b"
QUALITY = "10ec53e2cefd4e589e2d76d3373c8286"
TISSUE = "97084241311949189445f864d42e9d4e"
ANNOTATIONS = "41d5e1d7d43641ea8f645f9b7945e9f7"

BACKGROUND = Drive("/mnt/data/IKEM/colon/IBD_AI/dysplasia")

#: `mask_retrievers`, in config order. Heatmaps by default; `classes` makes a
#: class map. Everything is off until the reader switches it on, except the one
#: layer the config left bare — that one drew by default, so it still does.
MASKS = [
    Mask("Tissue", MlflowRun(TISSUE, "tissue_masks"), color="#ffff00", opacity=0.5),
    Mask(
        "Blur",
        MlflowRun(QUALITY, "qc_output/blur_per_pixel"),
        color="#00ffff",
        opacity=0.5,
    ),
    Mask(
        "Tiles - Blur",
        MlflowRun(TILES, "tile_masks/blur"),
        color="#00ffff",
        opacity=0.5,
    ),
    Mask(
        "Residual artifacts",
        MlflowRun(QUALITY, "qc_output/artifacts_per_pixel"),
        color="#00ffff",
        opacity=0.5,
    ),
    Mask(
        "Tiles - Artifacts",
        MlflowRun(TILES, "tile_masks/artifacts"),
        color="#00ffff",
        opacity=0.5,
    ),
    Mask(
        "Annotations",
        MlflowRun(ANNOTATIONS, "annot_masks"),
        opacity=0.5,
        classes=3,
        palette=["#ffffff", "#ff0000", "#00ff00"],
        breaks=[0.25, 0.75],
        mask=[0, 1, 1],  # class 0 is the slide, not an annotation
    ),
    Mask(
        "Tiles - LG Dysplasia",
        MlflowRun(TILES, "tile_masks/LG Dysplasia"),
        color="#00ff00",
        opacity=0.5,
    ),
    Mask(
        "Tiles - HG Dysplasia",
        MlflowRun(TILES, "tile_masks/HG Dysplasia"),
        color="#ff0000",
        opacity=0.5,
    ),
    Mask(
        "Tiles - Epithelium",
        MlflowRun(TILES, "tile_masks/epithelium"),
        color="#ff00ff",
        opacity=0.5,
    ),
    Mask(
        "Tiles - Outline",
        MlflowRun(TILES, "tile_masks/outlines"),
        color="#ffffff",
        opacity=1.0,
    ),
    Mask(
        "epithelium",
        Drive(
            "/mnt/projects/inflammatory_bowel_disease/ulcerative_colitis_dysplasia"
            "/epithelium_masks/downscale"
        ),
        visible=True,
    ),
]

#: `selected_items` — the 32 cases this report is about, in report order. The
#: folder holds ~370; without this every one of them is a session.
CASES = """
1094_18_HE_0   1095_18_HE_0   1095_18_HE_A   11601_21_HE_0  11601_21_HE_A
""".split()

#: `min_layer_count` — fewer overlays than this and the case is a retrieval
#: mistake, not a finding.
MIN_LAYERS = 3

# Both still the boilerplate the Hydra config shipped with, kept verbatim so the
# two reports read the same. Replace them and the report says something.
DESCRIPTION = (
    "You can now specify a description field in your Hydra config under reporter:. "
    "This text will be displayed before the slides in the generated report."
    "Add your description in the config (YAML)"
)
STATIC_END_TEXT = (
    "You can now specify a description field in your Hydra config under reporter"
)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--publish",
        nargs="?",
        const=PUBLISH_RUN,
        default=None,
        metavar="RUN_ID",
        help=f"log the report on a run (default: {PUBLISH_RUN})",
    )
    args = parser.parse_args(argv)

    flow = Mlflow(tracking_uri=TRACKING_URI)
    report = build_report(
        background=BACKGROUND,
        masks=MASKS,
        only=CASES,
        min_layers=MIN_LAYERS,
        flow=flow,
        endpoint=ENDPOINT,
        title="Dysplasia report",
        subtitle=f"{len(CASES)} cases · {len(MASKS)} overlays",
        intro=DESCRIPTION,
        blocks=[Prose(paragraphs=[STATIC_END_TEXT])],
        out=OUT,
    )
    print(f"wrote {OUT}: {len(CASES)} cases, {len(MASKS)} overlays")

    if args.publish:
        print(f"published {flow.publish(report, run_id=args.publish)}")
    else:
        print(f"  not uploaded — pass --publish to log it on {PUBLISH_RUN}")
    return 0                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 


if __name__ == "__main__":
    sys.exit(main())
