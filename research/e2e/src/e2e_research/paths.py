"""Path parsing and conservative candidate ancestry, without model inference."""

import re
from collections import Counter
from pathlib import Path

CODE_EXTENSIONS = {
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".py",
    ".java",
    ".kt",
    ".cs",
    ".go",
    ".rb",
}

EXCLUDED_PARTS = {
    "node_modules",
    "vendor",
    "__snapshots__",
    "snapshots",
    "screenshots",
    "fixtures",
    "__fixtures__",
    "test-data",
    "testdata",
    "assets",
    "dist",
    "generated",
}

TEST_NAME = re.compile(
    r"(?:\.(?:spec|test|e2e|cy)\.[^.]+$|(?:^|/)test_[^/]+\.py$|_test\.(?:py|go)\Z|(?:^|/)(?:Test[^/]*|[^/]+Tests?)\.(?:java|kt|cs)\Z)",
    re.I,
)


def path_kind(path):
    p = Path(path)
    parts = {s.lower() for s in p.parts}
    if p.suffix.lower() not in CODE_EXTENSIONS or parts & EXCLUDED_PARTS:
        return "other"
    if TEST_NAME.search(path):
        return "test"
    return "support_code"


def parse_changes(data):
    tokens = iter(data.decode("utf-8", errors="surrogateescape").split("\0"))
    result = {}
    current = None
    for token in tokens:
        if not token:
            continue
        if re.fullmatch("[0-9a-f]{40}", token):
            current = token
            result[current] = []
        else:
            assert current is not None, token
            path = next(tokens)
            if token[0] in "RC":
                new = next(tokens)
                result[current].append({"status": token[0], "path": path, "new_path": new})
            else:
                assert token[0] in "ADMTUXB", token
                result[current].append({"status": token[0], "path": path})
    return result


def ancestry_masks(graph, selected):
    """Retain only candidate ancestry; release parents after their final child."""
    bits = {sha: 1 << i for i, sha in enumerate(selected)}
    remaining = Counter(p for _, _, parents in graph for p in parents)
    live, result = {}, {}
    for sha, _, parents in graph:
        mask = 0
        for parent in parents:
            mask |= live[parent]
        if sha in bits:
            result[sha] = mask
        live[sha] = mask | bits.get(sha, 0)
        for parent in parents:
            remaining[parent] -= 1
            if not remaining[parent]:
                del live[parent]
    assert len(result) == len(selected)
    return result
