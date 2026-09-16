"""Components that come with the tool.

Each one renders one kind of thing and takes plain data or sessions -- none of
them knows about a database, a server or a slide-reading library. Compose them
into a ``Report``, or write your own ``BaseComponent`` beside them.

``Report`` and ``Section`` live in :mod:`report_fast.core` and are re-exported
here so that every name in the frozen set can be imported from the one place a
reader looks. They are not moved: ``core`` defines the page and the components
import ``core``, so the dependency points this way and a move would invert it.
The trial run reached for ``components.Section`` and found nothing, which is the
whole argument -- the two spellings should not be a thing you have to know.
"""

from ..core import Report, Section
from .chart import Chart
from .metrics import MetricTable
from .prose import Bullets, Heading, LinkList, Prose, RawHtml
from .slide_grid import SlideCard, SlideGrid, xOpatViewer

__all__ = [
    "Bullets",
    "Chart",
    "Heading",
    "LinkList",
    "MetricTable",
    "Prose",
    "RawHtml",
    "Report",
    "Section",
    "SlideCard",
    "SlideGrid",
    "xOpatViewer",
]
