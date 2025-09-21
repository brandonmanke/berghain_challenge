from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

AttributeId = str


@dataclass
class AttributeEwmaPolicy:
    """
    Attribute-aware EWMA reserve policy.

    Idea
    - Maintain per-attribute EWMA probabilities p_hat[a] based on arrivals.
    - A candidate is accepted only if, for every underfilled attribute a,
      the expected helpful arrivals in the remaining slots cover the target with margin:
        (count[a] + contrib[a]) + p_hat[a] * (R - 1) >= minCount[a] * (1 + margin)
      where contrib[a] is 1 when the candidate carries attribute a, else 0.
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
      where margin_eff ∈ [0, risk_margin] scales with how tight the scarcest
      remaining attribute is (max need / R'). We accept x only if this holds for
      every underfilled attribute the candidate does not cover.
    """

    min_counts: Mapping[AttributeId, int]
    capacity: int
    alpha: float = 0.04
    risk_margin: float = 0.20
    warmup_observations: int = 200
    prior_freqs: Optional[Mapping[AttributeId, float]] = None
    # Top-K gating: check only the K tightest underfilled attributes (by need)
    gate_top_k: Optional[int] = None
    # Correlation-aware expectation: inflate p_hat[a] using average positive correlation
    # with other underfilled attributes to account for multi-cover arrivals.
    correlations: Optional[Mapping[AttributeId, Mapping[AttributeId, float]]] = None
    corr_beta: float = 0.25
    corr_include_negative: bool = False

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

        remaining_before = self._remaining_needed()
        helpful = any(
            attributes.get(a, False) and remaining_before.get(a, 0) > 0 for a in self.min_counts
        )

        # Update EWMA with this arrival's attribute vector before making a decision
        self._record_arrival(attributes)

        if R <= 0:
            return False

        # Contribution of this candidate to each constrained attribute
        contributions = {a: 1 if bool(attributes.get(a, False)) else 0 for a in self.min_counts}

        # Remaining capacity after accepting this candidate
        R_post = max(0, R - 1)

        # Attribute counts if we accept the candidate
        counts_after_accept = {
            a: self.accepted_attribute_counts.get(a, 0) + contributions[a] for a in self.min_counts
        }
        remaining_after = {
            a: max(0, self.min_counts[a] - counts_after_accept[a]) for a in self.min_counts
        }
        underfilled = [(a, need) for a, need in remaining_after.items() if need > 0]
        max_need = max((need for _, need in underfilled), default=0)

        # Worst-case guard: if any attribute needs more than the slots left,
        # feasibility is already lost.
        for _, need in underfilled:
            if need > R_post:
                return False

        # During warmup fall back to conservative reserve-style gating:
        # accept helpful arrivals, otherwise require aggregate slack.
        if self.n_obs < self.warmup_observations:
            if helpful:
                return True
            if R_post <= 0:
                return False
            slack_before = sum(remaining_before.values())
            return slack_before < R_post

        # If no attributes remain underfilled after acceptance, the candidate is safe
        if not underfilled:
            return True

        # Adaptive margin scales with the tightest attribute after acceptance
        if R_post == 0:
            margin_eff = self.risk_margin
        else:
            tightness = max_need / float(R_post)
            margin_eff = self.risk_margin * min(1.0, tightness)

        underfilled_all_names = [a for a, _ in underfilled]
        if self.gate_top_k and self.gate_top_k > 0 and len(underfilled) > self.gate_top_k:
            underfilled.sort(key=lambda kv: kv[1], reverse=True)
            underfilled = underfilled[: self.gate_top_k]

        for a, need in underfilled:
            if contributions.get(a, 0) > 0:
                # Candidate already covers this attribute; rejecting would only
                # increase the deficit, so skip expectation gating for it.
                continue
            p = self.p_hat.get(a, 0.0)
            if self.correlations and self.corr_beta != 0.0 and len(underfilled_all_names) > 1:
                corrs = []
                row = self.correlations.get(a, {})
                for b in underfilled_all_names:
                    if b == a:
                        continue
                    r = float(row.get(b, 0.0))
                    if r >= 0.0 or self.corr_include_negative:
                        corrs.append(r)
                if corrs:
                    avg_corr = sum(corrs) / float(len(corrs))
                    p = min(1.0, max(0.0, p * (1.0 + self.corr_beta * avg_corr)))

            expected_help = p * float(R_post)
            target = self.min_counts[a] * (1.0 + margin_eff)
            if counts_after_accept[a] + expected_help < target:
                return False
        return True

    # Offline priming for resume
    def record_observation(self, helpful: bool) -> None:
        # Not used: for attribute-aware policy, we prime via _record_arrival on the full vector
        pass
