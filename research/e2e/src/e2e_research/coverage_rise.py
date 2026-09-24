"""Same-season coverage comparison used by the editorial E2E report."""

from statistics import mean

from e2e_research.common import CUTOFF

YEARS = ("2025", "2026")
MEASURES = ("matched", "coverage", "maintenance")


def counts_by_year(ytd: dict, slugs: list[str]) -> dict:
    return {
        year: {
            measure: sum(ytd[slug].get(year, {}).get(measure, 0) for slug in slugs)
            for measure in MEASURES
        }
        for year in YEARS
    }


def comparison(ytd: dict, slugs: list[str]) -> dict:
    counts = counts_by_year(ytd, slugs)
    for year in YEARS:
        matched = counts[year]["matched"]
        if not matched:
            raise ValueError(f"No E2E-positive commits for {year} in the selected projects")
        for measure in MEASURES[1:]:
            counts[year][f"{measure}_share"] = 100 * counts[year][measure] / matched
    return {
        "years": counts,
        "coverage_delta_pp": counts["2026"]["coverage_share"]
        - counts["2025"]["coverage_share"],
        "maintenance_delta_pp": counts["2026"]["maintenance_share"]
        - counts["2025"]["maintenance_share"],
    }


def direction(ytd: dict, slug: str, measure: str) -> int:
    before, after = (ytd[slug][year] for year in YEARS)
    difference = (
        after[measure] * before["matched"] - before[measure] * after["matched"]
    )
    return (difference > 0) - (difference < 0)


def directions(ytd: dict, slugs: list[str], measure: str) -> dict:
    changes = [direction(ytd, slug, measure) for slug in slugs]
    return {
        "up": changes.count(1),
        "down": changes.count(-1),
        "unchanged": changes.count(0),
    }


def equal_project_share(ytd: dict, slugs: list[str], measure: str) -> dict:
    if not slugs:
        return dict.fromkeys(YEARS)
    return {
        year: mean(100 * ytd[slug][year][measure] / ytd[slug][year]["matched"] for slug in slugs)
        for year in YEARS
    }


def coverage_rise(file_chains: dict) -> dict:
    """Compare identical calendar windows without treating projects as independent commits."""
    ytd = file_chains["ytd"]
    slugs = sorted(ytd)
    window = f"January 1–{CUTOFF:%B} {CUTOFF.day} in each year"
    if len(slugs) < 3 or any(not counts_by_year(ytd, slugs)[year]["matched"] for year in YEARS):
        return {"available": False, "window": window, "projects": len(slugs)}
    pooled = comparison(ytd, slugs)
    leave_one_out = {
        slug: comparison(ytd, [other for other in slugs if other != slug])["coverage_delta_pp"]
        for slug in slugs
    }
    top_two = sorted(
        slugs,
        key=lambda slug: (
            ytd[slug].get("2026", {}).get("coverage", 0)
            - ytd[slug].get("2025", {}).get("coverage", 0),
            slug,
        ),
        reverse=True,
    )[:2]
    without_top_two = comparison(ytd, [slug for slug in slugs if slug not in top_two])
    paired = [
        slug for slug in slugs if all(ytd[slug].get(year, {}).get("matched", 0) for year in YEARS)
    ]
    sampled = [
        slug for slug in paired if all(ytd[slug][year]["matched"] >= 20 for year in YEARS)
    ]
    return {
        "available": True,
        "window": window,
        "projects": len(slugs),
        "pooled": pooled,
        "leave_one_project_out": {
            "minimum_delta_pp": min(leave_one_out.values()),
            "maximum_delta_pp": max(leave_one_out.values()),
            "by_project": leave_one_out,
        },
        "without_two_largest_coverage_count_contributors": {
            "excluded": top_two,
            **without_top_two,
        },
        "paired_projects": {
            "count": len(paired),
            "coverage_directions": directions(ytd, paired, "coverage"),
        },
        "projects_with_at_least_20_e2e_commits_per_year": {
            "count": len(sampled),
            "coverage_directions": directions(ytd, sampled, "coverage"),
            "maintenance_directions": directions(ytd, sampled, "maintenance"),
            "equal_project_coverage_share": equal_project_share(ytd, sampled, "coverage"),
        },
    }
