"""Recalculate published headline counts from the downloadable commit-level exports."""

import json
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile

from e2e_research.common import CUTOFF, MAINTENANCE, load_json, save_json
from e2e_research.coverage_rise import coverage_rise


def read_zip_json(path: Path, member: str) -> dict:
    with ZipFile(path) as archive:
        return json.loads(archive.read(member))


def recalculate(commits: dict, origins: dict) -> dict:
    rows = commits["commits"]
    identities = [(row["repository"], row["sha"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate commit identity in public export")

    matched = [row for row in rows if row["e2e_relevance"] is True]
    labels = [(row, set(row["reason"]["labels"])) for row in matched]
    ytd = defaultdict(lambda: defaultdict(lambda: {"matched": 0, "coverage": 0, "maintenance": 0}))
    for row, reasons in labels:
        year = row["date"][:4]
        if year not in {"2025", "2026"} or row["date"][5:10] > CUTOFF.strftime("%m-%d"):
            continue
        counts = ytd[row["repository"]][year]
        counts["matched"] += 1
        counts["coverage"] += "coverage" in reasons
        counts["maintenance"] += bool(reasons & MAINTENANCE)

    matched_ids = {(row["repository"], row["sha"]) for row in matched}
    followups = {}
    for cohort, cohort_rows in origins["cohorts"].items():
        missing = {(row["slug"], row["sha"]) for row in cohort_rows} - matched_ids
        if missing:
            raise ValueError(f"{cohort}: {len(missing)} origins absent from E2E-positive commits")
        label = "flakiness" if cohort == "stabilization" else "maintenance"
        strict = [row for row in cohort_rows if row["all_selected_files"] == 1]
        followups[cohort] = {
            "origins": len(cohort_rows),
            "events": sum(row[f"repeat_{label}"] for row in cohort_rows),
            "strict_origins": len(strict),
            "strict_events": sum(
                ("flakiness" in row["strict_repeat_labels"])
                if cohort == "stabilization"
                else bool(set(row["strict_repeat_labels"]) & MAINTENANCE)
                for row in strict
            ),
        }

    return {
        "records": len(rows),
        "matched": len(matched),
        "coverage": sum("coverage" in reasons for _, reasons in labels),
        "maintenance_union": sum(bool(reasons & MAINTENANCE) for _, reasons in labels),
        "no_confident_reason": sum(not reasons for _, reasons in labels),
        "coverage_rise": coverage_rise({"ytd": ytd}),
        "followups": followups,
    }


def verify_public(
    commits_path: Path,
    origins_path: Path,
    study_path: Path,
    insights_path: Path,
    output: Path | None = None,
) -> dict:
    result = recalculate(
        read_zip_json(commits_path, "commits.json"),
        read_zip_json(origins_path, "followup-origins.json"),
    )
    study = load_json(study_path)["groups"]["all"]
    insights = load_json(insights_path)["followups"]
    expected = {
        "records": study["counts"]["records"],
        "matched": study["counts"]["matched"],
        "coverage": study["label_counts"]["coverage"],
        "maintenance_union": study["counts"]["maintenance_union"],
        "no_confident_reason": study["counts"]["no_confident_reason"],
    }
    for name, value in expected.items():
        if result[name] != value:
            raise ValueError(f"Published {name}={value}; recalculated {result[name]}")
    for cohort, counts in result["followups"].items():
        published = insights[cohort]
        for result_key, section, reference_key in (
            ("origins", "all", "origins"),
            ("events", "all", "events"),
            ("strict_origins", "both_single_selected_file", "origins"),
            ("strict_events", "both_single_selected_file", "events"),
        ):
            value = published[section][reference_key]
            if counts[result_key] != value:
                raise ValueError(
                    f"Published {cohort}.{result_key}={value}; recalculated {counts[result_key]}"
                )
    result["published_counts_match"] = True
    if output is not None:
        save_json(output, result)
    return result
