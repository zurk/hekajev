import gzip
import json
import subprocess
import sys
import threading

import httpx
from test_smoke import fixture_repo, git, write_config

from hekajev.cli import main
from hekajev.pipeline import summarize


def test_exact_batch_merge_first_parent_and_compressed_resume(tmp_path):
    repo = fixture_repo(tmp_path, "repo")
    root_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "feature")
    (repo / "test_feature.py").write_text("assert 'feature'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Add feature test")
    feature_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "main")
    (repo / "README.md").write_text("Main-only change\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Update readme")
    first_parent = git(repo, "rev-parse", "HEAD")
    git(repo, "merge", "--no-ff", "feature", "-m", "Integrate tests")
    merge_sha = git(repo, "rev-parse", "HEAD")
    selected = [feature_sha, merge_sha, root_sha]
    commit_file = tmp_path / "commits.txt"
    commit_file.write_text("\n".join(selected) + "\n")
    config, output = tmp_path / "config.yaml", tmp_path / "output"
    write_config(config)
    command = [
        sys.executable,
        "-m",
        "hekajev.cli",
        "scan",
        str(repo),
        "--config",
        str(config),
        "--output",
        str(output),
        "--dry-run",
        "--commits-file",
        str(commit_file),
        "--include-merges",
    ]
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    raw = (output / "results.jsonl").read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    assert {row["sha"] for row in rows} == set(selected)
    merged = next(row for row in rows if row["is_merge"])
    assert merged["parents"][0] == first_parent
    assert {file["path"] for file in merged["files"]} == {"test_feature.py"}
    assert merged["calls"][0]["input"]["state"]["commit"]["merge_comparison"]
    with gzip.open(output / "results.jsonl.gz", "wb") as stream:
        stream.write(raw)
    interrupted = {**rows[0], "status": "error", "error": "interrupted", "complete": False}
    (output / "results.jsonl").write_text(json.dumps(interrupted) + "\n")
    run = subprocess.run(command + ["--resume"], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    counts = summarize([output])["counts"]
    assert counts["expected"] == counts["preview"] == 3
    assert counts["missing"] == counts["error"] == 0
    assert gzip.decompress((output / "results.jsonl.gz").read_bytes()) == raw


def test_interrupt_during_result_serialization_drains_and_resume_reuses_responses(
    tmp_path,
    monkeypatch,
):
    repo = fixture_repo(tmp_path, "repo")
    (repo / "another_test.py").write_text("assert 3 + 4 == 7\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Add another scenario")
    config, output = tmp_path / "config.yaml", tmp_path / "output"
    write_config(config)
    barrier = threading.Barrier(2, timeout=10)
    calls = []

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        barrier.wait()
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "usage": {"input_tokens": 100, "output_tokens": 0},
                "answers": {key: {"type": "noul", "noul": 0.99} for key in payload["questions"]},
            },
        )

    original_client, original_dumps = httpx.Client, json.dumps
    interrupted = False

    def interrupt_once(value, *args, **kwargs):
        nonlocal interrupted
        if (
            not interrupted
            and isinstance(value, dict)
            and value.get("status") == "ok"
            and "calls" in value
        ):
            interrupted = True
            raise KeyboardInterrupt
        return original_dumps(value, *args, **kwargs)

    monkeypatch.setenv("TYPESAFE_API_KEY", "fixture-key")
    monkeypatch.setattr(
        "hekajev.jev.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    monkeypatch.setattr("hekajev.pipeline.json.dumps", interrupt_once)
    args = [
        "hekajev",
        "scan",
        str(repo),
        "--config",
        str(config),
        "--output",
        str(output),
        "--limit",
        "2",
        "--workers",
        "2",
    ]
    monkeypatch.setattr(sys, "argv", args)
    assert main() == 130
    assert interrupted and len(calls) == 2
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert all(row["status"] == "ok" and row["calls"][0]["valid"] for row in rows)
    summary = summarize([output])
    assert summary["counts"]["ok"] == 2 and summary["counts"]["missing"] == 0
    assert summary["spend"]["requests"] == 2

    monkeypatch.setattr(sys, "argv", args + ["--resume"])
    assert main() == 0
    assert len(calls) == 2
    resumed = json.loads((output / "summary.json").read_text())
    assert resumed["spend"]["cached_commits"] == 2
    assert resumed["spend"]["current_run_cost_usd"] == 0
