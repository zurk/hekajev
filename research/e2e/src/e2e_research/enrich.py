"""Build a portable JSONL census with saved classifications and Git-derived features."""

import hashlib
import json
import os
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from hekajev.git import Repository

from e2e_research.attribution import AI_PATTERN, size_bin, strip_attribution, tools_from_markers
from e2e_research.common import (
    CUTOFF,
    DAY,
    FEATURE_VERSION,
    SCHEMA_VERSION,
    WINDOWS,
    age_year,
    code_digest,
    digest,
    dt,
    load_json,
    read_jsonl,
    runtime_versions,
    save_json,
    write_jsonl,
)
from e2e_research.lineage import followups
from e2e_research.paths import ancestry_masks, parse_changes, path_kind


def git_read(
    repo: Repository, *args: str, input_bytes: bytes | None = None, literal_paths: bool = True
) -> bytes:
    process = subprocess.run(
        repo.command(*args, literal_paths=literal_paths),
        input=input_bytes,
        capture_output=True,
        check=True,
        timeout=900,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
    )
    return process.stdout


def metadata(repo: Repository, shas: list[str]) -> dict:
    result = {}
    for start in range(0, len(shas), 500):
        batch = shas[start : start + 500]
        raw = git_read(
            repo,
            "show",
            "--no-patch",
            "--no-show-signature",
            "--format=%H%x00%ae%x00%B%x00",
            *batch,
            "--",
        )
        fields = raw.decode("utf-8", errors="replace").split("\0")
        if len(fields) != len(batch) * 3 + 1:
            raise ValueError("Unexpected Git message framing")
        for i in range(0, len(fields) - 1, 3):
            sha, email, message = fields[i : i + 3]
            markers = sorted({m.group(0).strip() for m in AI_PATTERN.finditer(message)})
            result[sha.strip()] = {
                "ai_attributed": bool(markers),
                "ai_evidence": markers,
                "ai_tools": tools_from_markers(markers),
                "git_author_id": hashlib.sha256(email.strip().lower().encode()).hexdigest()[:24],
                "message_chars_without_attribution": len(strip_attribution(message)),
            }
    return result


def extract_repository(settings: dict, rows: list[dict], repo_root: Path) -> dict:
    slug = settings["slug"]
    repository = Repository(repo_root / (slug.replace("/", "__") + ".git"))
    if repository.shallow:
        raise ValueError(f"Full history required: {slug}")
    raw = git_read(
        repository,
        "log",
        "--reverse",
        "--topo-order",
        "--format=%H%x09%ct%x09%P",
        settings["frozen_sha"],
    )
    graph = [
        (sha, int(timestamp), parents.split())
        for sha, timestamp, parents in (line.split("\t") for line in raw.decode().splitlines())
    ]
    selected = sorted(r["sha"] for r in rows)
    masks = ancestry_masks(graph, selected)
    changes = parse_changes(
        git_read(
            repository,
            "diff-tree",
            "--stdin",
            "--root",
            "-r",
            "--name-status",
            "-z",
            "-M",
            "--",
            *repository.pathspecs(settings["path_patterns"]),
            input_bytes=("\n".join(selected) + "\n").encode(),
            literal_paths=False,
        )
    )
    if set(changes) != set(selected):
        raise ValueError(f"Missing candidate path changes: {slug}")
    timestamps = {sha: timestamp for sha, timestamp, _ in graph}
    meta = metadata(repository, selected)
    index = {sha: i for i, sha in enumerate(selected)}
    candidates = []
    for row in rows:
        row = dict(row)
        sha = row["sha"]
        if timestamps[sha] != int(dt(row["date"]).timestamp()):
            raise ValueError(f"Saved and Git dates disagree: {slug}/{sha}")
        row.update(
            files=changes[sha],
            bit=1 << index[sha],
            ancestors=masks[sha],
            timestamp=timestamps[sha],
            year=dt(row["date"]).year,
            source_stratum=settings["source_stratum"],
        )
        candidates.append(row)
    derived, edges, _ = followups(candidates, dt(settings["observed_until"]).timestamp(), slug)
    per_origin = defaultdict(list)
    for outcome in derived:
        per_origin[outcome["sha"]].append(outcome)
    features = {}
    for row in candidates:
        sha = row["sha"]
        features[sha] = {
            **meta[sha],
            "ai_attributed_saved": bool(AI_PATTERN.search(row["saved_message"])),
            "ai_markers_saved": sorted(
                {m.group(0).strip() for m in AI_PATTERN.finditer(row["saved_message"])}
            ),
            "size_bin": size_bin(row["changed_files"]),
            "e2e_paths": [
                {**f, "kind": path_kind(f.get("new_path", f["path"]))} for f in row["files"]
            ],
            "observation_days": (dt(settings["observed_until"]).timestamp() - row["timestamp"])
            / DAY,
            "eligible_windows": {
                str(w): row["timestamp"] <= dt(settings["observed_until"]).timestamp() - w * DAY
                for w in WINDOWS
            },
            "followups": per_origin[sha],
        }
    nonmerge = [[sha, timestamp] for sha, timestamp, parents in graph if len(parents) <= 1]
    if len(nonmerge) != settings["expected_nonmerge"]:
        raise ValueError(f"Frozen history denominator differs: {slug}")
    return {"history": nonmerge, "features": features, "edges_30d": edges}


