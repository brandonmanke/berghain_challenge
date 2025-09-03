from berghain.ewma_policy import EwmaRelaxedPolicy
from berghain.policy import QuotaReservePolicy
from berghain.window_policy import WindowRelaxedPolicy


def test_quota_reserve_basic():
    policy = QuotaReservePolicy(min_counts={"A": 2, "B": 1}, capacity=5)
    # Helpful candidate (A=True) should be accepted
    assert policy.decide(admitted_count=0, attributes={"A": True, "B": False}) is True
    policy.update_on_accept({"A": True, "B": False})
    # Non-helpful with slack should be accepted
    assert policy.decide(admitted_count=1, attributes={}) is True
    # Force no-slack: remaining S >= R
    # Remaining need S = A:1, B:1 => 2; set admitted=3 so R=2 => S>=R => reject non-helpful
    assert policy.decide(admitted_count=3, attributes={}) is False


def test_window_and_ewma_record_and_decide():
    for Policy in (WindowRelaxedPolicy, EwmaRelaxedPolicy):
        p = Policy(min_counts={"A": 1}, capacity=3)
        # Record a few observations
        for helpful in (True, False, True, True, False):
            p.record_observation(helpful)
        # Decision returns a boolean without raising
        assert isinstance(p.decide(admitted_count=0, attributes={"A": False}), bool)
