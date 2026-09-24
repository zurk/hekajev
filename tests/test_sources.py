import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from hekajev import sources

URLS = (
    "https://github.com/Example/project.git",
    "ssh://git@github.com/Example/project.git",
    "git@github.com:Example/project.git",
)


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def repositories(tmp_path: Path) -> tuple[Path, Path]:
    working = tmp_path / "working"
    working.mkdir()
    git(working, "init", "-b", "main")
    git(working, "config", "user.name", "Fixture")
    git(working, "config", "user.email", "fixture@example.invalid")
    (working / "code.txt").write_text("first\n")
    git(working, "add", "code.txt")
    git(working, "commit", "-m", "First")
    remote = tmp_path / "remote.git"
    git(working, "clone", "--bare", str(working), str(remote))
    return working, remote


def local_transport(monkeypatch, remote: Path, *, fail_first: bool = False):
    real_run = subprocess.run
    commands = []
    secret = "never-print-transport-fixture-key"

    def run(command, **kwargs):
        nonlocal fail_first
        commands.append(tuple(command))
        if fail_first and "clone" in command:
            fail_first = False
            temporary = Path(command[-1])
            assert temporary.name.startswith(".")
            (temporary / "HEAD").write_text("interrupted clone")
            return subprocess.CompletedProcess(command, 128, b"", secret.encode())
        # Only transport is redirected; real Git receives the clone/fetch/ref arguments unchanged.
        options = ["-c", "protocol.file.allow=always"]
        for url in URLS:
            options.extend(["-c", f"url.{remote.as_uri()}.insteadOf={url}"])
        return real_run([command[0], *options, *command[1:]], **kwargs)

    monkeypatch.setattr(sources.subprocess, "run", run)
    return commands, secret


def test_remote_cache_refresh_resume_and_local_working_tree(tmp_path, monkeypatch):
    working, remote = repositories(tmp_path)
    cache = tmp_path / "cache"
    commands, _ = local_transport(monkeypatch, remote)
    first = sources.prepare_source(URLS[0], cache)
    original_sha = git(first.path, "rev-parse", "HEAD")
    assert git(first.path, "rev-parse", "--is-bare-repository") == "true"
    assert original_sha == git(remote, "rev-parse", "HEAD")

    git(working, "branch", "-m", "trunk")
    (working / "code.txt").write_text("second\n")
    git(working, "commit", "-am", "Second")
    git(working, "tag", "v2")
    git(working, "push", str(remote), "trunk", "--tags")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
    git(remote, "update-ref", "-d", "refs/heads/main")
    (working / "code.txt").write_text("uncommitted work\n")
    (working / "notes.txt").write_text("untracked work\n")
    before = git(working, "status", "--porcelain")
    local = sources.prepare_source(str(working), cache)
    assert local.path == working.resolve()

    commands.clear()
    resumed = sources.prepare_source(URLS[1], cache, refresh=False)
    assert resumed.id == first.id and resumed.path == first.path
    assert not any(set(command) & {"clone", "fetch", "ls-remote"} for command in commands)
    assert git(resumed.path, "rev-parse", "HEAD") == original_sha

    refreshed = sources.prepare_source(URLS[2], cache)
    assert refreshed.id == first.id and refreshed.path == first.path
    assert git(refreshed.path, "rev-parse", "HEAD") == git(remote, "rev-parse", "HEAD")
    assert git(refreshed.path, "symbolic-ref", "HEAD") == "refs/heads/trunk"
    assert git(refreshed.path, "for-each-ref", "--format=%(refname)") == (
        "refs/heads/trunk\nrefs/tags/v2"
    )
    assert git(working, "status", "--porcelain") == before
    assert git(working, "symbolic-ref", "HEAD") == "refs/heads/trunk"
    assert (working / "code.txt").read_text() == "uncommitted work\n"
    assert (working / "notes.txt").read_text() == "untracked work\n"
    assert len(list(cache.glob("*.git"))) == 1


def test_failed_clone_is_cleaned_and_can_retry(tmp_path, monkeypatch, caplog):
    _, remote = repositories(tmp_path)
    cache = tmp_path / "cache"
    commands, secret = local_transport(monkeypatch, remote, fail_first=True)
    with pytest.raises(sources.SourceError, match="Git exit 128") as error:
        sources.prepare_source(URLS[0], cache)
    assert not list(cache.glob("*.git"))
    assert all(path.suffix == ".lock" for path in cache.iterdir())
    assert secret not in str(error.value) + caplog.text
    source = sources.prepare_source(URLS[0], cache)
    assert git(source.path, "rev-parse", "HEAD") == git(remote, "rev-parse", "HEAD")
    assert sum("clone" in command for command in commands) == 2
    assert {path.suffix for path in cache.iterdir()} == {".git", ".lock"}


def test_credentials_and_unsafe_urls_never_reach_git(tmp_path, monkeypatch, caplog):
    working, _ = repositories(tmp_path)
    cache = tmp_path / "cache"
    secret = "never-print-url-fixture-key"
    urls = [
        f"https://token:{secret}@github.com/Example/project.git",
        f"ssh://git:{secret}@github.com/Example/project.git",
        f"https://github.com/Example/project.git?access_token={secret}",
        f"--upload-pack={secret}",
        "ext::sh forbidden",
        "file:///tmp/repository.git",
        "git@host:-option",
        "https://github.com/Example/../project.git",
    ]
    with monkeypatch.context() as isolated:

        def forbidden(*args, **kwargs):
            pytest.fail("Invalid source reached Git")

        isolated.setattr(sources, "_git", forbidden)
        for url in urls:
            with pytest.raises(sources.SourceError) as error:
                sources.prepare_source(url, cache)
            assert secret not in str(error.value)
    assert not cache.exists()
    git(working, "remote", "add", "origin", urls[0])
    source = sources.prepare_source(str(working), cache)
    assert source.origin == "https://github.com/Example/project"
    assert secret not in json.dumps(dataclasses.asdict(source), default=str) + caplog.text