def enrich_dataset(source_dir: Path, repo_root: Path, output: Path) -> dict:
    sources = load_json(source_dir / "sources.json")
    source_file = source_dir / "classified.jsonl.gz"
    if digest(source_file) != sources["classified_sha256"]:
        raise ValueError("Classified artifact checksum mismatch")
    classified = list(read_jsonl(source_file))
    output.mkdir(parents=True, exist_ok=True)
    cache_dir = output / "cache"
    cache_dir.mkdir(exist_ok=True)
    source_hash = sources["classified_sha256"]
    cache_paths, edge_rows = [], []
    code_hash = code_digest()
    runtime = runtime_versions()
    runtime["git"] = subprocess.run(
        ["git", "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    for slug, settings in sorted(sources["repositories"].items()):
        rows = [r for r in classified if r["slug"] == slug]
        fingerprint = hashlib.sha256(
            json.dumps(
                [FEATURE_VERSION, source_hash, code_hash, settings, runtime], sort_keys=True
            ).encode()
        ).hexdigest()
        cache_path = cache_dir / (slug.replace("/", "__") + ".json.gz")
        cached = next(read_jsonl(cache_path)) if cache_path.exists() else None
        if cached is None or cached["fingerprint"] != fingerprint:
            cached = {"fingerprint": fingerprint, **extract_repository(settings, rows, repo_root)}
            write_jsonl(cache_path, [cached])
        cache_paths.append((slug, settings, cache_path))
        edge_rows.extend(cached["edges_30d"])
        print(
            f"Enriched {slug}: {len(rows)} classifications / {len(cached['history'])} commits",
            flush=True,
        )
    indexed = {(r["slug"], r["sha"]): r for r in classified}

    def records():
        for slug, settings, path in cache_paths:
            cache = next(read_jsonl(path))
            birth, first = dt(settings["birth"]), dt(settings["first_candidate"])
            for sha, timestamp in sorted(cache["history"], key=lambda x: (x[1], x[0])):
                when = datetime.fromtimestamp(timestamp, UTC)
                saved = indexed.get((slug, sha))
                calendar = {
                    "year": when.year,
                    "quarter": f"{when.year}-Q{(when.month - 1) // 3 + 1}",
                    "month": f"{when.year}-{when.month:02d}",
                    "halfyear": f"{when.year}-H{1 if when.month <= 6 else 2}",
                    "age_year": age_year(when, birth),
                    "e2e_age_year": age_year(when, first) if when >= first else None,
                    "within_ytd": (when.month, when.day) <= (CUTOFF.month, CUTOFF.day),
                }
                yield {
                    "schema_version": SCHEMA_VERSION,
                    "slug": slug,
                    "sha": sha,
                    "date": when.isoformat(),
                    "source_stratum": settings["source_stratum"],
                    "selection": "classified" if saved else "outside_path_selection",
                    "classification": saved,
                    "features": {**calendar, **cache["features"].get(sha, {})},
                }

    destination = output / "commits.enriched.jsonl.gz"
    total = write_jsonl(destination, records())
    write_jsonl(
        output / "edges-30d.jsonl.gz",
        sorted(edge_rows, key=lambda r: (r["slug"], r["source"], r["target"], r["path"])),
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "code_sha256": code_hash,
        "runtime": runtime,
        "classified_sha256": source_hash,
        "data_sha256": digest(destination),
        "records": total,
        "classified": len(classified),
        "repositories": sources["repositories"],
        "cutoff": sources["cutoff"],
        "windows": list(WINDOWS),
        "model_calls": 0,
        "interpretation": (
            "Outside selection is unclassified, not negative. AI attribution "
            "concerns a commit, not proven authorship of each test."
        ),
    }
    save_json(output / "dataset.json", manifest)
    return manifest
