import json
import subprocess
import sys

import pytest
import yaml
from test_smoke import fixture_repo, git, write_config

from hekajev.git import Repository


def change(repo, message, files):
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def test_path_history_root_rename_delete_or_and_merge_parent(tmp_path):
    repo = fixture_repo(tmp_path, "repo")
    root = git(repo, "rev-parse", "HEAD")
    added = change(repo, "Add E2E and product", {"e2e/a.spec.ts": "one", "app.py": "one"})
    (repo / "archive").mkdir()
    git(repo, "mv", "e2e/a.spec.ts", "archive/a.spec.ts")
    git(repo, "commit", "-m", "Move test out")
    moved = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "feature")
    branch = change(repo, "Nested E2E", {"e2e/nested/b.spec.ts": "two"})
    git(repo, "switch", "main")
    change(repo, "Unrelated main", {"README.md": "main"})
    git(repo, "merge", "--no-ff", "feature", "-m", "Merge E2E")
    merged = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "docs")
    change(repo, "Docs branch", {"README.md": "docs"})
    git(repo, "switch", "main")
    git(repo, "merge", "--no-ff", "docs", "-m", "Merge docs only")
    git(repo, "switch", "-c", "discarded")
    discarded = change(repo, "Unretained E2E", {"e2e/unused.spec.ts": "discarded"})
    git(repo, "switch", "main")
    git(repo, "merge", "--no-ff", "-s", "ours", "discarded", "-m", "Discard branch changes")
    git(repo, "rm", "e2e/nested/b.spec.ts")
    git(repo, "commit", "-m", "Delete E2E")
    deleted = git(repo, "rev-parse", "HEAD")
    change(repo, "Newest unrelated", {"app.py": "two"})
    repository = Repository(repo)

    def select(patterns, **kwargs):
        return repository.commits("HEAD", 100, None, None, path_patterns=patterns, **kwargs)

    assert set(select(["e2e/**"])) == {added, moved, branch, deleted, discarded}
    assert set(select(["e2e/**"], include_merges=True)) == {
        added,
        moved,
        branch,
        merged,
        deleted,
        discarded,
    }
    assert set(select(["e2e/*"])) == {added, moved, discarded}
    assert set(select(["test_case.py", "archive/**"])) == {root, moved}
    assert repository.commits("HEAD", 1, None, None, path_patterns=["e2e/**"]) == [deleted]
    assert repository.matches_paths(moved, ["e2e/**"])
    assert not repository.matches_paths(merged, ["archive/**"])


def test_cli_patterns_before_sample_full_context_exact_batches_and_resume(tmp_path):
    repo = fixture_repo(tmp_path, "repo")
    selected = change(repo, "E2E and product", {"e2e/a.spec.ts": "test", "app.py": "product"})
    change(repo, "Unrelated", {"README.md": "new"})
    config = tmp_path / "config.yaml"
    write_config(config)
    output = tmp_path / "output"
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
        "--path-pattern",
        "e2e/**",
        "--path-pattern",
        "playwright.config.*",
        "--limit",
        "1",
        "--sample",
        "1",
    ]
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert [row["sha"] for row in rows] == [selected]
    assert {f["path"] for f in rows[0]["files"]} == {"e2e/a.spec.ts", "app.py"}
    assert "product" in json.dumps(rows[0]["calls"])
    assert json.loads((output / "manifest.json").read_text())["path_patterns"] == [
        "e2e/**",
        "playwright.config.*",
    ]
    run = subprocess.run(
        command + ["--resume", "--path-pattern", "other/**"], capture_output=True, text=True
    )
    assert run.returncode == 2 and "Resume settings differ" in run.stderr
    exact = tmp_path / "selected.txt"
    exact.write_text(git(repo, "rev-parse", "HEAD") + "\n" + selected + "\n")
    command = command[:-4]
    command[command.index(str(output))] = str(tmp_path / "exact")
    run = subprocess.run(command + ["--commits-file", str(exact)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    manifest = json.loads((tmp_path / "exact/manifest.json").read_text())
    assert manifest["repositories"][0]["commits"] == [selected]
    command[command.index(str(tmp_path / "exact"))] = str(tmp_path / "empty")
    command[command.index("e2e/**")] = "no-such-path/**"
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    counts = json.loads((tmp_path / "empty/summary.json").read_text())["counts"]
    assert counts["expected"] == counts["source_errors"] == 0
    with pytest.raises(ValueError, match="relative"):
        Repository(repo).commits("HEAD", 1, None, None, path_patterns=["/absolute/**"])


def test_cli_yaml_inputs_keep_per_repo_selection_and_explicit_overrides(tmp_path):
    repos = [fixture_repo(tmp_path, name) for name in ("one", "two")]
    patterns = ["browser-scenarios/**", "acceptance/**"]
    frozen, alternate, recent, heads = {}, {}, {}, {}
    inputs = []
    for repo, pattern in zip(repos, patterns, strict=True):
        scenario = pattern.replace("**", "checkout.spec.ts")
        alternate[repo.name] = change(
            repo, "Earlier alternate scenario", {"override/account.spec.ts": "old"}
        )
        frozen[repo.name] = change(repo, "Freeze scenario", {scenario: "old"})
        recent[repo.name] = change(
            repo,
            "New scenarios after frozen revision",
            {scenario: "new", "override/account.spec.ts": "new"},
        )
        heads[repo.name] = change(repo, "Newest unrelated change", {"README.md": "latest"})
        inputs.append(
            {
                "repository": f"../{repo.name}",
                "revision": frozen[repo.name],
                "path_patterns": [pattern],
            }
        )

    config_dir = tmp_path / "plans"
    config_dir.mkdir()
    config = config_dir / "study.yaml"
    write_config(config)
    data = json.loads(config.read_text())
    data["inputs"] = inputs
    config.write_text(yaml.safe_dump(data))
    invocation_dir = tmp_path / "invocation"
    invocation_dir.mkdir()

    def scan(label, *arguments, expected_error=None):
        output = tmp_path / label
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "hekajev.cli",
                "scan",
                "--config",
                str(config),
                "--output",
                str(output),
                "--dry-run",
                "--limit",
                "1",
                *arguments,
            ],
            cwd=invocation_dir,
            capture_output=True,
            text=True,
        )
        if expected_error is not None:
            assert run.returncode == 2 and expected_error in run.stderr
            assert not (output / "manifest.json").exists()
            return None
        assert run.returncode == 0, run.stderr
        rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
        return sorted((row["repo"], row["sha"]) for row in rows)

    assert scan("yaml-only") == sorted(frozen.items())
    assert scan("revision-override", "--revision", "HEAD") == sorted(recent.items())
    assert scan("pattern-override", "--path-pattern", "override/**") == sorted(alternate.items())
    assert scan("sources-replaced", str(repos[0])) == [("one", heads["one"])]

    data["inputs"].append({**inputs[0], "revision": "HEAD"})
    config.write_text(yaml.safe_dump(data))
    scan("conflict", expected_error="Conflicting selections for repeated repositories")
