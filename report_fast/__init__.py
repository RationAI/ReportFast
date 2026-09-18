"""ReportFast: static HTML reports over xOpat v3 sessions.

Everything here is built on one value object, :class:`XopatSession`, which holds
the session document the v3 viewer boots from and turns it into a link. Reports
are collections of components rendered once into a self-contained HTML file --
no server, no JavaScript, no slide-reading library::

    from report_fast import XopatSession, SlideCard

    session = XopatSession.from_slide(
        "/mnt/slides/case_001.tif",
        [{"path": "/mnt/masks/tumor_001.tif", "name": "Tumor"}],
        name="case_001",
    )
    SlideCard(session).to_html()

Slides, masks and reports that live in an MLflow run go through :class:`Mlflow`
-- it turns artifacts into the DataIDs the tile server addresses rather than
downloading anything -- and :func:`sessions_from_masks` pairs a folder of masks
with a folder of slides by filename stem.

A report is written by a script, not by a command: the loop over a folder is a
few lines here, and those lines are the record of the report. Nothing in this
package writes a file unless it is told where to::

    from pathlib import Path

    from report_fast import Report, SlideGrid, XopatSession

    slides = Path("/mnt/slides")
    sessions = [
        XopatSession.from_slide(path, name=path.stem)
        for path in sorted(slides.glob("*.tif"))
    ]
    Report(title="QC", blocks=[SlideGrid(sessions=sessions)]).write("report.html")

The session JSON itself is authored -- by a person or, normally, by an agent
reading the viewer's own type definitions. :meth:`XopatSession.from_config` keeps
every key it is handed, so a field this package has never heard of survives; what
it does keep for itself is the one structural rule the viewer punishes silently,
that `data[]` indices are minted by :meth:`XopatSession.add_data` rather than
typed.
"""

from .components import (
    Bullets,
    Chart,
    Heading,
    LinkList,
    MetricTable,
    Prose,
    RawHtml,
    SlideCard,
    SlideGrid,
)
from .config import BUILTIN_PRESET, SessionPreset, load_preset, parse_preset
from .core import BASE_CSS, BaseComponent, ComponentRegistry, Report, Section
from .layer import colormap_layer, heatmap_layer
from .masks import Drive, Mask, MaskSource, MlflowRun, sessions_from_masks
from .mlflow import Mlflow, MlflowError, Published, artifact_data_id
from .session import (
    SessionTemplate,
    XopatSession,
    as_session,
    layers_from_files,
    sessions_from_folder,
    sessions_from_paths,
)
from .xopat import (
    DEFAULT_BASE_URL,
    XopatEndpoint,
    XopatError,
    mount_path,
    thumbnail_url,
    viewer_url,
)

__all__ = [
    # the base object
    "XopatSession",
    "SessionTemplate",
    "as_session",
    "sessions_from_folder",
    "sessions_from_paths",
    "layers_from_files",
    # defaults
    "SessionPreset",
    "BUILTIN_PRESET",
    "load_preset",
    "parse_preset",
    # the wire format
    "XopatEndpoint",
    "XopatError",
    "DEFAULT_BASE_URL",
    "mount_path",
    "thumbnail_url",
    "viewer_url",
    # report shell
    "Report",
    "Section",
    "BaseComponent",
    "ComponentRegistry",
    "BASE_CSS",
    # components
    "Bullets",
    "Chart",
    "Heading",
    "LinkList",
    "MetricTable",
    "Prose",
    "RawHtml",
    "SlideCard",
    "SlideGrid",
    # layer specs -- the only place a shader type name is written
    "colormap_layer",
    "heatmap_layer",
    # masks and where they come from
    "Mask",
    "Drive",
    "MlflowRun",
    "MaskSource",
    "sessions_from_masks",
    # runs (only these four touch mlflow, and only when they have to)
    "Mlflow",
    "MlflowError",
    "Published",
    "artifact_data_id",
]
