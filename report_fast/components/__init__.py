"""Components that come with the tool.

Each one renders one kind of thing and takes plain data or sessions -- none of
them knows about a database, a server or a slide-reading library. Compose them
into a ``Report``, or write your own ``BaseComponent`` beside them.
"""

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
    "SlideCard",
    "SlideGrid",
    "xOpatViewer",
]
