"""Calendar, project-age and reason frequencies from saved classifications."""

from collections import Counter, defaultdict

import numpy as np

from e2e_research.attribution import AI_PATTERN
from e2e_research.common import BOOTSTRAPS, CUTOFF, LABELS, NAMES, SEED, anniversary, dt

MIN_N = 20


def summarize(rows):
    matched = [r for r in rows if r["matched"] is True]
    n = len(matched)
    positive = Counter(label for r in matched for label in r["labels"])
    unknown = Counter(label for r in matched for label in r["undecided_labels"])
    return {
        "candidates": len(rows),
        "matched": n,
        "rejected": sum(r["matched"] is False for r in rows),
        "filter_undecided": sum(r["matched"] is None for r in rows),
        "incomplete": sum(not r["complete"] for r in rows),
        "matched_incomplete": sum(not r["complete"] for r in matched),
        "projects": len({r["slug"] for r in matched}),
        "counts": {label: positive[label] for label in LABELS},
        "undecided_counts": {label: unknown[label] for label in LABELS},
        "rates": {label: 100 * positive[label] / n if n else None for label in LABELS},
        "undecided_rates": {label: 100 * unknown[label] / n if n else None for label in LABELS},
        "ai_marked": sum(bool(r["ai_markers"]) for r in rows),
    }


