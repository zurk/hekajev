"""Regression check against saved report aggregates, separate from semantic accuracy."""

import math
from pathlib import Path

from e2e_research.common import digest, load_json, save_json

ORDER_DEPENDENT_INTERVALS = {
    "bootstrap_95",
    "leave_one_out",
    "leave_one_out_range",
    "leave_one_project_out_range",
}


def compare_numbers(expected, actual, path=()):
    matched, skipped, failures = 0, 0, []
    if isinstance(expected, dict):
        for key, value in expected.items():
            if key in ORDER_DEPENDENT_INTERVALS or key == "examples":
                skipped += 1
                continue
            if key not in actual:
                failures.append({"path": [*path, key], "reason": "missing field"})
                continue
            n, s, errors = compare_numbers(value, actual[key], (*path, key))
            matched += n
            skipped += s
            failures.extend(errors)
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            failures.append({"path": list(path), "reason": "list length differs"})
        elif all(isinstance(v, int | float) and not isinstance(v, bool) for v in expected):
            for i, value in enumerate(expected):
                n, s, errors = compare_numbers(value, actual[i], (*path, i))
                matched += n
                skipped += s
                failures.extend(errors)
    elif isinstance(expected, int | float) and not isinstance(expected, bool):
        if not isinstance(actual, int | float) or not math.isclose(
            expected, actual, rel_tol=1e-12, abs_tol=1e-9
        ):
            failures.append({"path": list(path), "expected": expected, "actual": actual})
        else:
            matched += 1
    return matched, skipped, failures


def verify_reference(aggregates: Path, reference: Path, output: Path) -> dict:
    specs = {
        "temporal": (
            reference / "analysis.json",
            ["total", "origins", "axes", "calendar_ytd", "comparisons"],
        ),
        "ai-attribution": (
            reference / "ai-comparison/analysis.json",
            [
                "candidates",
                "ai_candidates",
                "matched",
                "raw",
                "scenarios",
                "raw_by_scope",
                "by_quarter",
                "by_repository",
                "tools",
                "filter",
            ],
        ),
        "file-chains": (
            reference / "file-chains/analysis.json",
            ["annual", "ytd", "pooled_ytd", "contrasts", "chains"],
        ),
    }
    result = {
        "analyses": {},
        "model_calls": 0,
        "interval_policy": (
            "Bootstrap intervals are recomputed with canonical project order; "
            "compare point estimates and counts, not Monte Carlo order "
            "artifacts."
        ),
    }
    for name, (path, fields) in specs.items():
        old, new = load_json(path), load_json(aggregates / f"{name}.json")
        n, skipped, failures = compare_numbers(
            {k: old[k] for k in fields}, {k: new[k] for k in fields}
        )
        result["analyses"][name] = {
            "matched_numbers": n,
            "skipped_intervals": skipped,
            "failures": failures,
            "reference_sha256": digest(path),
        }
    result["passed"] = all(not v["failures"] for v in result["analyses"].values())
    save_json(output, result)
    if not result["passed"]:
        raise ValueError(f"Regression differences recorded in {output}")
    print(
        f"Verified {sum(v['matched_numbers'] for v in result['analyses'].values())} report numbers"
    )
    return result
