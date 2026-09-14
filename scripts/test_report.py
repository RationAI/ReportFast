#!/usr/bin/env python3
"""Generate the demo reports and open them.

    cd /home/jovyan/report_fast && python scripts/test_report.py
    xdg-open reports/report_output.html

Three ways to get slides onto the page, side by side:

1. `build_report` with a folder of backgrounds and a folder of masks. No config
   of your own, nothing to learn -- this is the out-of-the-box path.
2. `XopatSession` by hand, then any component around it. The base object is the
   one the tool itself uses; this is what a script written from scratch looks
   like.
3. A session somebody pasted in, used exactly as they wrote it.

The mount point below is real data on the cluster. Without it the script falls
back to paths that do not exist on disk: the report still builds and every link
still opens, because a session is a DataID string, not an opened file.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from fasthtml.common import Div, H4, P, Span

from report_fast import (
    BaseComponent,
    Chart,
    MetricTable,
    Prose,
    Report,
    Section,
    SlideCard,
    SlideGrid,
    XopatEndpoint,
    XopatSession,
    build_report,
    sessions_for,
)
from report_fast.components import Bullets, Heading, RawHtml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # the project: examples/ in, reports/ out
REPORTS = ROOT / "reports"
OUT = REPORTS / "report_output.html"

# ── 1. out of the box: a folder of backgrounds and a folder of masks ─────────

SLIDE_DIR = Path("/mnt/projects/epithelium_segmentation/breast/TNBC/tif/ce")
CENTROID_DIR = SLIDE_DIR.parent / "dysplasia/viz/centroid"
POLY_DIR = SLIDE_DIR.parent / "dysplasia/viz/poly"

FALLBACK_SLIDES = [
    Path("/data/slides/TNBC-BF-4-PNG_13.tif"),
    Path("/data/slides/TNBC-BF-5-PNG_34.tif"),
    Path("/data/slides/TNBC-BF-2-PNG_17.tif"),
]

#: The folder holds ~160 tiles; a demo report is easier to read with a dozen.
LIMIT = 12

#: Where the reader's browser finds the viewer and the tile server. Both default
#: to the live deployment, so only the mount and the protocol are spelled out
#: here; XOPAT_BASE_URL / XOPAT_WSI_BASE_URL / XOPAT_IMAGE_PROTOCOL /
#: XOPAT_MOUNT_ROOT override them per run.
ENDPOINT = XopatEndpoint(
    base_url="https://xopat.rationai.cloud.trusted.e-infra.cz/v3/",
    image_protocol="wsi_service",
    mount_root="/mnt",
)


def slides_on_disk() -> list[Path]:
    if not SLIDE_DIR.exists():
        return FALLBACK_SLIDES
    found = sorted(SLIDE_DIR.glob("*.tif"))
    return (found or FALLBACK_SLIDES)[:LIMIT]


def masks_for(slide: Path) -> list[dict]:
    """Per-slide overlays, found by name.

    Label maps go out lossless (`format: png`) with nearest-neighbour sampling:
    a JPEG pass over a class map invents values on the boundaries, and a
    smoothed one invents classes that were never there.
    """
    layers = []
    for label, directory in (("centroids", CENTROID_DIR), ("polygons", POLY_DIR)):
        overlay = directory / slide.name
        if not directory.exists():
            overlay = Path(f"/data/{label}/{slide.name}")
        layers.append(
            {
                "path": str(overlay),
                "name": label,
                "type": "colormap",
                "params": {"opacity": 0.6},
                "options": {"format": "png"},
                "smoothing": False,
            }
        )
    return layers


# ── 2. by hand: the base object, then whatever you want around it ────────────


def hand_built_session(slides: list[Path]) -> XopatSession:
    """Two timepoints of one case as switchable backgrounds of ONE session.

    The reader picks them from the viewer's background menu instead of
    scrolling between two cards. Merging is the tool's job: `data[]` is
    positional, so every reference in the second session is renumbered as it is
    appended.
    """
    first = XopatSession.from_slide(
        slides[0],
        masks_for(slides[0]),
        name=f"{slides[0].stem} · baseline",
        endpoint=ENDPOINT,
        params={"sessionName": f"demo-{slides[0].stem}"},
    )
    if len(slides) < 2:
        return first
    second = XopatSession.from_slide(
        slides[1],
        masks_for(slides[1]),
        name=f"{slides[1].stem} · follow-up",
        endpoint=ENDPOINT,
    )
    return first.merge(second)


# ── 3. pasted in: a session this tool never would have written ───────────────


def pasted_session() -> XopatSession:
    """`examples/dysplasia_case.json`, verbatim.

    A saved session in the shape the viewer writes: ids it minted itself,
    `visualizationIndex: null` on one background, an inline `shaders` array on
    that background, a `sessionName`, and the author's zoom level. Importing
    must not lose any of it, so the card below links the same bytes -- over
    data this deployment serves, so it has a picture too.

    `examples/viewer_export.json` is a colleague's real export and imports
    exactly the same way; it is kept as a test fixture. Its DataIDs live on
    another tile server, so its card has no preview to show.
    """
    return XopatSession.from_file(
        ROOT / "examples" / "dysplasia_case.json",
        endpoint=ENDPOINT,
        drop_state=True,  # the author's pan/zoom is not what you want to show a reader
    )


# ── a component of your own ─────────────────────────────────────────────────


class Callout(BaseComponent):
    """A component written where you use it rather than in the package.

    The report asks a component for two things -- `render()` and a stylesheet --
    and inlines the stylesheet once however many copies land on the page. Note
    the severity: `rf-warn` recolours the rule and the mark without the base
    package knowing the word exists.
    """

    component_type = "callout"

    def __init__(self, title: str, text: str, severity: str = "info", **kwargs) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.text = text
        self.severity = severity

    def css(self) -> str:
        return """
            .rf-callout { display: grid; grid-template-columns: 22px 1fr; gap: 12px;
            align-items: start; border-left: 4px solid var(--rf-accent); }
            .rf-callout .rf-mark { width: 22px; height: 22px; border-radius: 50%; text-align: center;
            line-height: 22px; font-weight: 700; font-size: 0.8rem; color: var(--rf-bg);
            background: var(--rf-accent); }
            .rf-callout h4 { margin: 0; font-size: 0.98rem; }
            .rf-callout .rf-pill { margin-left: 8px; padding: 1px 8px; border: 1px solid var(--rf-line);
            border-radius: 999px; vertical-align: 2px; font-size: 0.68rem; letter-spacing: 0.06em;
            text-transform: uppercase; color: var(--rf-muted); }
            .rf-callout p { margin: 3px 0 0; color: var(--rf-muted); }
            .rf-callout.rf-warn { border-left-color: #d9822b; }
            .rf-callout.rf-warn .rf-mark { background: #d9822b; }
            """

    def render(self):
        return Div(
            Span("!" if self.severity == "warn" else "i", cls="rf-mark"),
            Div(
                H4(self.title, Span(self.severity, cls="rf-pill")),
                P(self.text),
            ),
            cls=f"rf-card rf-callout rf-{self.severity}",
            id=self.id,
        )


def passed_through(sessions: list[XopatSession]) -> str:
    """HTML the report never renders: your own `<style>`, your own markup, an SVG.

    `RawHtml` writes the string between its div and the page untouched, which is
    the point: output from another generator, a snippet from a design system, a
    hand-drawn figure. The stylesheet travels inside the markup rather than
    through `BaseComponent.css()`, so this is the escape hatch taken all the way.
    Bars are the same numbers as the table above, not decoration.
    """
    bars = "".join(
        f'<li><span title="{name}">{name}</span>'
        f'<i style="--w:{row["dice"] * 100:.1f}%"></i>'
        f"<b>{row['dice']:.3f}</b></li>"
        for name, row in list(metrics_for(sessions).items())[:6]
    )
    return f"""
            <div class="xh-panel">
            <style>
                .xh-panel {{ border: 1px solid var(--rf-line); border-radius: 10px;
                padding: 16px; background: var(--rf-card); }}
                .xh-panel header {{ display: flex; gap: 10px; align-items: baseline;
                margin: 0 0 12px; }}
                .xh-panel .xh-tag {{ margin-left: auto; padding: 2px 8px; border-radius: 6px;
                border: 1px solid var(--rf-line); background: var(--rf-bg);
                color: var(--rf-muted); font: 0.7rem ui-monospace, SFMono-Regular, Menlo, monospace; }}
                .xh-bars {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }}
                .xh-bars li {{ display: grid; grid-template-columns: 10rem 1fr 3.4rem; gap: 10px;
                align-items: center; font-size: 0.85rem; }}
                .xh-bars span {{ overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
                color: var(--rf-muted); }}
                .xh-bars i {{ height: 10px; border-radius: 5px; width: var(--w);
                background: linear-gradient(90deg, var(--rf-accent), #7dd3fc); }}
                .xh-bars b {{ text-align: right; font-variant-numeric: tabular-nums; }}
                .xh-foot {{ display: flex; gap: 14px; align-items: center; margin-top: 14px;
                padding-top: 12px; border-top: 1px dashed var(--rf-line);
                font-size: 0.85rem; color: var(--rf-muted); }}
                .xh-foot svg {{ flex: none; }}
            </style>
            <header>
                <strong>Markup from elsewhere</strong>
                <span class="xh-tag">raw html · own &lt;style&gt; · no component class</span>
            </header>
            <ol class="xh-bars">{bars}</ol>
            <div class="xh-foot">
                <svg width="88" height="28" viewBox="0 0 88 28" aria-hidden="true">
                <polyline fill="none" stroke="var(--rf-accent)" stroke-width="2"
                    points="2,24 14,17 26,20 38,9 50,13 62,5 74,11 86,3" />
                <circle cx="62" cy="5" r="3.2" fill="#d9822b" />
                </svg>
                <span>
                Inline SVG, a <code>&lt;style&gt;</code> block, a <code>&lt;details&gt;</code>
                and <mark>one highlighted word</mark> -- whatever your other tool emits
                reaches the reader unchanged.
                <details>
                    <summary>how it got here</summary>
                    <p>
                    <code>RawHtml(html=…)</code> drops the string in with no template and no
                    escaping. The report's own CSS variables are still in scope, which is
                    why this panel follows the page between light and dark.
                    </p>
                </details>
                </span>
            </div>
            </div>
            """


def metrics_for(sessions: list[XopatSession]) -> dict:
    """Numbers from wherever you have them. This tool ships no metric store."""
    random.seed(4)
    return {
        session.names[0]: {
            "tiles": random.randint(1_800, 4_200),
            "dice": round(random.uniform(0.71, 0.93), 3),
            "area_mm2": round(random.uniform(4.0, 60.0), 1),
        }
        for session in sessions
    }


def quality_chart(sessions: list[XopatSession]) -> Chart:
    """A matplotlib figure, inlined as a data URL. Nothing to serve alongside."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    metrics = metrics_for(sessions)
    figure, axes = plt.subplots(figsize=(5.2, 3.0))
    axes.bar(
        [name[:18] for name in metrics],
        [row["dice"] for row in metrics.values()],
        color="#0b6bcb",
    )
    axes.set_ylim(0.6, 1.0)
    axes.set_ylabel("dice")
    axes.tick_params(axis="x", rotation=45, labelsize=7)
    figure.tight_layout()
    chart = Chart.from_matplotlib(figure, caption="Segmentation Dice per slide.")
    plt.close(figure)
    return chart


def main() -> int:
    slides = slides_on_disk()
    print(
        f"{len(slides)} slides from {SLIDE_DIR if SLIDE_DIR.exists() else 'fallback paths'}"
    )

    # No sessionName here on purpose: it is the viewer's persistence namespace,
    # so slides sharing one would also share their saved zoom and pan. Left
    # unset, the viewer derives one per slide and each opens where you sent it.
    sessions = sessions_for(slides, layers_for=masks_for, endpoint=ENDPOINT)
    grid = SlideGrid(sessions, title="From a folder", endpoint=ENDPOINT)

    hand = hand_built_session(slides)
    pasted = pasted_session()

    report = Report(
        title="ReportFast demo",
        subtitle=(
            f"{len(sessions)} slides · generated by scripts/test_report.py · links open "
            f"{ENDPOINT.base_url}"
        ),
    )
    report.add(
        Prose(
            paragraphs=[
                "Every card below is a link. The session travels in the URL's "
                "fragment, so the report is a static file: nothing to serve, "
                "nothing to keep running, safe to mail.",
                "The thumbnails come from the tile server, so they only appear "
                "where the DataIDs resolve. The links work either way.",
            ]
        )
    )

    if sessions:
        report.add(grid)
        report.add(MetricTable(metrics_for(sessions), title="Per slide"))
        report.add(quality_chart(sessions))

    report.add(
        Section(
            "Built by hand",
            [
                Prose(
                    text=(
                        "One session holding two timepoints as backgrounds. The "
                        "viewer's background menu switches between them; the "
                        "card links straight to each."
                    )
                ),
                SlideCard(hand, endpoint=ENDPOINT),
            ],
            subtitle="XopatSession.from_slide(...).merge(...)",
        )
    )

    report.add(
        Section(
            "Pasted in",
            [
                Prose(
                    text=(
                        "A saved session in the shape the viewer writes, used exactly "
                        "as it was authored. Fields this tool knows nothing about "
                        "survive the import and reach the reader's browser unchanged. "
                        "It carries two backgrounds of the same tile -- CE and H&E -- "
                        "and a saved overlay on the first, so the reader switches "
                        "between them inside one viewer tab."
                    )
                ),
                SlideCard(pasted, endpoint=ENDPOINT),
                Bullets(
                    [
                        "background[].id minted by the viewer: kept, both of them.",
                        "visualizationIndex: null: kept, so the H&E opens with no overlay.",
                        "inline shaders on the background: kept.",
                        "sessionName: kept -- it is the viewer's persistence namespace.",
                        "the author's viewport: dropped, unless drop_state=False.",
                    ]
                ),
            ],
            subtitle="XopatSession.from_file('examples/dysplasia_case.json')",
        )
    )

    report.add(Heading("Extended by the caller", level=2))
    report.add(
        Callout(
            "Two ways to add a block",
            "The callout you are reading is a BaseComponent subclass written in "
            "this file; the panel under it is a string. report.add() takes "
            "either, and each brings its own stylesheet.",
        )
    )
    report.add(
        Callout(
            "The overlay disagrees with the mask",
            "Dice is above 0.9 on every tile in the table above, yet the polygon "
            "layer still crosses boundaries it should not. Check the mask before "
            "the model. Severity is just a class: the package has never heard of "
            "rf-warn.",
            severity="warn",
        )
    )
    report.add(RawHtml(html=passed_through(sessions)))

    written = report.write(OUT)
    size_kb = math.ceil(written.stat().st_size / 1024)
    print(f"wrote {written} ({size_kb} KB)")

    # The same slides through the one-call path, for comparison: no Report, no
    # components, no grid. That is the whole of it when you have nothing to add.
    quick = build_report(
        slides,
        layers_for=masks_for,
        title="Dysplasia screen",
        subtitle="Centroid and polygon overlays.",
        endpoint=ENDPOINT,
        params={"theme": "dark", "ui": {"toolBar": False}},
        out=REPORTS / "report_quick.html",
    )
    print(
        f"wrote {REPORTS / 'report_quick.html'}: "
        f"{', '.join(block.component_type for block in quick.blocks)}"
    )
    print(f"open it:  xdg-open {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
