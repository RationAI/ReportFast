"""State Management for ReportFast.

This module handles:
1. Filtering: Users can filter slides by classification, metadata values, etc.
2. Cross-component state: Changes in one component (e.g., a filter dropdown) 
   propagate to other components (e.g., metric tables update to show only 
   filtered slides).
3. URL/Query parameter serialization for shareable filtered views.

State Management Strategy:
- ReportState is a lightweight dataclass that components bind to.
- When filters change, components re-render with the new state.
- FastHTML's HTMX integration allows partial page updates.
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import json


class FilterOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    LT = "lt"
    GTE = "gte"
    LTE = "lte"
    IN = "in"
    CONTAINS = "contains"
    REGEX = "regex"


@dataclass
class Filter:
    """A single filter condition.
    
    Example:
        Filter(field="tissue_type", operator=FilterOperator.EQ, value="tumor")
        Filter(field="confidence", operator=FilterOperator.GTE, value=0.85)
        Filter(field="slide_id", operator=FilterOperator.IN, value=["S01", "S02"])
    """
    field: str
    operator: FilterOperator
    value: Any
    component_type: Optional[str] = None  # Which component type this filter targets
    
    def evaluate(self, data: Dict[str, Any]) -> bool:
        """Evaluate this filter against a data record (dict)."""
        if self.field not in data:
            return False
        
        record_value = data[self.field]
        
        if self.operator == FilterOperator.EQ:
            return record_value == self.value
        elif self.operator == FilterOperator.NE:
            return record_value != self.value
        elif self.operator == FilterOperator.GT:
            return record_value > self.value
        elif self.operator == FilterOperator.LT:
            return record_value < self.value
        elif self.operator == FilterOperator.GTE:
            return record_value >= self.value
        elif self.operator == FilterOperator.LTE:
            return record_value <= self.value
        elif self.operator == FilterOperator.IN:
            return record_value in self.value
        elif self.operator == FilterOperator.CONTAINS:
            return self.value in str(record_value)
        elif self.operator == FilterOperator.REGEX:
            import re
            return bool(re.search(self.value, str(record_value)))
        
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "operator": self.operator.value,
            "value": self.value,
            "component_type": self.component_type,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Filter":
        return cls(
            field=d["field"],
            operator=FilterOperator(d["operator"]),
            value=d["value"],
            component_type=d.get("component_type"),
        )


@dataclass
class ReportState:
    """Global state for a Report instance.
    
    Components bind to this state and use it to:
    1. Access current filter values
    2. Track selection state (e.g., selected slides)
    3. Share data between components (e.g., a color scale used by multiple viewers)
    
    The state is intentionally simple (dict-based) to work well with FastHTML's
    request/response cycle. For complex reactive updates, we use HTMX.
    """
    
    filters: List[Filter] = field(default_factory=list)
    selection: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    _listeners: List[Callable] = field(default_factory=list, repr=False)
    
    def add_filter(self, filter_obj: Filter):
        """Add a filter condition."""
        self.filters.append(filter_obj)
        self._notify()
    
    def remove_filter(self, field: str):
        """Remove all filters on a field."""
        self.filters = [f for f in self.filters if f.field != field]
        self._notify()
    
    def clear_filters(self):
        """Clear all filters."""
        self.filters.clear()
        self._notify()
    
    def update_filters(self, filter_data: Dict[str, Any]):
        """Update filters from a dictionary (e.g., from POST request)."""
        self.filters = []
        for key, value in filter_data.items():
            if isinstance(value, dict):
                self.filters.append(Filter.from_dict({"field": key, **value}))
            else:
                # Simple equality filter
                self.filters.append(Filter(
                    field=key,
                    operator=FilterOperator.EQ,
                    value=value
                ))
        self._notify()
    
    def get_filter_for(self, component_type: str) -> List[Filter]:
        """Get filters relevant to a component type."""
        return [
            f for f in self.filters 
            if f.component_type is None or f.component_type == component_type
        ]
    
    def apply_filters(self, records: List[Dict]) -> List[Dict]:
        """Apply all current filters to a list of data records."""
        if not self.filters:
            return records
        
        return [
            record for record in records
            if all(f.evaluate(record) for f in self.filters)
        ]
    
    def select(self, item_id: str):
        """Add an item to the selection."""
        if item_id not in self.selection:
            self.selection.append(item_id)
            self._notify()
    
    def deselect(self, item_id: str):
        """Remove an item from the selection."""
        if item_id in self.selection:
            self.selection.remove(item_id)
            self._notify()
    
    def set_context(self, key: str, value: Any):
        """Set a context value (e.g., shared color scale)."""
        self.context[key] = value
    
    def get_context(self, key: str, default: Any = None) -> Any:
        """Get a context value."""
        return self.context.get(key, default)
    
    def _notify(self):
        """Notify registered listeners of state change."""
        for listener in self._listeners:
            listener(self)
    
    def subscribe(self, callback: Callable):
        """Subscribe to state changes."""
        self._listeners.append(callback)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize state to dictionary."""
        return {
            "filters": [f.to_dict() for f in self.filters],
            "selection": self.selection,
            "context": self.context,
        }
    
    def to_query_string(self) -> str:
        """Convert state to URL query string for shareable views."""
        from urllib.parse import urlencode
        params = {}
        for f in self.filters:
            params[f"filter_{f.field}"] = json.dumps(f.to_dict())
        if self.selection:
            params["selection"] = ",".join(self.selection)
        return urlencode(params)
