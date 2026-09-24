from e2e_research.coverage_rise import coverage_rise


def period(matched, coverage, maintenance):
    return {"matched": matched, "coverage": coverage, "maintenance": maintenance}


def test_pooled_growth_does_not_imply_project_level_growth():
    ytd = {
        "a": {"2025": period(90, 45, 20), "2026": period(10, 4, 3)},
        "b": {"2025": period(10, 9, 6), "2026": period(90, 72, 50)},
        "c": {"2025": period(50, 25, 20), "2026": period(50, 25, 30)},
    }
    result = coverage_rise({"ytd": ytd})

    assert result["pooled"]["coverage_delta_pp"] > 0
    assert result["paired_projects"]["coverage_directions"] == {
        "up": 0,
        "down": 2,
        "unchanged": 1,
    }
    assert result["projects_with_at_least_20_e2e_commits_per_year"] == {
        "count": 1,
        "coverage_directions": {"up": 0, "down": 0, "unchanged": 1},
        "maintenance_directions": {"up": 1, "down": 0, "unchanged": 0},
        "equal_project_coverage_share": {"2025": 50, "2026": 50},
    }


def test_comparison_requires_observations_in_both_years():
    ytd = {slug: {"2025": period(10, 4, 3)} for slug in ("a", "b", "c")}
    result = coverage_rise({"ytd": ytd})
    assert result == {
        "available": False,
        "window": "January 1–September 21 in each year",
        "projects": 3,
    }
