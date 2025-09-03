from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping

AttributeId = str


@dataclass
class EwmaRelaxedPolicy:
    """
    EWMA-based relaxed reserve policy (single global p_hat).

    Notation
    - capacity: total venue capacity (N), admitted_count: A, remaining capacity R = N - A
    - remaining_needed: vector of per-attribute shortfalls; S = sum(remaining_needed)
    - helpful arrival: contributes to at least one underfilled attribute

    Logic (non-helpful candidate x)
    - Safety gate: if S >= R, reject (no slack: even if every remaining arrival were helpful,
      accepting x consumes a slot needed to meet minimums).
    - Warmup: if observations < warmup_observations, require S < (R - 1) (keep one-slot slack).
    - EWMA gate: with R' = R - 1 and EWMA helpful rate p_hat,
      accept if p_hat >= S / R' * (1 + risk_margin).
      Rationale: E[helpful in remaining] = p_hat * R' >= S ensures expected feasibility; margin
      inflates the requirement to reduce variance risk.

    Tuning
    - alpha: 0.03–0.06 (higher adapts faster, can be noisier).
    - risk_margin: 0.10–0.20 (higher is safer, more rejections).
    - warmup_observations: 80–150 typical.
    """

    min_counts: Mapping[AttributeId, int]
    capacity: int
    alpha: float = 0.03  # smoothing factor in (0,1]
    risk_margin: float = 0.15
    warmup_observations: int = 100

    # Internal state
    accepted_attribute_counts: Dict[AttributeId, int] = field(default_factory=dict)
    p_hat: float = 0.0
    n_obs: int = 0

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

    def _update_p_hat(self, helpful: bool) -> None:
        x = 1.0 if helpful else 0.0
        if self.n_obs == 0:
            self.p_hat = x
        else:
            a = max(1e-6, min(1.0, self.alpha))
            self.p_hat = a * x + (1.0 - a) * self.p_hat
        self.n_obs += 1

    def decide(self, admitted_count: int, attributes: Mapping[AttributeId, bool]) -> bool:
        R = max(0, self.capacity - admitted_count)
        remaining_needed = self._remaining_needed()
        helpful = any(
            attributes.get(a, False) and remaining_needed.get(a, 0) > 0 for a in self.min_counts
        )

        # Update EWMA with helpfulness of this candidate
        self._update_p_hat(helpful)

        if helpful:
            return True

        S = sum(remaining_needed.values())
        if S >= R:
            return False

        # Early-game: conservative safety until enough observations
        if self.n_obs < self.warmup_observations:
            return S < (R - 1)

        R_prime = max(1, R - 1)
        req_ratio = S / float(R_prime)
        return self.p_hat >= req_ratio * (1.0 + self.risk_margin)

    # Offline priming from logs
    def record_observation(self, helpful: bool) -> None:
        self._update_p_hat(helpful)
