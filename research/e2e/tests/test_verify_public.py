import pytest

from e2e_research.verify_public import recalculate


def commit(slug, sha, year, relevance, labels):
    return {
        "repository": slug,
        "sha": sha,
        "date": f"{year}-05-01T12:00:00+00:00",
        "e2e_relevance": relevance,
        "reason": {"labels": labels},
    }


def test_public_counts_keep_overlap_and_reject_non_e2e_origins():
    rows = [
        commit("a", "1", 2025, True, ["coverage", "flakiness"]),
        commit("a", "2", 2026, True, ["coverage"]),
        commit("b", "3", 2025, True, []),
        commit("b", "4", 2026, True, ["environment"]),
        commit("c", "5", 2025, True, ["coverage"]),
        commit("c", "6", 2026, True, ["coverage"]),
        commit("c", "7", 2026, False, ["coverage"]),
    ]
    origin = {
        "slug": "a",
        "sha": "1",
        "all_selected_files": 1,
        "repeat_maintenance": True,
        "repeat_flakiness": True,
        "strict_repeat_labels": ["flakiness"],
    }
    origins = {"cohorts": {"new_files": [origin], "stabilization": [origin]}}
    result = recalculate({"commits": rows}, origins)
    assert (result["records"], result["matched"], result["coverage"]) == (7, 6, 4)
    assert (result["maintenance_union"], result["no_confident_reason"]) == (2, 1)
    assert result["coverage_rise"]["pooled"]["years"]["2026"]["matched"] == 3
    assert result["followups"]["new_files"]["strict_events"] == 1

    origins["cohorts"]["new_files"].append({**origin, "sha": "7"})
    with pytest.raises(ValueError, match="absent from E2E-positive"):
        recalculate({"commits": rows}, origins)
