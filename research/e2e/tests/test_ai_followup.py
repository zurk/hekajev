import pytest

from e2e_research.ai_followup import describe, standardized
from e2e_research.attribution import AI_PATTERN, strip_attribution


def origin(index, quarter, ai, delay):
    return {
        "slug": "demo",
        "sha": str(index),
        "quarter": quarter,
        "ai_attributed": ai,
        "first_maintenance_days": delay,
        "repeat_maintenance": delay is not None,
        "repeat_flakiness": False,
        "repeat_any": delay is not None,
        "window": 30,
    }


def test_period_matching_removes_an_artificial_ai_gap():
    rows = []
    for quarter, ai, n, delay in (
        ("Q1", True, 5, None),
        ("Q1", False, 95, None),
        ("Q2", True, 95, 2),
        ("Q2", False, 5, 2),
    ):
        rows.extend(origin(len(rows) + i, quarter, ai, delay) for i in range(n))
    raw = standardized(rows, [], 5)
    adjusted = standardized(rows, ["quarter"], 5)
    assert raw["metrics"]["maintenance_window"]["delta"] == 90
    assert adjusted["metrics"]["maintenance_window"]["delta"] == pytest.approx(0)
    assert adjusted["metrics"]["days_without_maintenance"]["delta"] == pytest.approx(0)
    assert adjusted["metrics"]["maintenance_window"]["bootstrap_95"] is None


def test_nonreturns_contribute_the_full_window_to_timing():
    result = describe([origin(0, "Q1", True, 4), origin(1, "Q1", True, None)])
    assert result["estimates"]["days_without_maintenance"] == 17
    assert result["estimates"]["maintenance_7d"] == 50
    assert result["conditional_median_days"] == 4
    assert result["conditional_events"] == 1


def test_disjoint_periods_are_not_controls():
    rows = [origin(i, "Q1" if i < 30 else "Q2", i < 30, 2) for i in range(60)]
    result = standardized(rows, ["quarter"], 3)
    assert result["n_projects"] == 0
    assert result["metrics"] == {}


def test_ai_topic_is_not_attribution_and_trailers_do_not_inflate_message_size():
    assert not AI_PATTERN.search("Fix Claude API integration tests")
    message = "Fix login tests\n\nCo-authored-by: Claude <bot@example.com>"
    assert AI_PATTERN.search(message)
    assert strip_attribution(message) == "Fix login tests"
