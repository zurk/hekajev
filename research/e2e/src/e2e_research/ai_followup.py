"""Compare follow-up frequency and timing under equal observation windows."""

from collections import defaultdict

import numpy as np

from e2e_research.common import BOOTSTRAPS, MAINTENANCE, SEED

METHODS = {
    "project": ([], 5),
    "quarter": (["quarter"], 5),
    "month": (["month"], 3),
    "quarter_size": (["quarter", "file_count_bin"], 5),
    "quarter_activity": (["quarter", "prior_activity_bin"], 5),
    "quarter_activity_size": (["quarter", "prior_activity_bin", "file_count_bin"], 5),
    "author_halfyear": (["halfyear", "git_author_id"], 5),
}
METRICS = {
    "maintenance_7d": "Сопровождение за 7 дней",
    "maintenance_14d": "Сопровождение за 14 дней",
    "maintenance_window": "Сопровождение за всё окно",
    "flakiness_window": "Flakiness-правка за всё окно",
    "any_touch_window": "Любая повторная правка файла",
    "days_without_maintenance": "Дни до сопровождения, с ограничением окном",
}


def outcomes(row: dict) -> dict:
    delay = row["first_maintenance_days"]
    return {
        "maintenance_7d": float(delay is not None and delay <= 7),
        "maintenance_14d": float(delay is not None and delay <= 14),
        "maintenance_window": float(row["repeat_maintenance"]),
        "flakiness_window": float(row["repeat_flakiness"]),
        "any_touch_window": float(row["repeat_any"]),
        "days_without_maintenance": float(delay if delay is not None else row["window"]),
    }


def describe(rows: list[dict]) -> dict:
    n = len(rows)
    sums = {m: sum(outcomes(r)[m] for r in rows) for m in METRICS}
    delays = [r["first_maintenance_days"] for r in rows if r["repeat_maintenance"]]
    return {
        "n": n,
        "projects": len({r["slug"] for r in rows}),
        "sums": sums,
        "estimates": {
            m: (1 if m.startswith("days_") else 100) * s / n if n else None for m, s in sums.items()
        },
        "conditional_median_days": float(np.median(delays)) if delays else None,
        "conditional_events": len(delays),
    }


def raw_groups(rows: list[dict]) -> dict:
    return {
        name: describe([r for r in rows if r["ai_attributed"] == flag])
        for flag, name in ((True, "ai"), (False, "unmarked"))
    }


def standardized(rows: list[dict], fields: list[str], minimum: int, min_project: int = 20) -> dict:
    cells = defaultdict(lambda: {True: [], False: []})
    for row in rows:
        cells[(row["slug"], *(row[f] for f in fields))][row["ai_attributed"]].append(row)
    usable = defaultdict(list)
    for key, groups in sorted(cells.items()):
        if min(len(groups[True]), len(groups[False])) >= minimum:
            usable[key[0]].append((key, groups))
    projects, included = {}, set()
    for slug, members in sorted(usable.items()):
        sizes = {flag: sum(len(g[flag]) for _, g in members) for flag in (True, False)}
        if min(sizes.values()) < min_project:
            continue
        estimates = {"ai": {}, "unmarked": {}}
        strata = []
        for key, groups in members:
            strata.append(
                {
                    "key": list(key[1:]),
                    "n_ai": len(groups[True]),
                    "n_unmarked": len(groups[False]),
                    "weight": len(groups[True]) / sizes[True],
                }
            )
            included.update((r["slug"], r["sha"]) for flag in (True, False) for r in groups[flag])
        for flag, name in ((True, "ai"), (False, "unmarked")):
            for metric in METRICS:
                multiplier = 1 if metric.startswith("days_") else 100
                estimates[name][metric] = multiplier * sum(
                    len(g[True]) / sizes[True] * np.mean([outcomes(r)[metric] for r in g[flag]])
                    for _, g in members
                )
        projects[slug] = {
            "n_ai": sizes[True],
            "n_unmarked": sizes[False],
            "estimates": estimates,
            "strata": strata,
        }
    n = len(projects)
    result = {
        "n_projects": n,
        "n_ai": sum(v["n_ai"] for v in projects.values()),
        "n_unmarked": sum(v["n_unmarked"] for v in projects.values()),
        "total_ai": sum(r["ai_attributed"] for r in rows),
        "total_unmarked": sum(not r["ai_attributed"] for r in rows),
        "by_project": projects,
        "metrics": {},
        "same_rows_raw": raw_groups([r for r in rows if (r["slug"], r["sha"]) in included]),
    }
    if not projects:
        return result
    draws = np.random.default_rng(SEED).integers(n, size=(BOOTSTRAPS, n))
    for metric in METRICS:
        a = np.array([v["estimates"]["ai"][metric] for v in projects.values()])
        b = np.array([v["estimates"]["unmarked"][metric] for v in projects.values()])
        delta = a - b
        result["metrics"][metric] = {
            "ai": float(a.mean()),
            "unmarked": float(b.mean()),
            "delta": float(delta.mean()),
            "unit": "days" if metric.startswith("days_") else "percentage_points",
            "bootstrap_95": np.quantile(delta[draws].mean(axis=1), [0.025, 0.975]).tolist()
            if n >= 3
            else None,
            "up": int(sum(delta > 0)),
            "down": int(sum(delta < 0)),
            "leave_one_out": [(float(delta.sum()) - float(v)) / (n - 1) for v in delta]
            if n > 1
            else [],
        }
    return result


