"""The components a report is made of: one slide, or a grid of them.

Two components, and that is the set. ``SlideCard`` renders one session and
``SlideGrid`` renders any number of them; ``Report`` and ``Section`` are the page
they sit on, defined in :mod:`report_fast.core` and re-exported here so every
component name can be imported from one place. The dependency points this way --
``core`` defines the page and the components import ``core`` -- so they are not
moved into ``core``, only reachable through it.

There deliberately used to be ten: prose, headings, bullets, link lists, a
metrics table, a chart, and a raw-HTML escape hatch. They went because a report
the agent writes in March and one it writes in September have to look like the
same report, and every component someone can reach for is a way for the two to
drift apart. What the page needs beyond slides it now carries itself -- a
subtitle and a preamble on :class:`~report_fast.core.Report`.

Nothing stops a report from having a component of its own: subclass
:class:`~report_fast.core.BaseComponent`, give it a class prefix that is not
``rf-``, and add it. That is the documented way to extend the page; the removed
raw-HTML block was not, because it let anything through.
"""

from ..core import Report, Section
from .slide_grid import SlideCard, SlideGrid

__all__ = [
    "Report",
    "Section",
    "SlideCard",
    "SlideGrid",
]
