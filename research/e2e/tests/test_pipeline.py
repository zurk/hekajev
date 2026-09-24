import json
import os
import subprocess
from pathlib import Path

import pytest

from e2e_research.aggregate import aggregate_dataset
from e2e_research.common import digest, read_jsonl, write_jsonl
from e2e_research.enrich import enrich_dataset
from e2e_research.ingest import ingest_runs


def test_real_git_and_resume_reader_produce_an_offline_reproducible_census(tmp_path, monkeypatch):
    root = tmp_path / "repos"
    repo = root / "example__demo.git"
    repo.mkdir(parents=True)

    def git(*args, day=1):
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": f"2025-01-{day:02}T12:00:00+00:00",
            "GIT_COMMITTER_DATE": f"2025-01-{day:02}T12:00:00+00:00",
        }
        return subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Example",
                "-c",
                "user.email=test@example.com",
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()

    git("init")
    (repo / "e2e").mkdir()
    (repo / "e2e/login.spec.ts").write_text("test('login', () => {});\n")
    git("add", ".")
    git("commit", "-m", "Add login coverage\n\nCo-authored-by: Claude <bot@example.com>")
    a = git("rev-parse", "HEAD")
    (repo / "README.md").write_text("Demo\n")
    git("add", ".")
    git("commit", "-m", "Docs", day=2)
    (repo / "e2e/login.spec.ts").write_text("test('login', async () => { await ready(); });\n")
    git("add", ".")
    git("commit", "-m", "Stabilize login", day=3)
    b = git("rev-parse", "HEAD")
    batch = tmp_path / "runs" / "one"
    batch.mkdir(parents=True)
    manifest = {
        "run_id": "run",
        "model": "saved-only",
        "repositories": [
            {"id": "repo", "origin": "https://github.com/example/demo", "commits": [a, b]}
        ],
    }
    (batch / "manifest.json").write_text(json.dumps(manifest))
    records = []
    for sha, day, label in ((a, 1, "coverage"), (b, 3, "flakiness")):
        records.append(
            {
                "run_id": "run",
                "repo_id": "repo",
                "sha": sha,
                "date": f"2025-01-{day:02}T12:00:00+00:00",
                "title": label,
                "matched": True,
                "complete": True,
                "chunks": 1,
                "status": "ok",
                "is_merge": False,
                "files": [{"path": "e2e/login.spec.ts"}],
                "filters": [True],
                "calls": [],
                "classifications": {"reason": {"labels": [label], "undecided_labels": []}},
            }
        )
    write_jsonl(batch / "results.jsonl.gz", [{**records[0], "status": "error"}])
    (batch / "results.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    population = {
        "summary": {"all_commits": 2},
        "repositories": [
            {
                "slug": "example/demo",
                "frozen_sha": b,
                "fetch_finished_at": "2026-09-21T20:00:00Z",
                "date_span_utc": {"committer": {"earliest": "2025-01-01T12:00:00Z"}},
                "source_stratum": "public_oss",
                "path_patterns": ["e2e/**"],
                "repository_history_counts": {"nonmerge": 3},
            }
        ],
    }
    population_file = tmp_path / "population.json"
    population_file.write_text(json.dumps(population))
    sources, dataset = tmp_path / "sources", tmp_path / "dataset"
    ingest_runs(population_file, batch.parent, sources)
    enrich_dataset(sources, root, dataset)
    path = dataset / "commits.enriched.jsonl.gz"
    rows = list(read_jsonl(path))
    assert len(rows) == 3
    assert sum(r["classification"] is not None for r in rows) == 2
    unclassified = next(r for r in rows if r["classification"] is None)
    assert unclassified["selection"] == "outside_path_selection"
    assert "ai_attributed" not in unclassified["features"]
    first = next(r for r in rows if r["sha"] == a)
    assert first["features"]["ai_attributed"] is True
    followup = next(
        r for r in first["features"]["followups"] if r["scope"] == "new_test" and r["window"] == 30
    )
    assert followup["repeat_maintenance"] and followup["first_maintenance_days"] == 2
    before = digest(path)
    enrich_dataset(sources, root, dataset)
    assert digest(path) == before

    def no_external_process(*args, **kwargs):
        raise AssertionError("Aggregation must not access Git or another process")

    monkeypatch.setattr(subprocess, "run", no_external_process)
    output = tmp_path / "aggregates"
    result = aggregate_dataset(dataset, output)
    assert result["basic"]["all_nonmerge"] == 3
    assert result["file-chains"]["ytd"]["example/demo"]["2025"]["maintenance"] == 1
    assert result["file-chains"]["pooled_ytd"]["all"]["2025"]["rates"][
        "maintenance"
    ] == pytest.approx(100 / 3)
    other = tmp_path / "second-aggregate"
    aggregate_dataset(dataset, other)
    for artifact in output.glob("*.json"):
        assert artifact.read_bytes() == (other / artifact.name).read_bytes()
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        aggregate_dataset(dataset, output)


def test_jsonl_gzip_is_independent_of_output_filename(tmp_path: Path):
    rows = [{"n": 1, "value": "данные"}]
    a, b = tmp_path / "one.jsonl.gz", tmp_path / "two.jsonl.gz"
    write_jsonl(a, rows)
    write_jsonl(b, rows)
    assert digest(a) == digest(b)
