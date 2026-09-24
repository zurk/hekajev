import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from hekajev.cli import main
from hekajev.pipeline import summarize


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def fixture_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "test_case.py").write_text("def test_case():\n    assert 2 + 2 == 4\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Add test")
    return repo


def write_config(path: Path):
    path.write_text(
        json.dumps(
            {
                "filters": ["Does this change tests?"],
                "classifications": {
                    "level": {
                        "mode": "multi",
                        "question": "Which test levels are evidenced?",
                        "categories": {"unit": "Isolated behavior", "e2e": "Full user workflow"},
                    }
                },
            }
        )
    )


def test_real_cli_multisource_preview_and_partial_population(tmp_path):
    repos = [fixture_repo(tmp_path, name) for name in ("one", "two")]
    config = tmp_path / "config.yaml"
    write_config(config)
    output = tmp_path / "preview"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hekajev.cli",
            "scan",
            *map(str, repos),
            "--config",
            str(config),
            "--output",
            str(output),
            "--dry-run",
            "--limit",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "TOTAL" in result.stdout and "Processed" in result.stderr
    records = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert len({row["repo_id"] for row in records}) == 2
    assert all(row["status"] == "preview" for row in records)
    assert all(isinstance(row["calls"][0]["input"]["state"], dict) for row in records)
    assert (output / "report.html").exists() and (output / "run.log").exists()
    (output / "results.jsonl").write_text(json.dumps(records[0]) + "\n")
    summary = summarize([output])
    assert summary["counts"]["expected"] == 2 and summary["counts"]["missing"] == 1
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hekajev.cli",
            "summarize",
            str(output),
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and json.loads(result.stdout)["counts"]["missing"] == 1


def test_live_contract_cost_resume_frozen_selection_and_unknown_usage(
    tmp_path, monkeypatch, capsys
):
    repos = [fixture_repo(tmp_path, name) for name in ("one", "two")]
    config = tmp_path / "config.yaml"
    write_config(config)
    output = tmp_path / "live"
    secret = "fixture-key-never-print"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    calls = []

    def handle(req):
        assert req.headers["Authorization"] == f"Bearer {secret}"
        assert req.url.path == "/v1/systemone"
        payload = json.loads(req.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "answers": {
                    key: {"type": "noul", "noul": 0.99 if key != "class_0_1" else 0.01}
                    for key in payload["questions"]
                },
            },
        )

    original = httpx.Client
    monkeypatch.setattr(
        "hekajev.jev.httpx.Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(handle),
            **kwargs,
        ),
    )
    args = [
        "hekajev",
        "scan",
        *map(str, repos),
        "--config",
        str(config),
        "--output",
        str(output),
        "--limit",
        "10",
        "--workers",
        "1",
        "--progress-every",
        "1",
    ]
    monkeypatch.setattr(sys, "argv", args)
    assert main() == 0
    streams = capsys.readouterr()
    assert "TOTAL" in streams.out and "estimated cost" in streams.err
    summary = json.loads((output / "summary.json").read_text())
    assert summary["counts"]["matched"] == 2
    assert summary["classifications"]["level"]["counts"] == {"unit": 2}
    assert summary["spend"]["requests"] == 3
    assert summary["spend"]["unknown_cost_requests"] == 1
    assert summary["spend"]["cost_usd"] == pytest.approx(200 * 0.042 / 1_000_000)
    (repos[0] / "new.py").write_text("changed_head = True\n")
    git(repos[0], "add", ".")
    git(repos[0], "commit", "-m", "Move HEAD")
    monkeypatch.setattr(sys, "argv", args + ["--resume"])
    assert main() == 0
    assert len(calls) == 3
    resumed = json.loads((output / "summary.json").read_text())
    assert resumed["counts"]["expected"] == 2 and resumed["spend"]["cached_commits"] == 2
    assert resumed["spend"]["current_run_cost_usd"] == 0
    assert resumed["spend"]["cost_usd"] == summary["spend"]["cost_usd"]
    assert all(secret not in path.read_text() for path in output.iterdir() if path.is_file())
    data = json.loads(config.read_text())
    data["filter_mode"] = "or"
    config.write_text(json.dumps(data))
    assert main() == 2
    assert "Resume settings differ" in capsys.readouterr().err

    failed = tmp_path / "failed"
    failure_args = list(args)
    failure_args[failure_args.index("--output") + 1] = str(failed)
    monkeypatch.setattr(sys, "argv", failure_args)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "hekajev.jev.httpx.Client",
            lambda **kwargs: original(
                transport=httpx.MockTransport(lambda _: httpx.Response(401)), **kwargs
            ),
        )
        assert main() == 1
    failed_rows = [json.loads(line) for line in (failed / "results.jsonl").read_text().splitlines()]
    assert len(failed_rows) == 1 and failed_rows[0]["fatal_http_status"] == 401
    failed_summary = json.loads((failed / "summary.json").read_text())
    assert failed_summary["counts"]["missing"] == 2
    assert all(row["status"] == "error" and not row["complete"] for row in failed_rows)
    assert all(row["calls"][0]["input_hash"] and row["calls"][0]["input"] for row in failed_rows)
    monkeypatch.setattr(sys, "argv", failure_args + ["--resume"])
    assert main() == 0
    recovered = json.loads((failed / "summary.json").read_text())
    assert recovered["counts"]["ok"] == 3 and recovered["counts"]["error"] == 0
    assert recovered["spend"]["unknown_cost_requests"] == 1


