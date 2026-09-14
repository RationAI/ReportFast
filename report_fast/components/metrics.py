"""Numbers in a table. Nothing here knows about MLflow or any other store --
pass what you already have."""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from fasthtml.common import FT, Div, H2, Table, Tbody, Td, Th, Thead, Tr

from ..core import BaseComponent, ComponentRegistry

__all__ = ["MetricTable"]


def _cells(value: Any, precision: int) -> List[str]:
    if isinstance(value, bool):
        return ["yes" if value else "no"]
    if isinstance(value, float):
        return [f"{value:.{precision}g}"]
    if isinstance(value, (str, int)) or value is None:
        return ["—" if value is None else str(value)]
    if isinstance(value, Mapping):
        return [
            ", ".join(
                f"{key}={_cells(item, precision)[0]}" for key, item in value.items()
            )
        ]
    if isinstance(value, (list, tuple, set)):
        return [", ".join(_cells(item, precision)[0] for item in value)]
    return [str(value)]


class MetricTable(BaseComponent):
    """A table of plain values.

    Accepts, in the shape most convenient at the call site:

    * ``{"auc": 0.91, "dice": 0.83}`` -- metric / value.
    * ``[("auc", 0.91), ...]`` -- the same, with the order kept.
    * ``{"slide-a": {"auc": 0.9}, ...}`` -- one row per label.
    * ``[{"slide": "a", "auc": 0.9}, ...]`` -- one row per record.

    Args:
        metrics: The values, in any of the shapes above.
        title: Heading above the table.
        columns: Column order for record rows; defaults to the first row's keys.
        headers: Column titles, overriding ``columns``.
        precision: Significant digits for floats.
    """

    component_type = "metric-table"

    def __init__(
        self,
        metrics: Any,
        *,
        title: str = "",
        columns: Optional[Sequence[str]] = None,
        headers: Optional[Sequence[str]] = None,
        precision: int = 4,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.precision = precision
        self.headers, self.rows = _tabulate(metrics, columns, headers)

    def css(self) -> str:
        return """
.rf-metrics h2 { margin: 0 0 10px; font-size: 1.15rem; }
.rf-metrics table { border: 1px solid var(--rf-line); border-radius: var(--rf-radius); overflow: hidden; }
.rf-metrics td:last-child, .rf-metrics th:last-child { text-align: right; }
.rf-metrics td:first-child { font-weight: 500; }
"""

    def render(self) -> FT:
        head = Thead(Tr(*[Th(header) for header in self.headers]))
        body = Tbody(
            *[
                Tr(*[Td(_cells(value, self.precision)[0]) for value in row])
                for row in self.rows
            ]
        )
        parts: List[FT] = []
        if self.title:
            parts.append(H2(self.title))
        parts.append(Table(head, body))
        return Div(*parts, cls="rf-metrics", id=self.id)


def _tabulate(
    metrics: Any,
    columns: Optional[Sequence[str]],
    headers: Optional[Sequence[str]],
) -> Tuple[List[str], List[Sequence[Any]]]:
    if isinstance(metrics, Mapping):
        values = list(metrics.values())
        if values and all(isinstance(value, Mapping) for value in values):
            keys = list(columns or _union_keys(values))
            return (
                list(headers) if headers else [""] + keys,
                [
                    [label] + [record.get(key) for key in keys]
                    for label, record in metrics.items()
                ],
            )
        return list(headers or ["metric", "value"]), [
            [key, value] for key, value in metrics.items()
        ]

    records = list(metrics)
    if not records:
        return list(headers or ["metric", "value"]), []
    if all(isinstance(record, Mapping) for record in records):
        keys = list(columns or _union_keys(records))
        return list(headers or keys), [
            [record.get(key) for key in keys] for record in records
        ]
    if all(isinstance(record, (list, tuple)) for record in records):
        width = max(len(record) for record in records)
        return list(headers or [f"column {i + 1}" for i in range(width)]), [
            list(record) for record in records
        ]
    return list(headers or ["value"]), [[record] for record in records]


def _union_keys(records: Iterable[Mapping[str, Any]]) -> List[str]:
    keys: List[str] = []
    for record in records:
        for key in record:
            if key not in keys:
                keys.append(key)
    return keys


ComponentRegistry.register("metric-table", MetricTable)
