#!/usr/bin/env python3
"""The same report, with the slides and the masks read out of an MLflow run.

    cd /home/jovyan/report_fast && uv sync --extra mlflow
    uv run python scripts/test_mlflow.py
    xdg-open reports/report_mlflow.html

Nothing is downloaded. The run's artifact directory is listed, each file becomes
the DataID the tile server addresses it by (`mflow/<experiment>/<run>/artifacts/
<path>`), and those strings go into the report as backgrounds and as overlays --
matched with each other by file stem, which is how the original tool paired them
too.

The report is written locally by default and uploaded only when asked, because
uploading touches someone's run:

    uv run python scripts/test_mlflow.py --publish <run_id>
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from report_fast import Mlflow, MlflowError, build_report
from report_fast.components import LinkList

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPORTS = ROOT / "reports"
OUT = REPORTS / "report_mlflow.html"

#: The tracking API is reachable from inside the cluster; the UI its links point
#: at is a different host, which is why the two are separate settings.
TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow.rationai-mlflow:5000/")
WEB_URL = os.environ.get("MLFLOW_WEB_URL", "https://mlflow.rationai.cloud.trusted.e-infra.cz/")

#: Experiment 111, "Level 1 heatmaps": four prediction variants of the same
#: thirty-two H&E tiles, which is what makes one of them a background and
#: another its overlay without any path guesswork.
RUN = "5b72e2a73b3941f0be63e232d8072127"
BELOW = "heatmaps/epi0_negon"
ABOVE = "heatmaps/epi0_negoff"

LIMIT = 12


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--publish", metavar="RUN_ID", help="upload the report to this run")
    parser.add_argument("--run", default=RUN, help="run to read the artifacts from")
    parser.add_argument("--limit", type=int, default=LIMIT, help="cards to show")
    args = parser.parse_args(argv)

    flow = Mlflow(tracking_uri=TRACKING_URI, web_url=WEB_URL)

    try:
        backgrounds = flow.slides(args.run, BELOW)[: args.limit]
        overlays = flow.masks(args.run, ABOVE, name="epi 0 · negative offset")
        metrics = flow.metrics(args.run)
    except ImportError:
        print("mlflow is not installed in this venv: uv sync --extra mlflow")
        return 1
    except MlflowError as error:
        print(f"cannot read the run: {error}")
        return 1

    report = build_report(
        slides=backgrounds,
        masks=overlays,
        metrics=metrics or None,
        title="Level 1 heatmaps · run report",
        subtitle=(
            f"{len(backgrounds)} backgrounds read out of MLflow run "
            f"{args.run[:8]}… · every image is served from the artifact store"
        ),
        intro=(
            f"Artifacts were never downloaded: `artifacts/{BELOW}` became the "
            f"backgrounds and `artifacts/{ABOVE}` the overlay on top of each, "
            "paired by file stem."
        ),
        blocks=[LinkList({"Run in MLflow": flow.link(args.run)})],
        out=OUT,
    )
    print(f"wrote {OUT}: {len(report.blocks)} blocks, {len(backgrounds)} sessions")
    print(f"  {backgrounds[0]}")

    if args.publish:
        try:
            published = flow.publish(report, run_id=args.publish)
        except MlflowError as error:
            print(f"cannot write the report: {error}")
            return 1
        print(f"published {published}")
    else:
        print("  not uploaded -- pass --publish <run_id> to log it on a run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
