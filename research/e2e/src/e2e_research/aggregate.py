"""All statistical stages consume the portable JSONL, never Git or model APIs."""

from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from e2e_research.ai_followup import aggregate_ai_followups
from e2e_research.ai_reasons import aggregate_ai_reasons
from e2e_research.calendar_followup import aggregate_calendar_followups
from e2e_research.common import (
    CUTOFF,
    FEATURE_VERSION,
    LABELS,
    NAMES,
    SCHEMA_VERSION,
    code_digest,
    digest,
    dt,
    load_json,
    read_jsonl,
    runtime_versions,
    save_json,
    write_jsonl,
)
from e2e_research.coverage_rise import coverage_rise
from e2e_research.followup_stats import MEASURES, chains_summary, compare, counts, rates
from e2e_research.temporal import aggregate_temporal, summarize


def load_dataset(directory: Path) -> tuple[dict, list[dict], list[dict], dict]:
    manifest = load_json(directory / "dataset.json")
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["feature_version"] != FEATURE_VERSION
    ):
        raise ValueError("Unsupported dataset schema or feature definition")
    if dt(manifest["cutoff"]) != CUTOFF:
        raise ValueError("Dataset cutoff differs from the frozen study definition")
    path = directory / "commits.enriched.jsonl.gz"
    if digest(path) != manifest["data_sha256"]:
        raise ValueError("Enriched artifact checksum mismatch")
    rows, cohorts, seen = [], [], set()
    history = {"annual": defaultdict(Counter), "ytd": defaultdict(Counter)}
    for record in read_jsonl(path):
        identity = (record["slug"], record["sha"])
        if identity in seen:
            raise ValueError(f"Duplicate Git identity: {identity}")
        seen.add(identity)
        features = record["features"]
        if dt(record["date"]) <= CUTOFF:
            history["annual"][record["slug"]][str(features["year"])] += 1
            if features["within_ytd"]:
                history["ytd"][record["slug"]][str(features["year"])] += 1
        saved = record["classification"]
        if saved is None:
            if record["selection"] != "outside_path_selection":
                raise ValueError("Missing classification for selected commit")
            continue
        row = {k: v for k, v in saved.items() if k not in ("jev", "source", "saved_message")}
        row.update({k: v for k, v in features.items() if k not in ("followups", "e2e_paths")})
        row["source_stratum"] = record["source_stratum"]
        row["message"] = saved["saved_message"]
        row["batch"] = saved["source"]["run"]
        row["ai_markers"] = (
            features["ai_evidence"] if row["year"] >= 2025 else features["ai_markers_saved"]
        )
        rows.append(row)
        cohorts.extend(features["followups"])
    if len(seen) != manifest["records"] or len(rows) != manifest["classified"]:
        raise ValueError("Dataset counts disagree with manifest")
    if len(cohorts) != len({(r["slug"], r["sha"], r["scope"], r["window"]) for r in cohorts}):
        raise ValueError("Duplicate origin in follow-up cohorts")
    return manifest, rows, cohorts, history