def test_resume_recovered_alias_preserves_existing_canonical_selection(tmp_path):
    repo = fixture_repo(tmp_path, "repo")
    alias = tmp_path / "initially-unavailable-alias"
    config, output = tmp_path / "config.yaml", tmp_path / "output"
    write_config(config)
    original_sha = git(repo, "rev-parse", "HEAD")
    command = [
        sys.executable,
        "-m",
        "hekajev.cli",
        "scan",
        str(alias),
        str(repo),
        "--config",
        str(config),
        "--output",
        str(output),
        "--dry-run",
        "--limit",
        "1",
    ]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 1, first.stderr
    initial_records = (output / "results.jsonl").read_text()
    assert json.loads(initial_records)["sha"] == original_sha
    (repo / "new.py").write_text("new_head = True\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Advance HEAD after selection")
    alias.symlink_to(repo, target_is_directory=True)

    resumed = subprocess.run(command + ["--resume"], capture_output=True, text=True)
    assert resumed.returncode == 0, resumed.stderr
    assert (output / "results.jsonl").read_text() == initial_records
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(manifest["repositories"]) == 1
    assert manifest["repositories"][0]["commits"] == [original_sha]
    summary = json.loads((output / "summary.json").read_text())
    assert summary["counts"]["expected"] == 1
    assert summary["counts"]["source_errors"] == summary["counts"]["missing"] == 0


def test_resume_distinguishes_same_relative_source_from_different_working_directories(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    fixture_repo(left, "repo")
    fixture_repo(right, "repo")
    config = tmp_path / "config.yaml"
    output = tmp_path / "output"
    write_config(config)
    command = [
        sys.executable,
        "-m",
        "hekajev.cli",
        "scan",
        "repo",
        "--config",
        str(config),
        "--output",
        str(output),
        "--dry-run",
        "--limit",
        "1",
    ]
    first = subprocess.run(command, cwd=left, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    resumed = subprocess.run(command + ["--resume"], cwd=right, capture_output=True, text=True)
    assert resumed.returncode == 2
    assert "Resume settings differ from the manifest" in resumed.stderr


def test_resume_rejects_retargeted_source_symlink(tmp_path):
    one = fixture_repo(tmp_path, "one")
    two = fixture_repo(tmp_path, "two")
    alias = tmp_path / "repo"
    alias.symlink_to(one, target_is_directory=True)
    config = tmp_path / "config.yaml"
    output = tmp_path / "output"
    write_config(config)
    command = [
        sys.executable,
        "-m",
        "hekajev.cli",
        "scan",
        str(alias),
        "--config",
        str(config),
        "--output",
        str(output),
        "--dry-run",
        "--limit",
        "1",
    ]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    alias.unlink()
    alias.symlink_to(two, target_is_directory=True)
    resumed = subprocess.run(command + ["--resume"], capture_output=True, text=True)
    assert resumed.returncode == 2
    assert "Source identity differs from the manifest" in resumed.stderr
