"""Descriptive comparisons with explicit AI attribution; no inference calls."""

from collections import Counter, defaultdict

import numpy as np

from e2e_research.common import BOOTSTRAPS, LABELS, NAMES, SEED

MIN_PROJECT = 20

METRICS = {
    **{k: {"name": n, "group": "reasons"} for k, n in zip(LABELS, NAMES, strict=True)},
    **{
        f"unknown_{k}": {"name": f"Неопределённо: {n.lower()}", "group": "uncertainty"}
        for k, n in zip(LABELS, NAMES, strict=True)
    },
    "no_confident_reason": {"name": "Ни одной уверенной причины", "group": "classification"},
    "multi_reason": {"name": "Несколько уверенных причин", "group": "classification"},
    "incomplete": {"name": "Неполные данные для Jev", "group": "classification"},
    "single_file": {"name": "Изменён один файл", "group": "size"},
    "large_commit": {"name": "Изменено ≥10 файлов", "group": "size"},
}

METHODS = {
    "project": {"name": "Один проект", "fields": [], "minimum": 5},
    "quarter": {"name": "Один проект + квартал", "fields": ["quarter"], "minimum": 5},
    "month": {"name": "Один проект + месяц", "fields": ["month"], "minimum": 3},
    "quarter_size": {
        "name": "Проект + квартал + размер",
        "fields": ["quarter", "size_bin"],
        "minimum": 3,
    },
    "author_halfyear": {
        "name": "Проект + полугодие + Git-автор",
        "fields": ["halfyear", "git_author_id"],
        "minimum": 3,
    },
}


def outcome(row, metric):
    if metric in LABELS:
        return float(metric in row["labels"])
    if metric.startswith("unknown_"):
        return float(metric[8:] in row["undecided_labels"])
    return float(
        {
            "no_confident_reason": not row["labels"],
            "multi_reason": len(row["labels"]) >= 2,
            "incomplete": not row["complete"],
            "single_file": row["changed_files"] == 1,
            "large_commit": row["changed_files"] >= 10,
        }[metric]
    )


def describe(rows):
    if not rows:
        return {"n": 0, "rates": {}, "counts": {}}
    counts = {m: sum(outcome(r, m) for r in rows) for m in METRICS}
    return {
        "n": len(rows),
        "projects": len({r["slug"] for r in rows}),
        "counts": counts,
        "rates": {m: 100 * v / len(rows) for m, v in counts.items()},
        "files_median": float(np.median([r["changed_files"] for r in rows])),
        "files_p90": float(np.quantile([r["changed_files"] for r in rows], 0.9)),
        "message_chars_median": float(
            np.median([r["message_chars_without_attribution"] for r in rows])
        ),
        "chunks_median": float(np.median([r["chunks"] for r in rows])),
    }


def group_comparison(rows):
    return {
        "ai": describe([r for r in rows if r["ai_attributed"]]),
        "unmarked": describe([r for r in rows if not r["ai_attributed"]]),
    }


def standardized(rows, fields, minimum, min_project=MIN_PROJECT, include_reference=False):
    """Weight shared cells by attributed counts within each project; then projects equally."""
    cells = defaultdict(lambda: {False: [], True: []})
    for row in rows:
        key = (row["slug"], *(row[field] for field in fields))
        cells[key][row["ai_attributed"]].append(row)
    usable = defaultdict(list)
    for key, groups in cells.items():
        if min(len(groups[False]), len(groups[True])) >= minimum:
            usable[key[0]].append((key, groups))
    projects = {}
    identities = set()
    for slug, members in sorted(usable.items()):
        sizes = {flag: sum(len(groups[flag]) for _, groups in members) for flag in (False, True)}
        if min(sizes.values()) < min_project:
            continue
        rates = {"ai": {}, "unmarked": {}}
        for metric in METRICS:
            for flag, group_name in [(False, "unmarked"), (True, "ai")]:
                rates[group_name][metric] = 100 * sum(
                    len(groups[True])
                    / sizes[True]
                    * np.mean([outcome(r, metric) for r in groups[flag]])
                    for _, groups in members
                )
        details = []
        for key, groups in members:
            details.append(
                {
                    "stratum": list(key[1:]),
                    "n_ai": len(groups[True]),
                    "n_unmarked": len(groups[False]),
                    "weight": len(groups[True]) / sizes[True],
                }
            )
            identities.update((r["slug"], r["sha"]) for flag in (False, True) for r in groups[flag])
        projects[slug] = {
            "n_ai": sizes[True],
            "n_unmarked": sizes[False],
            "n_cells": len(members),
            "rates": rates,
            "strata": details,
        }
    n = len(projects)
    result = {
        "n_projects": n,
        "n_cells": sum(v["n_cells"] for v in projects.values()),
        "n_ai": sum(v["n_ai"] for v in projects.values()),
        "n_unmarked": sum(v["n_unmarked"] for v in projects.values()),
        "by_project": projects,
        "metrics": {},
        "support_pooled": group_comparison(
            [r for r in rows if (r["slug"], r["sha"]) in identities]
        ),
        "total_ai": sum(r["ai_attributed"] for r in rows),
        "total_unmarked": sum(not r["ai_attributed"] for r in rows),
    }
    if not projects:
        return result
    if include_reference and fields:
        result["same_rows_project_only"] = standardized(
            [r for r in rows if (r["slug"], r["sha"]) in identities],
            [],
            1,
            min_project=min_project,
        )
    rng = np.random.default_rng(SEED)
    draws = rng.integers(n, size=(BOOTSTRAPS, n))
    for metric in METRICS:
        ai = np.array([p["rates"]["ai"][metric] for p in projects.values()])
        control = np.array([p["rates"]["unmarked"][metric] for p in projects.values()])
        delta = ai - control
        loo = (delta.sum() - delta) / (n - 1) if n > 1 else np.array([delta[0]])
        result["metrics"][metric] = {
            "ai": float(ai.mean()),
            "unmarked": float(control.mean()),
            "delta_pp": float(delta.mean()),
            "median_project_delta_pp": float(np.median(delta)),
            "bootstrap_95": [
                float(v) for v in np.quantile(delta[draws].mean(axis=1), [0.025, 0.975])
            ],
            "positive_projects": int(sum(delta > 1e-10)),
            "negative_projects": int(sum(delta < -1e-10)),
            "leave_one_out_range": [float(loo.min()), float(loo.max())],
        }
    return result


