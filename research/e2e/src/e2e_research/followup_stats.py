"""Observed maintenance intensity and follow-up counts with explicit denominators."""

from collections import Counter

import numpy as np

from e2e_research.common import BOOTSTRAPS, LABELS, MAINTENANCE, SEED

MEASURES = [
    "matched",
    "maintenance",
    "maintenance_without_coverage",
    "coverage",
    "flakiness",
    "product_adaptation",
    "environment",
    "refactoring",
    "test_bug",
    "unknown_flakiness",
]


def counts(rows, total):
    good = [r for r in rows if r["matched"] is True]
    c = Counter(
        {
            "total": total,
            "candidates": len(rows),
            "matched": len(good),
            "filter_undecided": sum(r["matched"] is None for r in rows),
            "rejected": sum(r["matched"] is False for r in rows),
            "incomplete": sum(not r["complete"] for r in good),
        }
    )
    for row in good:
        c.update(row["labels"])
        if MAINTENANCE & set(row["labels"]):
            c["maintenance"] += 1
            if "coverage" not in row["labels"]:
                c["maintenance_without_coverage"] += 1
        if "flakiness" in row["undecided_labels"]:
            c["unknown_flakiness"] += 1
    for key in MEASURES:
        c.setdefault(key, 0)
    return dict(c)


def rates(c, denominator="total"):
    return {k: 100 * c[k] / c[denominator] if c[denominator] else None for k in MEASURES}


def compare(groups, before, after, slugs, minimum=20, denominator="total"):
    pairs = {}
    for slug in slugs:
        a, b = groups[slug].get(str(before)), groups[slug].get(str(after))
        if (
            not a
            or not b
            or min(a["matched"], b["matched"]) < minimum
            or min(a["total"], b["total"]) < 100
        ):
            continue
        pairs[slug] = {"before": a, "after": b}
    if not pairs:
        return {"n_projects": 0, "by_project": {}, "metrics": {}}
    result = {"n_projects": len(pairs), "by_project": pairs, "metrics": {}}
    draws = np.random.default_rng(SEED).integers(len(pairs), size=(BOOTSTRAPS, len(pairs)))
    for metric in MEASURES:
        a = np.array([100 * v["before"][metric] / v["before"][denominator] for v in pairs.values()])
        b = np.array([100 * v["after"][metric] / v["after"][denominator] for v in pairs.values()])
        delta = b - a
        leave = [(delta.sum() - d) / (len(delta) - 1) for d in delta] if len(delta) > 1 else []
        result["metrics"][metric] = {
            "before": float(a.mean()),
            "after": float(b.mean()),
            "delta": float(delta.mean()),
            "bootstrap_95": np.quantile(delta[draws].mean(axis=1), [0.025, 0.975]).tolist(),
            "up": int(sum(delta > 0)),
            "down": int(sum(delta < 0)),
            "flat": int(sum(delta == 0)),
            "leave_one_out_range": [min(leave), max(leave)] if leave else None,
            "pooled_before": 100
            * sum(v["before"][metric] for v in pairs.values())
            / sum(v["before"][denominator] for v in pairs.values()),
            "pooled_after": 100
            * sum(v["after"][metric] for v in pairs.values())
            / sum(v["after"][denominator] for v in pairs.values()),
        }
    return result


def summarize_cohort(rows):
    n = len(rows)
    keys = (
        "repeat_any",
        "repeat_e2e",
        "repeat_maintenance",
        "repeat_flakiness",
        "unknown_followup",
        "terminal_touch",
    )
    c = {key: sum(r[key] for r in rows) for key in keys}
    delays = [r["first_maintenance_days"] for r in rows if r["repeat_maintenance"]]
    return {
        "n": n,
        "projects": len({r["slug"] for r in rows}),
        "counts": c,
        "rates": {key: 100 * v / n if n else None for key, v in c.items()},
        "label_counts": {key: sum(key in r["repeat_labels"] for r in rows) for key in LABELS},
        "median_first_maintenance_days": float(np.median(delays)) if delays else None,
    }


def chains_summary(rows):
    out = {}
    for scope in ("test", "code", "new_test"):
        out[scope] = {}
        for horizon in (14, 30, 90):
            base = [r for r in rows if r["scope"] == scope and r["window"] == horizon]
            groups = {
                "all": base,
                "oss": [r for r in base if r["source_stratum"] == "public_oss"],
                "single_selected_file": [r for r in base if r["all_selected_files"] == 1],
                "single_test_file": [r for r in base if r["files"] == 1],
                "both_single_selected_file": [
                    dict(
                        r,
                        repeat_labels=r["strict_repeat_labels"],
                        repeat_maintenance=bool(MAINTENANCE & set(r["strict_repeat_labels"])),
                        repeat_flakiness="flakiness" in r["strict_repeat_labels"],
                        first_maintenance_days=r["strict_first_maintenance_days"],
                    )
                    for r in base
                    if r["all_selected_files"] == 1
                ],
                "oss_both_single_selected_file": [
                    dict(
                        r,
                        repeat_labels=r["strict_repeat_labels"],
                        repeat_maintenance=bool(MAINTENANCE & set(r["strict_repeat_labels"])),
                        repeat_flakiness="flakiness" in r["strict_repeat_labels"],
                        first_maintenance_days=r["strict_first_maintenance_days"],
                    )
                    for r in base
                    if r["all_selected_files"] == 1 and r["source_stratum"] == "public_oss"
                ],
                "complete": [r for r in base if r["complete"]],
                "recent_2024_2026": [r for r in base if r["year"] >= 2024],
            }
            out[scope][str(horizon)] = {}
            for group, values in groups.items():
                cohorts = {
                    "all": values,
                    **{label: [r for r in values if label in r["labels"]] for label in LABELS},
                    "coverage_only": [r for r in values if r["labels"] == ["coverage"]],
                    "new_file_coverage": [
                        r for r in values if "coverage" in r["labels"] and r["added_files"] > 0
                    ],
                }
                out[scope][str(horizon)][group] = {
                    label: summarize_cohort(v) for label, v in cohorts.items()
                }
                if group in ("all", "oss"):
                    out[scope][str(horizon)][group]["by_project"] = {
                        slug: {
                            label: summarize_cohort([r for r in subset if r["slug"] == slug])
                            for label, subset in cohorts.items()
                        }
                        for slug in sorted({r["slug"] for r in values})
                    }
    return out
