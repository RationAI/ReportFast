"""ReportFast: static HTML reports over xOpat v3 sessions.

Everything here is built on one value object, :class:`XopatSession`, which holds
the session document the v3 viewer boots from and turns it into a link. Reports
are collections of components rendered once into a self-contained HTML file --
no server, no JavaScript, no slide-reading library.

    from report_fast import XopatSession, SlideCard

    session = XopatSession.from_slide(
        "/mnt/slides/case_001.tif",
        [{"path": "/mnt/masks/tumor_001.tif", "name": "Tumor"}],
        name="case_001",
    )
    SlideCard(session).to_html()

For the whole report in one call, see :func:`build_report`. Slides, masks and
reports that live in an MLflow run go through :class:`Mlflow` -- it turns
artifacts into the DataIDs the tile server addresses rather than downloading
anything.
"""

from .build import build_report, sessions_for
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
from .shader import ShaderConfig, ShaderParameter, ShaderType
from .xopat import (
    DEFAULT_BASE_URL,
    PARAM_KEYS,
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
    "PARAM_KEYS",
    "mount_path",
    "thumbnail_url",
    "viewer_url",
    # report shell
    "Report",
    "Section",
    "BaseComponent",
    "ComponentRegistry",
    "BASE_CSS",
    "build_report",
    "sessions_for",
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
    # layer specs
    "ShaderConfig",
    "ShaderParameter",
    "ShaderType",
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
