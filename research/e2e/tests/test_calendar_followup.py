import pytest

from e2e_research.calendar_followup import describe, origin_cutoff, paired, same_season


def row(slug, year, repeats):
    return {
        "slug": slug,
        "year": year,
        "date": f"{year}-01-10T12:00:00+00:00",
        "window": 30,
        "repeat_any": repeats,
        "first_repeat_days": 2 if repeats else None,
        "repeat_maintenance": False,
        "first_maintenance_days": None,
        "repeat_flakiness": False,
    }


def test_matching_origin_season_excludes_late_old_origins_too():
    cutoff = origin_cutoff(
        {
            "a": {"observed_until": "2026-09-21T11:00:00Z"},
            "b": {"observed_until": "2026-09-21T20:00:00Z"},
        },
        30,
    )
    assert cutoff.isoformat() == "2026-08-21T23:59:59+00:00"
    for year in (2025, 2026):
        assert same_season({"date": f"{year}-08-21T23:59:59Z"}, cutoff)
        assert not same_season({"date": f"{year}-08-22T00:00:00Z"}, cutoff)


def test_project_composition_cannot_become_a_paired_time_trend():
    rows = (
        [row("low", 2025, False)] * 90
        + [row("high", 2025, True)] * 10
        + [row("low", 2026, False)] * 10
        + [row("high", 2026, True)] * 90
    )
    before = describe([r for r in rows if r["year"] == 2025])
    after = describe([r for r in rows if r["year"] == 2026])
    assert after["estimates"]["any_touch_window"] - before["estimates"]["any_touch_window"] == 80
    result = paired(rows, (2025,), (2026,), minimum=10)
    assert result["n_projects"] == 2
    assert result["metrics"]["any_touch_window"]["delta"] == pytest.approx(0)
    assert result["metrics"]["any_touch_window"]["bootstrap_95"] is None
    assert paired(rows, (2025,), (2026,), minimum=20)["n_projects"] == 0


def test_any_file_edit_is_distinct_from_labeled_maintenance():
    result = describe([row("demo", 2025, True), row("demo", 2025, False)])
    assert result["estimates"]["any_touch_window"] == 50
    assert result["estimates"]["maintenance_window"] == 0
    assert result["estimates"]["days_without_any_touch"] == 16
    assert result["estimates"]["days_without_maintenance"] == 30