def aggregate_ai_reasons(rows):
    matched = [r for r in rows if r["matched"] is True]
    scenarios = {}
    for key, method in METHODS.items():
        scenarios[key] = {}
        for sensitivity, subset in {
            "all": matched,
            "oss": [r for r in matched if r["source_stratum"] == "public_oss"],
            "complete": [r for r in matched if r["complete"]],
            "2026": [r for r in matched if r["year"] == 2026],
        }.items():
            scenarios[key][sensitivity] = standardized(
                subset,
                method["fields"],
                method["minimum"],
                include_reference=True,
            )
    by_quarter = {}
    for quarter in sorted({r["quarter"] for r in rows}):
        current = [r for r in rows if r["quarter"] == quarter]
        by_quarter[quarter] = {
            "candidates": len(current),
            "ai": sum(r["ai_attributed"] for r in current),
            "matched": sum(r["matched"] is True for r in current),
        }
    by_repo = {}
    for slug in sorted({r["slug"] for r in rows}):
        current = [r for r in rows if r["slug"] == slug]
        by_repo[slug] = {
            "candidates": len(current),
            "ai_candidates": sum(r["ai_attributed"] for r in current),
            "outcomes": group_comparison([r for r in current if r["matched"] is True]),
        }
    tools = {}
    for tool in sorted({t for r in rows for t in r["ai_tools"]}):
        current = [r for r in rows if tool in r["ai_tools"]]
        tools[tool] = {
            "candidates": len(current),
            "matched": sum(r["matched"] is True for r in current),
            "projects": dict(Counter(r["slug"] for r in current)),
        }
    return {
        "period": "2025-01-01 through frozen 2026-09-21 census",
        "metrics": METRICS,
        "methods": METHODS,
        "candidates": len(rows),
        "ai_candidates": sum(r["ai_attributed"] for r in rows),
        "matched": len(matched),
        "raw": group_comparison(matched),
        "scenarios": scenarios,
        "raw_by_scope": {
            "all": group_comparison(matched),
            "oss": group_comparison([r for r in matched if r["source_stratum"] == "public_oss"]),
            "complete": group_comparison([r for r in matched if r["complete"]]),
            "2026": group_comparison([r for r in matched if r["year"] == 2026]),
        },
        "by_quarter": by_quarter,
        "by_repository": by_repo,
        "tools": tools,
        "filter": {
            name: {
                "n": len(group),
                "matched": sum(r["matched"] is True for r in group),
                "undecided": sum(r["matched"] is None for r in group),
                "rejected": sum(r["matched"] is False for r in group),
            }
            for name, group in [
                ("ai", [r for r in rows if r["ai_attributed"]]),
                ("unmarked", [r for r in rows if not r["ai_attributed"]]),
            ]
        },
        "interpretation": [
            "These comparisons use explicit AI markers; they do not measure AI's effect.",
            (
                "Reasons and ambiguity use E2E-positive commits; filter outcomes "
                "use all path candidates."
            ),
            (
                "Within a project, shared strata are weighted by AI-attributed "
                "counts; projects receive equal weight."
            ),
            (
                "An unmarked commit may still use AI. Git author names the recorded "
                "author; it does not verify who wrote the code."
            ),
            (
                "Changed-file counts include the whole commit. They measure neither "
                "test quality nor engineering time or defect rate."
            ),
            (
                "Commit size may affect the outcome and differ between groups. "
                "Adjusting for size tests sensitivity; it does not estimate a total effect."
            ),
            (
                "Bootstrap resamples projects, seed 42, 10,000 draws. Exploratory "
                "intervals, no multiple-testing correction."
            ),
        ],
    }