def aggregate_followups(
    rows: list[dict], cohorts: list[dict], history: dict, repositories: dict
) -> dict:
    annual, ytd, complete_ytd = {}, {}, {}
    for slug in repositories:
        local = [r for r in rows if r["slug"] == slug]
        annual[slug], ytd[slug], complete_ytd[slug] = {}, {}, {}
        for year, total in sorted(history["annual"][slug].items()):
            group = [r for r in local if r["year"] == int(year)]
            annual[slug][year] = counts(group, total)
            selected = [r for r in group if r["within_ytd"]]
            denominator = history["ytd"][slug][year]
            ytd[slug][year] = counts(selected, denominator)
            complete_ytd[slug][year] = counts([r for r in selected if r["complete"]], denominator)
    all_slugs = sorted(repositories)
    oss = [s for s in all_slugs if repositories[s]["source_stratum"] == "public_oss"]
    contrasts = {}
    for a, b in ((2024, 2025), (2025, 2026), (2024, 2026)):
        contrasts[f"{a}_{b}"] = {
            "all": compare(ytd, a, b, all_slugs),
            "oss": compare(ytd, a, b, oss),
            "oss_minimum_50": compare(ytd, a, b, oss, minimum=50),
            "oss_complete": compare(complete_ytd, a, b, oss),
            "oss_within_e2e": compare(ytd, a, b, oss, denominator="matched"),
        }
    stable = [
        s
        for s in oss
        if all(
            ytd[s].get(str(y), {}).get("matched", 0) >= 20 and ytd[s][str(y)]["total"] >= 100
            for y in (2024, 2025, 2026)
        )
    ]
    pooled = {}
    for scope, slugs in (("all", all_slugs), ("oss", oss), ("stable_oss_2024_2026", stable)):
        pooled[scope] = {}
        for year in range(2019, 2027):
            values = [ytd[s][str(year)] for s in slugs if str(year) in ytd[s]]
            sums = dict(sum((Counter(v) for v in values), Counter()))
            sums.setdefault("total", 0)
            for key in MEASURES:
                sums.setdefault(key, 0)
            pooled[scope][str(year)] = {
                "counts": sums,
                "rates": rates(sums),
                "projects": len(values),
                "equal_rates": {
                    k: (
                        float(np.mean([100 * v[k] / v["total"] for v in values if v["total"]]))
                        if any(v["total"] for v in values)
                        else None
                    )
                    for k in MEASURES
                },
            }
    return {
        "annual": annual,
        "ytd": ytd,
        "pooled_ytd": pooled,
        "contrasts": contrasts,
        "stable_oss_projects": stable,
        "chains": chains_summary(cohorts),
    }


def numeric_leaves(value: object, path: tuple = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in ("examples", "message", "interpretation"):
                yield from numeric_leaves(item, (*path, key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from numeric_leaves(item, (*path, i))
    elif isinstance(value, int | float) and not isinstance(value, bool):
        yield {"path": list(path), "value": value}


def aggregate_dataset(dataset: Path, output: Path) -> dict:
    manifest, rows, cohorts, history = load_dataset(dataset)
    repositories = manifest["repositories"]
    temporal_repos = {
        slug: {
            **repo,
            "shallow": False,
            "date_span_utc": {"committer": {"earliest": repo["birth"]}},
        }
        for slug, repo in repositories.items()
    }
    print(
        f"Read {manifest['records']} commits; {len(rows)} classifications; {len(cohorts)} cohorts",
        flush=True,
    )
    file_chains = aggregate_followups(rows, cohorts, history, repositories)
    values = {
        "basic": {
            "labels": dict(zip(LABELS, NAMES, strict=True)),
            "total": summarize(rows),
            "all_nonmerge": manifest["records"],
            "by_source_stratum": {
                s: summarize([r for r in rows if r["source_stratum"] == s])
                for s in sorted({r["source_stratum"] for r in rows})
            },
            "by_repository": {
                s: summarize([r for r in rows if r["slug"] == s]) for s in repositories
            },
        },
        "temporal": aggregate_temporal(rows, temporal_repos),
        "ai-attribution": aggregate_ai_reasons([r for r in rows if r["year"] in (2025, 2026)]),
        "file-chains": file_chains,
        "coverage-rise": coverage_rise(file_chains),
        "ai-followup": aggregate_ai_followups(cohorts, rows),
        "calendar-followup": aggregate_calendar_followups(cohorts, repositories),
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, value in values.items():
        save_json(output / f"{name}.json", value)
    n_metrics = write_jsonl(
        output / "metrics.jsonl.gz",
        (
            {"analysis": name, **leaf}
            for name, value in values.items()
            for leaf in numeric_leaves(value)
        ),
    )
    provenance = {
        "dataset_sha256": manifest["data_sha256"],
        "code_sha256": code_digest(),
        "output_sha256": {f"{name}.json": digest(output / f"{name}.json") for name in values},
        "numeric_metrics": n_metrics,
        "runtime": runtime_versions(),
        "model_calls": 0,
        "inputs": "commits.enriched.jsonl.gz and dataset.json; no Git or network access",
    }
    save_json(output / "provenance.json", provenance)
    print(f"Wrote {len(values)} analyses and {n_metrics} numeric leaves", flush=True)
    return values
