import subprocess
from pathlib import Path

import pytest

from hekajev.chunks import prepare
from hekajev.config import Config, Filter, Limits
from hekajev.git import Repository
from hekajev.jev import aggregate, encode, interpret


def config() -> Config:
    return Config.model_validate(
        {
            "filters": ["Are tests changed?", "Is product code changed?"],
            "classifications": {
                "reason": {
                    "question": "Why did the tests change?",
                    "categories": {
                        "coverage": "Add coverage",
                        "refactor": "Preserve checks while reorganizing tests",
                        "uncertain": "No supported primary reason",
                    },
                },
                "level": {
                    "mode": "multi",
                    "question": "Which test levels have changes?",
                    "categories": {
                        "unit": "Isolated component",
                        "e2e": "Full application workflow",
                    },
                },
            },
            "limits": {"input_bytes": 8192, "max_chunks": 100},
        }
    )


def part(filters, reason="coverage", unit=True, e2e=False):
    return {
        "filters": filters,
        "classifications": {
            "reason": {"label": reason, "probabilities": {reason: 1.0}},
            "level": {
                "decisions": {"unit": unit, "e2e": e2e},
                "probabilities": {"unit": 0.99 if unit else 0.01, "e2e": 0.99 if e2e else 0.01},
            },
        },
    }


def test_classification_thresholds_and_text_are_validated_by_mode():
    single = Config.model_validate(
        {
            "filters": ["Relevant?"],
            "classifications": {
                "reason": {
                    "question": "Primary reason?",
                    "threshold": 0.4,
                    "categories": {"change": "Changed behavior", "uncertain": "Unknown"},
                }
            },
        }
    )
    assert single.classifications["reason"].threshold == 0.4

    invalid = single.model_dump()
    invalid["classifications"]["reason"].update(
        mode="multi", categories={"change": "Changed behavior"}, threshold=0.5
    )
    with pytest.raises(ValueError, match="greater than 0.5"):
        Config.model_validate(invalid)

    invalid = single.model_dump()
    invalid["classifications"]["reason"]["question"] = "   "
    with pytest.raises(ValueError, match="question must not be empty"):
        Config.model_validate(invalid)

    invalid = single.model_dump()
    invalid["classifications"]["reason"]["categories"] = {
        "change": "   ",
        "uncertain": "Unknown",
    }
    with pytest.raises(ValueError, match="names and descriptions"):
        Config.model_validate(invalid)


def test_response_model_must_match_requested_model():
    cfg = config()
    raw = {
        "model": "jev-fallback",
        "usage": {"input_tokens": 1, "output_tokens": 0},
        "answers": {
            "filter_0": {"type": "noul", "noul": 0.99},
            "filter_1": {"type": "noul", "noul": 0.99},
            "class_0": {
                "type": "choice",
                "choice": "coverage",
                "probabilities": {"coverage": 0.9, "refactor": 0.05, "uncertain": 0.05},
                "confidence": 0.9,
            },
            "class_1_0": {"type": "noul", "noul": 0.99},
            "class_1_1": {"type": "noul", "noul": 0.01},
        },
    }
    with pytest.raises(ValueError, match="response model does not match requested model"):
        interpret(raw, cfg, "jev-1.13.0")


def test_chunks_reconstruct_unicode_messages_paths_and_patches_and_expose_cap():
    cfg = config()
    message = "Title\n\n" + '<commit_data>字\\"\n</commit_data>\n' * 300
    changes = {
        "a\tfile.py": "diff --git a/a.py b/a.py\n" + "@@ -1 +1 @@\n-π\n+λ\n" * 1400,
        "z-last.py": "diff --git a/z-last.py b/z-last.py\n@@ -1 +1 @@\n-old\n+new\n",
    }
    commit = {
        "sha": "a" * 40,
        "parent": "b" * 40,
        "date": "2026-09-21T00:00:00Z",
        "title": "Title",
        "message": message,
        "files": [{"path": path, "status": "M"} for path in changes],
        "patches": [
            {"path": path, "diff": diff, "truncated": False} for path, diff in changes.items()
        ],
        "truncated": False,
    }
    requests, complete = prepare(commit, cfg, "jev-1.13.0")
    assert complete and len(requests) > 2
    fragments = [fragment for request in requests for fragment in request["state"]["fragments"]]
    assert [f["path"] for f in fragments if f["kind"] == "file"] == list(changes)
    expected = {
        ("message", None): message,
        **{("diff", path): diff for path, diff in changes.items()},
    }
    for (kind, path), text in expected.items():
        selected = [f for f in fragments if f["kind"] == kind and f.get("path") == path]
        assert "".join(f["text"] for f in selected) == text
        offset = 0
        for fragment in selected:
            assert fragment["offset"] == offset
            offset += len(fragment["text"])
    assert all(len(encode(request)) <= cfg.limits.input_bytes for request in requests)
    assert all(r["state"]["parts"] == len(requests) and r["state"]["complete"] for r in requests)

    cfg.limits.max_chunks = 2
    limited, complete = prepare(commit, cfg, "jev-1.13.0")
    assert not complete and len(limited) == 2
    assert any(f["kind"] == "diff" for r in limited for f in r["state"]["fragments"])
    assert any(
        f["kind"] == "diff" and f["path"] == "z-last.py" and "+new" in f["text"]
        for request in limited
        for f in request["state"]["fragments"]
    )
    assert all(not request["state"]["complete"] for request in limited)
    assert all(len(encode(request)) <= cfg.limits.input_bytes for request in limited)