def grouped(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return {str(k): summarize(v) for k, v in sorted(groups.items())}


def is_ytd(row):
    when = dt(row["date"])
    return (when.month, when.day) <= (CUTOFF.month, CUTOFF.day)


def eligible_pair(rows, before, after, eligible=None, minimum=MIN_N):
    by_repo = defaultdict(list)
    for row in rows:
        by_repo[row["slug"]].append(row)
    pairs = {}
    for slug, values in by_repo.items():
        if eligible is not None and slug not in eligible:
            continue
        a = summarize([r for r in values if before(r)])
        b = summarize([r for r in values if after(r)])
        if min(a["matched"], b["matched"]) >= minimum:
            pairs[slug] = {"before": a, "after": b}
    return pairs


def pair_stats(pairs):
    if not pairs:
        return {"n_projects": 0, "by_project": {}, "labels": {}}
    rng = np.random.default_rng(SEED)
    result = {"n_projects": len(pairs), "by_project": pairs, "labels": {}}
    resample = rng.integers(len(pairs), size=(BOOTSTRAPS, len(pairs)))
    for label in LABELS:
        early = np.array([v["before"]["rates"][label] for v in pairs.values()])
        late = np.array([v["after"]["rates"][label] for v in pairs.values()])
        differences = late - early
        boot = differences[resample].mean(axis=1)
        leave_out = (
            [(differences.sum() - v) / (len(differences) - 1) for v in differences]
            if len(differences) > 1
            else []
        )
        result["labels"][label] = {
            "mean_before": float(early.mean()),
            "mean_after": float(late.mean()),
            "mean_delta_pp": float(differences.mean()),
            "median_delta_pp": float(np.median(differences)),
            "bootstrap_95": [float(v) for v in np.quantile(boot, [0.025, 0.975])],
            "up": int(sum(differences > 0)),
            "down": int(sum(differences < 0)),
            "unchanged": int(sum(differences == 0)),
            "leave_one_out_range": [min(leave_out), max(leave_out)] if leave_out else None,
            "mean_undecided_delta_pp": float(
                np.mean(
                    [
                        v["after"]["undecided_rates"][label] - v["before"]["undecided_rates"][label]
                        for v in pairs.values()
                    ]
                )
            ),
        }
    return result


def make_comparisons(rows, repos):
    def cut(r):
        return r["year"] <= CUTOFF.year and is_ytd(r)

    definitions = {
        "calendar_2024_2025": (lambda r: r["year"] == 2024, lambda r: r["year"] == 2025, None),
        "ytd_2025_2026": (
            lambda r: r["year"] == 2025 and cut(r),
            lambda r: r["year"] == 2026 and cut(r),
            None,
        ),
        "period_2019_2022_vs_2023_2025": (
            lambda r: 2019 <= r["year"] <= 2022,
            lambda r: 2023 <= r["year"] <= 2025,
            None,
        ),
        "age_1_3_vs_4_6": (
            lambda r: 1 <= r["age_year"] <= 3,
            lambda r: 4 <= r["age_year"] <= 6,
            {
                slug
                for slug, repo in repos.items()
                if anniversary(
                    dt(repo["date_span_utc"]["committer"]["earliest"]),
                    dt(repo["date_span_utc"]["committer"]["earliest"]).year + 6,
                )
                <= CUTOFF
            },
        ),
        "age_1_2_vs_3_4": (
            lambda r: 1 <= r["age_year"] <= 2,
            lambda r: 3 <= r["age_year"] <= 4,
            {
                slug
                for slug, repo in repos.items()
                if anniversary(
                    dt(repo["date_span_utc"]["committer"]["earliest"]),
                    dt(repo["date_span_utc"]["committer"]["earliest"]).year + 4,
                )
                <= CUTOFF
            },
        ),
        "e2e_age_1_2_vs_3_4": (
            lambda r: 1 <= r["e2e_age_year"] <= 2,
            lambda r: 3 <= r["e2e_age_year"] <= 4,
            {
                slug
                for slug in repos
                if anniversary(
                    min(dt(r["date"]) for r in rows if r["slug"] == slug),
                    min(dt(r["date"]) for r in rows if r["slug"] == slug).year + 4,
                )
                <= CUTOFF
            },
        ),
        "ai_marked_vs_unmarked_2026": (
            lambda r: r["year"] == 2026 and not r["ai_markers"],
            lambda r: r["year"] == 2026 and bool(r["ai_markers"]),
            None,
        ),
    }
    results = {}
    for key, (before, after, eligible) in definitions.items():
        full_pairs = eligible_pair(rows, before, after, eligible)
        names = set(full_pairs)
        results[key] = {
            "main": pair_stats(full_pairs),
            "oss_only": pair_stats(
                eligible_pair(
                    [r for r in rows if r["source_stratum"] == "public_oss"], before, after, names
                )
            ),
            "complete_only": pair_stats(
                eligible_pair([r for r in rows if r["complete"]], before, after, names)
            ),
            "minimum_50": pair_stats(eligible_pair(rows, before, after, eligible, minimum=50)),
            "without_ai_markers": pair_stats(
                eligible_pair([r for r in rows if not r["ai_markers"]], before, after, names)
            ),
            "pooled_before": summarize([r for r in rows if before(r)]),
            "pooled_after": summarize([r for r in rows if after(r)]),
        }
    return results


def aggregate_temporal(rows, repos):
    ai_evidence_source = (
        "Full Git messages for 2025–2026; saved fragments earlier, "
        "matching the frozen analysis contract"
    )
    origins = {}
    for slug, repo in repos.items():
        selected = [r for r in rows if r["slug"] == slug]
        first = min(r["date"] for r in selected)
        origins[slug] = {
            "birth": repo["date_span_utc"]["committer"]["earliest"],
            "first_candidate": first,
            "first_candidate_age_year": min(r["age_year"] for r in selected),
            "source_stratum": repo["source_stratum"],
            "shallow": repo["shallow"],
            "frozen_sha": repo["frozen_sha"],
            "totals": summarize(selected),
        }
    axes = {}
    for axis in ("year", "age_year", "e2e_age_year"):
        axes[axis] = {
            "all": grouped(rows, lambda r, axis=axis: r[axis]),
            "oss": grouped(
                [r for r in rows if r["source_stratum"] == "public_oss"],
                lambda r, axis=axis: r[axis],
            ),
            "complete": grouped([r for r in rows if r["complete"]], lambda r, axis=axis: r[axis]),
            "by_project": {
                slug: grouped([r for r in rows if r["slug"] == slug], lambda r, axis=axis: r[axis])
                for slug in repos
            },
        }
    ytd = [r for r in rows if is_ytd(r)]
    axes["year_ytd"] = {
        "all": grouped(ytd, lambda r: r["year"]),
        "oss": grouped(
            [r for r in ytd if r["source_stratum"] == "public_oss"], lambda r: r["year"]
        ),
        "complete": grouped([r for r in ytd if r["complete"]], lambda r: r["year"]),
        "by_project": {
            slug: grouped([r for r in ytd if r["slug"] == slug], lambda r: r["year"])
            for slug in repos
        },
    }
    comparisons = make_comparisons(rows, repos)
    ai_rows = [r for r in rows if r["ai_markers"]]
    return {
        "scope": (
            "Existing 20,026 classified E2E-path candidates in 21 repositories; no new inference."
        ),
        "labels": dict(zip(LABELS, NAMES, strict=True)),
        "cutoff": CUTOFF.isoformat(),
        "total": summarize(rows),
        "origins": origins,
        "axes": axes,
        "calendar_ytd": grouped([r for r in rows if is_ytd(r)], lambda r: r["year"]),
        "comparisons": comparisons,
        "ai": {
            "pattern": AI_PATTERN.pattern,
            "marked_count": len(ai_rows),
            "evidence_source": ai_evidence_source,
            "by_year": grouped(ai_rows, lambda r: r["year"]),
            "by_project": grouped(ai_rows, lambda r: r["slug"]),
            "examples": ai_rows,
        },
        "methods": {
            "calendar_date": "UTC committer date; 2026 is incomplete through 21 September",
            "project_age": (
                "Anniversary years from earliest reachable committer date in "
                "frozen Git history; first year = 1"
            ),
            "e2e_age": (
                "Anniversary years since the first path-selected candidate"
            ),
            "denominator": (
                "E2E matched commits, including incomplete ones; uncertainty retained separately"
            ),
            "paired": (
                "Same repositories with >=20 matched commits in both windows; equal project weights"
            ),
            "intervals": (
                "10,000 project bootstrap draws, seed 42; descriptive stability, "
                "not population or causal confidence"
            ),
            "age_pair": (
                "Years 1–3 vs 4–6; repository must have reached sixth anniversary before cutoff"
            ),
            "age_period_cohort": (
                "Calendar time, project age and birth cohort are linked; these "
                "slices do not identify independent causal effects"
            ),
            "ai": (
                "AI attribution needs a marker in the full message; a title mention "
                "alone does not count. Unmarked commits may still use AI."
            ),
        },
    }
