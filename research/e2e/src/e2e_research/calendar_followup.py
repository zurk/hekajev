"""Follow-up rates by origin year, with common seasons and complete observation."""

from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

from e2e_research.ai_followup import METRICS as MAINTENANCE_METRICS
from e2e_research.ai_followup import outcomes as maintenance_outcomes
from e2e_research.common import BOOTSTRAPS, SEED, WINDOWS, dt

METRICS = {
    "any_touch_window": "Любая повторная правка за окно",
    "any_touch_7d": "Любая повторная правка за 7 дней",
    "any_touch_14d": "Любая повторная правка за 14 дней",
    "days_without_any_touch": "Дни до повторной правки, с ограничением окном",
    **{key: name for key, name in MAINTENANCE_METRICS.items() if key != "any_touch_window"},
}
COMPARISONS = {
    "2025_2026": ((2025,), (2026,)),
    "2024_2026": ((2024,), (2026,)),
    "2024_2025": ((2024,), (2025,)),
    "2019_2022_vs_2023_2025": ((2019, 2020, 2021, 2022), (2023, 2024, 2025)),
}
COHORTS = {
    "new_file_coverage": "Добавление покрытия в новых файлах",
    "coverage": "Все добавления покрытия",
}
SCOPES = {
    "oss": "Только 19 OSS-проектов",
    "all": "Все 21 проекта",
    "oss_single_file": "OSS: один исходный тестовый файл",
    "oss_complete": "OSS: полные данные исходника",
}


def origin_cutoff(repositories: dict, window: int) -> datetime:
    available = min(dt(repo["observed_until"]) for repo in repositories.values())
    last_partial_day = available - timedelta(days=window)
    # A partial cutoff day otherwise admits a different slice of the final year's cohort.
    return last_partial_day.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        seconds=1
    )


def same_season(row: dict, cutoff: datetime) -> bool:
    when = dt(row["date"])
    return when.year <= cutoff.year and (when.month, when.day) <= (cutoff.month, cutoff.day)


def outcomes(row: dict) -> dict:
    delay = row["first_repeat_days"]
    return {
        **maintenance_outcomes(row),
        "any_touch_7d": float(delay is not None and delay <= 7),
        "any_touch_14d": float(delay is not None and delay <= 14),
        "days_without_any_touch": float(delay if delay is not None else row["window"]),
    }


def describe(rows: list[dict]) -> dict:
    sums = {metric: sum(outcomes(row)[metric] for row in rows) for metric in METRICS}
    return {
        "n": len(rows),
        "projects": len({row["slug"] for row in rows}),
        "sums": sums,
        "estimates": {
            metric: (1 if metric.startswith("days_") else 100) * value / len(rows) if rows else None
            for metric, value in sums.items()
        },
    }


def paired(rows: list[dict], before_years: tuple, after_years: tuple, minimum: int) -> dict:
    projects = defaultdict(lambda: {"before": [], "after": []})
    for row in rows:
        period = (
            "before"
            if row["year"] in before_years
            else "after"
            if row["year"] in after_years
            else None
        )
        if period:
            projects[row["slug"]][period].append(row)
    usable = {
        slug: {key: describe(values) for key, values in periods.items()}
        for slug, periods in sorted(projects.items())
        if min(len(periods["before"]), len(periods["after"])) >= minimum
    }
    n = len(usable)
    result = {
        "n_projects": n,
        "minimum_origins": minimum,
        "n_before": sum(v["before"]["n"] for v in usable.values()),
        "n_after": sum(v["after"]["n"] for v in usable.values()),
        "by_project": usable,
        "metrics": {},
    }
    if not n:
        return result
    draws = np.random.default_rng(SEED).integers(n, size=(BOOTSTRAPS, n))
    for metric in METRICS:
        before = np.array([v["before"]["estimates"][metric] for v in usable.values()])
        after = np.array([v["after"]["estimates"][metric] for v in usable.values()])
        delta = after - before
        scale = 1 if metric.startswith("days_") else 100
        result["metrics"][metric] = {
            "before": float(before.mean()),
            "after": float(after.mean()),
            "delta": float(delta.mean()),
            "unit": "days" if scale == 1 else "percentage_points",
            "bootstrap_95": np.quantile(delta[draws].mean(axis=1), [0.025, 0.975]).tolist()
            if n >= 3
            else None,
            "up": int(sum(delta > 0)),
            "down": int(sum(delta < 0)),
            "pooled_before": scale
            * sum(v["before"]["sums"][metric] for v in usable.values())
            / result["n_before"],
            "pooled_after": scale
            * sum(v["after"]["sums"][metric] for v in usable.values())
            / result["n_after"],
            "leave_one_out": [float((delta.sum() - d) / (n - 1)) for d in delta] if n > 1 else [],
        }
    return result


