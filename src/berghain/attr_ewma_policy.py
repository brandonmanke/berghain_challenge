from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping

AttributeId = str


@dataclass
class AttributeEwmaPolicy:
    """
    Attribute-aware EWMA reserve policy.

    Idea
    - Maintain per-attribute EWMA probabilities p_hat[a] based on arrivals.
    - A non-helpful candidate is accepted only if, for every underfilled attribute a,
      the expected helpful arrivals in the remaining slots cover the target with margin:
        count[a] + p_hat[a] * (R - 1) >= minCount[a] * (1 + margin)
    - Helpful candidates (matching any underfilled attribute) are always accepted.
    - During warmup (few observations), fall back to conservative reserve logic.

    Parameters
    - alpha: EWMA smoothing in (0,1]. Higher -> adapt faster (0.04–0.06 works well).
    - risk_margin: safety cushion (0.10–0.25). Higher reduces risk but may raise rejections.
    - warmup_observations: observations before relaxing (80–200). Larger is safer.
    - prior_freqs: optional priors from API to initialize p_hat for faster convergence.

    Math notes (non-helpful candidate x)
    - For each underfilled attribute a with shortfall need[a] > 0:
      let current count c[a], minimum m[a], and estimated rate p_hat[a].
      After deciding on x, remaining slots are R' = R - 1. We want in expectation:
          c[a] + p_hat[a] * R' >= m[a] * (1 + margin_eff)
      where margin_eff ∈ [0, risk_margin] scales with tightness S/R' (S = total shortfall).
      Rearranged acceptance test per a:
          p_hat[a] >= (m[a] * (1 + margin_eff) - c[a]) / R'
      We accept x only if this holds for all underfilled a. Helpful candidates are always accepted.
    """

    min_counts: Mapping[AttributeId, int]
    capacity: int
    alpha: float = 0.04
    risk_margin: float = 0.20
    warmup_observations: int = 200
    prior_freqs: Mapping[AttributeId, float] | None = None

    accepted_attribute_counts: Dict[AttributeId, int] = field(default_factory=dict)
    p_hat: Dict[AttributeId, float] = field(default_factory=dict)
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

    def _ewma_update(self, a: AttributeId, x: float) -> None:
        a_s = max(1e-6, min(1.0, self.alpha))
        prev = self.p_hat.get(a)
        if prev is None:
            prev = float(self.prior_freqs.get(a, x) if self.prior_freqs else x)
        self.p_hat[a] = a_s * x + (1.0 - a_s) * prev

    def _record_arrival(self, attributes: Mapping[AttributeId, bool]) -> None:
        # Update EWMA for each constrained attribute using the full attribute vector
        for a in self.min_counts.keys():
            x = 1.0 if bool(attributes.get(a, False)) else 0.0
            self._ewma_update(a, x)
        self.n_obs += 1

    def decide(self, admitted_count: int, attributes: Mapping[AttributeId, bool]) -> bool:
        R = max(0, self.capacity - admitted_count)
        remaining_needed = self._remaining_needed()
        # A candidate is helpful if they contribute to any underfilled attribute
        helpful = any(
            attributes.get(a, False) and remaining_needed.get(a, 0) > 0 for a in self.min_counts
        )

        # Update EWMA with this arrival's attribute vector
        self._record_arrival(attributes)

        if helpful:
            return True

        # If no attributes remain underfilled, accept freely
        if all(v == 0 for v in remaining_needed.values()):
            return True

        # Hard safety: if worst-case needs exceed remaining slots, reject non-helpful
        S = sum(remaining_needed.values())
        if S >= R:
            return False

        # Early-game conservative gating
        if self.n_obs < self.warmup_observations:
            return S < (R - 1)

        # Expectation-based gating for every underfilled attribute
        R_prime = max(1, R - 1)
        # Adaptive margin scales with tightness (S vs R)
        margin_eff = self.risk_margin * min(1.0, (S / float(max(1, R_prime))))
        for a, need in remaining_needed.items():
            if need <= 0:
                continue
            p = self.p_hat.get(a, 0.0)
            expected_help = p * float(R_prime)
            # Require margin to reduce risk of falling short; equivalent to
            #   p_hat[a] >= (target - c[a]) / R'
            target = self.min_counts[a] * (1.0 + margin_eff)
            if self.accepted_attribute_counts.get(a, 0) + expected_help < target:
                return False
        return True

    # Offline priming for resume
    def record_observation(self, helpful: bool) -> None:
        # Not used: for attribute-aware policy, we prime via _record_arrival on the full vector
        pass