def aggregate_ai_followups(cohorts: list[dict], classified: list[dict]) -> dict:
    metadata = {(r["slug"], r["sha"]): r for r in classified if r["year"] >= 2025}
    rows = []
    for row in cohorts:
        meta = metadata.get((row["slug"], row["sha"]))
        if meta is None:
            continue
        row = dict(row)
        for field in ("ai_attributed", "month", "halfyear", "git_author_id"):
            row[field] = meta[field]
        n = row["prior_30d_commits"]
        row["prior_activity_bin"] = (
            "0" if n == 0 else "1-2" if n <= 2 else "3-9" if n <= 9 else "10+"
        )
        row["file_count_bin"] = "1" if row["files"] == 1 else "2-5" if row["files"] <= 5 else "6+"
        rows.append(row)
    result = {}
    definitions = {
        "new_file_coverage": lambda r: r["scope"] == "new_test" and "coverage" in r["labels"],
        "coverage": lambda r: r["scope"] == "test" and "coverage" in r["labels"],
        "flakiness": lambda r: r["scope"] == "test" and "flakiness" in r["labels"],
        "all_e2e": lambda r: r["scope"] == "test",
    }
    for cohort, predicate in definitions.items():
        result[cohort] = {}
        for window in (14, 30, 90):
            base = [r for r in rows if predicate(r) and r["window"] == window]
            scopes = {
                "all": base,
                "oss": [r for r in base if r["source_stratum"] == "public_oss"],
                "complete": [r for r in base if r["complete"]],
                "strict_pair": [
                    dict(
                        r,
                        repeat_maintenance=bool(MAINTENANCE & set(r["strict_repeat_labels"])),
                        repeat_flakiness="flakiness" in r["strict_repeat_labels"],
                        first_maintenance_days=r["strict_first_maintenance_days"],
                    )
                    for r in base
                    if r["all_selected_files"] == 1
                ],
            }
            result[cohort][str(window)] = {
                scope: {
                    "raw": raw_groups(values),
                    "methods": {
                        method: standardized(values, fields, minimum)
                        for method, (fields, minimum) in METHODS.items()
                    },
                }
                for scope, values in scopes.items()
            }
    return {
        "cohorts": result,
        "metrics": METRICS,
        "method": {
            "period": "2025–2026 origins with a complete fixed observation window",
            "primary": "new_file_coverage / 30 days / all / project + quarter",
            "contrast": (
                "AI requires a marker in the origin message. Unmarked commits may still use AI."
            ),
            "weighting": (
                "Shared cells weighted by AI-origin counts within project; "
                "projects weighted equally."
            ),
            "support": (
                "At least 5 per group/cell (3 monthly), 20 per group/project; "
                "intervals only with >=3 projects."
            ),
            "time": (
                "First labeled maintenance delay, capped at window; origins with "
                "no event contribute the full window."
            ),
            "caution": (
                "The time measure combines how often and how soon files change. "
                "The median among returned origins is descriptive only."
            ),
            "uncertainty": (
                "Project bootstrap, 10,000 draws, seed 42. Exploratory "
                "associations, not causal effects."
            ),
        },
    }