def summarize_years(rows: list[dict]) -> dict:
    return {
        str(year): describe([r for r in rows if r["year"] == year])
        for year in sorted({r["year"] for r in rows})
    }


def balanced_recent(by_project: dict, minimum: int) -> dict:
    years = (2024, 2025, 2026)
    slugs = [
        slug
        for slug, values in by_project.items()
        if all(values.get(str(y), {}).get("n", 0) >= minimum for y in years)
    ]
    return {
        "projects": slugs,
        "minimum_origins": minimum,
        "annual": {
            str(year): {
                "n": sum(by_project[s][str(year)]["n"] for s in slugs),
                "projects": len(slugs),
                "estimates": {
                    metric: float(
                        np.mean([by_project[s][str(year)]["estimates"][metric] for s in slugs])
                    )
                    if slugs
                    else None
                    for metric in METRICS
                },
            }
            for year in years
        },
    }


def aggregate_calendar_followups(cohorts: list[dict], repositories: dict) -> dict:
    windows = {}
    for window in WINDOWS:
        cutoff = origin_cutoff(repositories, window)
        eligible = [r for r in cohorts if r["window"] == window and "coverage" in r["labels"]]
        selected = [r for r in eligible if same_season(r, cutoff)]
        selections = {}
        for name, file_scope in (("new_file_coverage", "new_test"), ("coverage", "test")):
            base = [r for r in selected if r["scope"] == file_scope]
            scopes = {
                "all": base,
                "oss": [r for r in base if r["source_stratum"] == "public_oss"],
                "oss_single_file": [
                    r for r in base if r["source_stratum"] == "public_oss" and r["files"] == 1
                ],
                "oss_complete": [
                    r for r in base if r["source_stratum"] == "public_oss" and r["complete"]
                ],
            }
            selections[name] = {}
            for scope, rows in scopes.items():
                by_project = {
                    slug: summarize_years([r for r in rows if r["slug"] == slug])
                    for slug in sorted({r["slug"] for r in rows})
                }
                selections[name][scope] = {
                    "annual": summarize_years(rows),
                    "by_project_year": by_project,
                    "balanced_recent": {str(n): balanced_recent(by_project, n) for n in (20, 10)},
                    "comparisons": {
                        key: {str(n): paired(rows, before, after, n) for n in (20, 10)}
                        for key, (before, after) in COMPARISONS.items()
                    },
                }
        windows[str(window)] = {
            "last_origin_in_final_year": cutoff.isoformat(),
            "common_month_day": cutoff.strftime("%m-%d"),
            "cohorts": selections,
        }
    return {
        "metrics": METRICS,
        "cohorts": COHORTS,
        "scopes": SCOPES,
        "windows": windows,
        "method": {
            "primary": (
                "New-file coverage origins, OSS, 30 days, 2025 vs 2026, >=20 "
                "origins per project-period."
            ),
            "origin": (
                "Commit adding at least one test-like file; follow-ups concern added paths only."
            ),
            "time": (
                "Group by the origin year, using the same Jan 1–cutoff dates each year."
            ),
            "cutoff": (
                "Last complete origin day before min repository freeze minus observation window."
            ),
            "event": (
                "Any observed path touch includes modifications, deletions and "
                "renames; labeled maintenance is separate."
            ),
            "denominator": (
                "Count each eligible origin once, even with several files or later edits."
            ),
            "paired": (
                "Same projects, equal weights; pooled counts also supplied. "
                "Threshold 10 is sensitivity."
            ),
            "intervals": (
                "Project bootstrap; seed 42, 10,000 draws. No interval below three projects."
            ),
            "attribution": "This comparison does not use AI markers or estimate an AI effect.",
        },
    }
