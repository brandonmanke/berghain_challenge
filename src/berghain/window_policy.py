from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Mapping

AttributeId = str


@dataclass
class WindowRelaxedPolicy:
    """
    Sliding-window relaxed variant of QuotaReserve.

    Notation
    - R = remaining capacity, R' = R - 1 after decision
    - S = sum of per-attribute shortfalls (remaining_needed)
    - p_hat = (# helpful in last W arrivals) / W

    Gate for non-helpful candidate
    - Safety: if S >= R, reject (no slack).
    - Warmup: until min_observations, require S < (R - 1).
    - Window gate: accept if p_hat >= S / R' * (1 + risk_margin),
      which ensures E[helpful in remaining] ≈ p_hat * R' ≥ S in expectation.

    Tuning
    - window_size: 300–800 (larger = smoother, slower to adapt).
    - risk_margin: 0.10–0.20.
    - min_observations: 80–150.
    """

    min_counts: Mapping[AttributeId, int]
    capacity: int
    window_size: int = 500
    risk_margin: float = 0.15
    min_observations: int = 100

    # Internal state
    accepted_attribute_counts: Dict[AttributeId, int] = field(default_factory=dict)
    window_helpful: Deque[bool] = field(default_factory=deque)

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

    def _record_window(self, helpful: bool) -> None:
        self.window_helpful.append(helpful)
        while len(self.window_helpful) > self.window_size:
            self.window_helpful.popleft()

    def _p_hat(self) -> float:
        if not self.window_helpful:
            return 0.0
        return sum(1 for x in self.window_helpful if x) / float(len(self.window_helpful))

    def decide(self, admitted_count: int, attributes: Mapping[AttributeId, bool]) -> bool:
        R = max(0, self.capacity - admitted_count)
        remaining_needed = self._remaining_needed()
        helpful = any(
            attributes.get(a, False) and remaining_needed.get(a, 0) > 0 for a in self.min_counts
        )

        # Record helpfulness of the current candidate relative to current needs
        self._record_window(helpful)

        if helpful:
            return True

        S = sum(remaining_needed.values())
        # Hard safety: if no slack, reject non-helpful
        if S >= R:
            return False

        # Early-game fallback to conservative rule
        if len(self.window_helpful) < self.min_observations:
            # Require at least one slot of slack, like QuotaReserve
            return S < (R - 1)

        # Window-based relaxed decision
        R_prime = max(1, R - 1)
        p_hat = self._p_hat()
        req_ratio = S / float(R_prime)
        # Accept non-helpful if recent helpful rate looks sufficient
        return p_hat >= req_ratio * (1.0 + self.risk_margin)

    # Offline priming from logs
    def record_observation(self, helpful: bool) -> None:
        self._record_window(helpful)
