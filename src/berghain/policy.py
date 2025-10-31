from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping

AttributeId = str


@dataclass
class QuotaReservePolicy:
    """
    Greedy policy that reserves enough capacity to always be able to
    satisfy remaining per-attribute minimum counts in the worst case.

    - Always accept if the candidate contributes to any underfilled attribute.
    - Otherwise accept only if there is slack: sum(remaining_needed) <= R - 1,
      where R is remaining capacity.
    """

    min_counts: Mapping[AttributeId, int]
    capacity: int

    # Internal state
    accepted_attribute_counts: Dict[AttributeId, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate parameters."""
        from .utils import validate_param

        validate_param("capacity", self.capacity, min_val=1, min_exclusive=False)

    def _remaining_needed(self) -> Dict[AttributeId, int]:
        rem: Dict[AttributeId, int] = {}
        for a, m in self.min_counts.items():
            c = self.accepted_attribute_counts.get(a, 0)
            rem[a] = max(0, m - c)
        return rem

    def remaining_needed(self) -> Dict[AttributeId, int]:
        return self._remaining_needed()

    def update_on_accept(self, attributes: Mapping[AttributeId, bool]) -> None:
        for a, v in attributes.items():
            if v:
                self.accepted_attribute_counts[a] = self.accepted_attribute_counts.get(a, 0) + 1

    def decide(self, admitted_count: int, attributes: Mapping[AttributeId, bool]) -> bool:
        R = max(0, self.capacity - admitted_count)
        remaining_needed = self._remaining_needed()
        helpful = any(
            attributes.get(a, False) and remaining_needed.get(a, 0) > 0 for a in self.min_counts
        )
        if helpful:
            return True

        S = sum(remaining_needed.values())
        # If we don't have at least one slot of slack beyond worst-case needs,
        # reject non-helpful candidates to keep feasibility.
        if S >= R:
            return False
        return True

    # Offline priming from logs (no-op for reserve policy)
    def record_observation(self, helpful: bool) -> None:
        pass
