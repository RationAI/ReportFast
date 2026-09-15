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

The agent path enters through :func:`expand`: one hand-authored xOpat session
design, validated once against the viewer's own schema, then bound per case in a
plain Python loop, then composed into a page (:class:`Composition`). Nothing in
that chain needs a manifest and nothing it does not ask for is written to disk::

    from report_fast import expand, Composition

    composition = expand(cases, design="design.json", slots={0: "slide", 1: "mask"})
    for case in cases:
        ...
    composition.build("report.html")

Every build also writes `report.provenance.json` beside the HTML -- the record of
what resolved. Nothing is stamped into the page, so a mailed report carries no
provenance; that is ruled, not an oversight.

:mod:`report_fast.frozen` is the set of components that path may use, and
:mod:`report_fast.audit` / :mod:`report_fast.contract` are the session gate and
the viewer facts it is checked against -- exported because a caller auditing its
own sessions, or pinning which viewer a report was verified against, needs them
and should not reach into a private module to do it.
"""

from .audit import Finding, audit, split
from .build import build_report, sessions_for
from .compose import (
    ComposeError,
    ComposeNotFound,
    Composition,
    expand,
    load_design,
    load_session,
    sessions_from_dir,
    temp_dir_sessions,
)
from .contract import viewer_stamp
from .frozen import CompositionError, FROZEN, authorize, violations
from .provenance import (
    Provenance,
    ProvenanceError,
    read as read_provenance,
    sidecar_path,
    verify_pair,
)
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
    # the agent path: author, gate, bind, compose
    "expand",
    "load_design",
    "load_session",
    "sessions_from_dir",
    "temp_dir_sessions",
    "Composition",
    "ComposeError",
    "ComposeNotFound",
    # what the agent path is allowed to put on the page
    "FROZEN",
    "authorize",
    "violations",
    "CompositionError",
    # the gate and the facts it is gated against
    "audit",
    "split",
    "Finding",
    "viewer_stamp",
    # the record every build leaves beside the page
    "Provenance",
    "ProvenanceError",
    "read_provenance",
    "sidecar_path",
    "verify_pair",
]