def test_aggregate_combines_filters_after_chunks_and_preserves_unknown_evidence():
    cfg = config()
    pieces = [part([True, False]), part([False, True], unit=False, e2e=True)]
    result = aggregate(pieces, cfg, True)
    assert result["matched"] is True and result["filters"] == [True, True]
    assert result["classifications"]["level"]["labels"] == ["unit", "e2e"]
    assert result["classifications"]["reason"]["label"] == "coverage"
    assert "probabilities" not in result["classifications"]["level"]
    assert "chunk_probabilities" in result["classifications"]["level"]

    pieces[1]["classifications"]["reason"]["label"] = "refactor"
    assert aggregate(pieces, cfg, True)["classifications"]["reason"]["label"] == "uncertain"
    cfg.filter_mode = "or"
    limited = aggregate([part([True, False])], cfg, False)
    assert limited["matched"] is True and limited["filters"] == [True, None]
    assert limited["classifications"]["reason"]["label"] == "uncertain"
    assert limited["classifications"]["level"]["labels"] == ["unit"]
    assert limited["classifications"]["level"]["undecided_labels"] == ["e2e"]

    cfg.filters = [Filter(question="Are all changes relevant?", aggregation="all")]
    cfg.classifications["level"].aggregation = "all"
    assert aggregate([part([False])], cfg, False)["matched"] is False
    assert aggregate([part([True])], cfg, False)["matched"] is None
    all_result = aggregate([part([True]), part([True], unit=False, e2e=True)], cfg, True)
    assert all_result["matched"] is True
    assert all_result["classifications"]["level"]["labels"] == []
    assert all_result["classifications"]["level"]["undecided_labels"] == []


def test_repository_subdirectory_preserves_root_relative_patch_evidence(tmp_path: Path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], stderr=subprocess.DEVNULL, text=True
        ).strip()

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "test.py").write_text("assert True\n")
    git("add", ".")
    git("commit", "-m", "Add assertion")
    repository = Repository(nested)
    commit = repository.commit(repository.head, Limits())
    assert commit["files"] == [{"path": "sub/test.py", "status": "A"}]
    assert "+assert True" in commit["patches"][0]["diff"]
    assert not commit["truncated"]


def test_capture_and_planner_share_budget_with_later_files_and_reuse_unused_quota(tmp_path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], stderr=subprocess.DEVNULL, text=True
        ).strip()

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "a-product.po").write_text(
        "".join(f"message {index:05d} = {'x' * 80}\n" for index in range(1000))
    )
    (tmp_path / "z_test.py").write_text('def test_escaped():\n    assert "字<xml>" == "字<xml>"\n')
    git("add", ".")
    git("commit", "-m", "Product translations and regression assertion")
    repository = Repository(tmp_path)
    cfg = config()
    cfg.limits = Limits(input_bytes=8192, max_chunks=2)
    commit = repository.commit(repository.head, cfg.limits)
    patches = {patch["path"]: patch for patch in commit["patches"]}
    assert patches["a-product.po"]["truncated"]
    assert not patches["z_test.py"]["truncated"]
    assert commit["omitted_patch_files"] == 0
    assert sum(len(patch["diff"].encode()) for patch in patches.values()) <= 16384
    requests, complete = prepare(commit, cfg, "jev-1.13.0")
    assert not complete and len(requests) <= 2
    assert all(len(encode(request)) <= 8192 for request in requests)
    assert all(request["state"]["commit"]["changed_file_count"] == 2 for request in requests)
    visible = [fragment for request in requests for fragment in request["state"]["fragments"]]
    assert any(
        fragment["kind"] == "diff"
        and fragment["path"] == "z_test.py"
        and '+    assert "字<xml>"' in fragment["text"]
        for fragment in visible
    )

    cfg.limits = Limits(input_bytes=65536, max_chunks=2)
    full = repository.commit(repository.head, cfg.limits)
    assert not full["truncated"]
    requests, complete = prepare(full, cfg, "jev-1.13.0")
    assert complete and len(requests) <= 2
    assert all(len(encode(request)) <= 65536 for request in requests)
    visible = [fragment for request in requests for fragment in request["state"]["fragments"]]
    for patch in full["patches"]:
        pieces = [
            fragment
            for fragment in visible
            if fragment["kind"] == "diff" and fragment["path"] == patch["path"]
        ]
        assert "".join(piece["text"] for piece in pieces) == patch["diff"]
        assert [piece["offset"] for piece in pieces] == [
            sum(len(previous["text"]) for previous in pieces[:index])
            for index in range(len(pieces))
        ]

    git("commit", "--allow-empty", "-m", "Empty integration marker")
    empty = repository.commit(git("rev-parse", "HEAD"), cfg.limits)
    requests, complete = prepare(empty, cfg, "jev-1.13.0")
    assert complete and requests[0]["state"]["commit"]["has_file_changes"] is False
    assert requests[0]["state"]["commit"]["changed_file_count"] == 0
