from e2e_research.common import DAY
from e2e_research.followup_stats import counts
from e2e_research.lineage import eligible_targets, followups
from e2e_research.paths import ancestry_masks, parse_changes, path_kind


def row(sha, bit, ancestors, day, labels, files, matched=True):
    return {
        "sha": sha,
        "bit": bit,
        "ancestors": ancestors,
        "timestamp": day * DAY,
        "date": "2025-01-01T00:00:00+00:00",
        "year": 2025,
        "labels": labels,
        "files": files,
        "matched": matched,
        "complete": True,
        "source_stratum": "public_oss",
        "undecided_labels": [],
    }


def test_branch_order_is_not_ancestry():
    graph = [("a", 0, []), ("b", 1, ["a"]), ("c", 2, ["a"]), ("d", 3, ["b", "c"])]
    masks = ancestry_masks(graph, ["a", "b", "c", "d"])
    assert masks == {"a": 0, "b": 1, "c": 1, "d": 7}
    a = row("a", 1, 0, 0, ["coverage"], [])
    b = row("b", 2, 1, 1, ["flakiness"], [])
    c = row("c", 4, 1, 2, ["flakiness"], [])
    d = row("d", 8, 7, 3, ["flakiness"], [])
    assert [
        r["sha"] for r, _ in eligible_targets(b, [(a, "M"), (b, "M"), (c, "M"), (d, "M")], 30)
    ] == ["d"]


def test_recreation_and_rename_break_identity_even_with_wrong_timestamp():
    a = row("a", 1, 0, 0, [], [])
    deleted = row("b", 2, 1, 200, [], [])
    recreated = row("c", 4, 3, 2, [], [])
    repair = row("d", 8, 7, 3, [], [])
    assert (
        eligible_targets(a, [(a, "M"), (deleted, "D"), (recreated, "A"), (repair, "M")], 30) == []
    )
    renamed = row("b", 2, 1, 1, [], [])
    assert [
        r["sha"] for r, _ in eligible_targets(a, [(a, "M"), (renamed, "R_end"), (repair, "M")], 30)
    ] == ["b"]


def test_same_commit_multi_file_dedup_window_and_uncertain_target():
    files = [{"path": p, "status": "M"} for p in ("e2e/a.spec.ts", "e2e/b.spec.ts")]
    a = row("a", 1, 0, 0, ["coverage"], files)
    b = row("b", 2, 1, 4, ["flakiness", "environment"], files)
    c = row("c", 4, 3, 9, ["test_bug"], files, matched=None)
    outputs, edges, _ = followups([a, b, c], 31 * DAY, "r")
    item = next(
        r for r in outputs if r["sha"] == "a" and r["scope"] == "test" and r["window"] == 30
    )
    assert item["repeat_count"] == 2 and item["repeat_labels"] == ["environment", "flakiness"]
    assert item["unknown_followup"] and item["first_maintenance_days"] == 4
    assert not any(r["sha"] == "b" and r["window"] == 30 for r in outputs)
    assert len([e for e in edges if e["source"] == "a"]) == 4


def test_maintenance_union_and_denominator_are_not_sum_of_labels():
    rows = [
        {
            "matched": True,
            "complete": True,
            "labels": ["coverage", "flakiness", "environment"],
            "undecided_labels": [],
        },
        {
            "matched": None,
            "complete": True,
            "labels": ["test_bug"],
            "undecided_labels": [],
        },
    ]
    c = counts(rows, 100)
    assert c["total"] == 100 and c["candidates"] == 2 and c["matched"] == 1
    assert c["maintenance"] == 1 and c["maintenance_without_coverage"] == 0 and c["test_bug"] == 0


def test_git_null_format_and_test_file_scope():
    sha = "a" * 40
    changes = parse_changes(
        (sha + "\0R100\0e2e/old.spec.ts\0e2e/new.spec.ts\0M\0e2e/a.spec.ts\0").encode()
    )
    assert changes[sha] == [
        {"status": "R", "path": "e2e/old.spec.ts", "new_path": "e2e/new.spec.ts"},
        {"status": "M", "path": "e2e/a.spec.ts"},
    ]
    assert path_kind("e2e/a.spec.ts") == "test"
    assert path_kind("e2e/fixtures/a.spec.ts") == "other"
    assert path_kind("e2e/helpers/util.ts") == "support_code"
    assert path_kind("tests/LoginTest.java") == "test"
