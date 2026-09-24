"""Read effective saved results through HekaJev's resume-aware reader."""

from pathlib import Path

from hekajev.pipeline import read_records

from e2e_research.common import (
    CUTOFF,
    SCHEMA_VERSION,
    digest,
    dt,
    load_json,
    save_json,
    write_jsonl,
)


def ingest_runs(population_path: Path, runs: Path, output: Path) -> dict:
    population = load_json(population_path)
    repositories = {}
    for repo in population["repositories"]:
        slug = repo["slug"]
        repositories[slug] = {
            "slug": slug,
            "url": f"https://github.com/{slug}.git",
            "frozen_sha": repo["frozen_sha"],
            "observed_until": min(CUTOFF, dt(repo["fetch_finished_at"])).isoformat(),
            "birth": repo["date_span_utc"]["committer"]["earliest"],
            "source_stratum": repo["source_stratum"],
            "path_patterns": repo["path_patterns"],
            "expected_nonmerge": repo["repository_history_counts"]["nonmerge"],
        }
    manifests = sorted(runs.glob("**/manifest.json"))
    if not manifests:
        raise ValueError("No saved run manifests found")
    rows, sources, seen = [], [], set()
    for index, manifest_path in enumerate(manifests):
        manifest = load_json(manifest_path)
        slugs = {
            repo["id"]: repo["origin"].removesuffix(".git").removeprefix("https://github.com/")
            for repo in manifest["repositories"]
        }
        source = str(manifest_path.parent.relative_to(runs))
        hashes = {
            p.name: digest(p)
            for p in manifest_path.parent.iterdir()
            if p.name in ("manifest.json", "results.jsonl", "results.jsonl.gz")
        }
        sources.append({"directory": source, "sha256": hashes, "model": manifest["model"]})
        effective = read_records(manifest_path.parent / "results.jsonl", manifest)
        expected = {
            (repo["id"], sha) for repo in manifest["repositories"] for sha in repo["commits"]
        }
        if set(effective) != expected:
            raise ValueError(f"Incomplete source run: {source}")
        for (repo_id, sha), record in effective.items():
            slug = slugs[repo_id]
            identity = (slug, sha)
            if slug not in repositories or identity in seen:
                raise ValueError(f"Unknown repository or duplicate classification: {identity}")
            if record["status"] != "ok" or record["is_merge"]:
                raise ValueError(f"Require successful nonmerge source records: {identity}")
            seen.add(identity)
            contexts = []
            for call in record.get("calls", []):
                context = call.get("input", {}).get("state", {}).get("context", {})
                contexts.extend([context.get("message_head", ""), context.get("message_tail", "")])
            reason = record["classifications"].get("reason", {})
            rows.append(
                {
                    "slug": slug,
                    "sha": sha,
                    "date": dt(record["date"]).isoformat(),
                    "title": record["title"],
                    "matched": record["matched"],
                    "complete": record["complete"],
                    "chunks": record["chunks"],
                    "labels": reason.get("labels", []),
                    "undecided_labels": reason.get("undecided_labels", []),
                    "changed_files": len(record["files"]),
                    "saved_message": "\n".join(dict.fromkeys([record["title"], *contexts])),
                    "jev": {
                        "filters": record["filters"],
                        "classifications": record["classifications"],
                        "status": record["status"],
                    },
                    "source": {"run": source, "run_id": record["run_id"], "repo_id": repo_id},
                }
            )
        if index % 20 == 0:
            print(f"Read {index + 1}/{len(manifests)} runs; {len(rows)} records", flush=True)
    if len(rows) != population["summary"]["all_commits"]:
        raise ValueError("Source census count differs from population")
    for slug, repo in repositories.items():
        repo["first_candidate"] = min(r["date"] for r in rows if r["slug"] == slug)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "classified.jsonl.gz"
    write_jsonl(path, sorted(rows, key=lambda r: (r["slug"], r["sha"])))
    result = {
        "schema_version": SCHEMA_VERSION,
        "cutoff": CUTOFF.isoformat(),
        "repositories": repositories,
        "source_runs": sources,
        "source_population_sha256": digest(population_path),
        "classified_sha256": digest(path),
        "classified_count": len(rows),
        "model_calls": 0,
    }
    save_json(output / "sources.json", result)
    return result
