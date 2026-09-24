from collections import deque

from hekajev.config import Config
from hekajev.jev import encode, questions


def _text_prefix(text: str, budget: int) -> int:
    low, high = 0, min(len(text), budget)
    overhead = len(encode({"text": ""}))
    while low < high:
        middle = (low + high + 1) // 2
        if len(encode({"text": text[:middle]})) - overhead <= budget:
            low = middle
        else:
            high = middle - 1
    if low == len(text):
        return low
    boundary = text.rfind("\n@@", 0, low)
    if boundary < low // 2:
        boundary = text.rfind("\n", 0, low)
    return boundary + 1 if boundary >= low // 2 else low


def _elements(commit: dict, available: int, max_chunks: int):
    patches = {item["path"]: item for item in commit["patches"]}
    descriptors = [{"kind": "file", **file} for file in commit["files"]]
    overhead = sum(len(encode(file)) + 1 for file in descriptors)
    metadata_sizes = [
        len(
            encode(
                {
                    "kind": "diff",
                    "path": path,
                    "offset": len(patch["diff"]),
                    "text": "",
                    "source_truncated": patch["truncated"],
                }
            )
        )
        + 1
        for path, patch in patches.items()
    ]
    overhead += sum(metadata_sizes)
    fragment_overhead = max(metadata_sizes, default=0)
    fair_budget = (available * max_chunks - overhead) // (2 * max(1, len(patches)))
    block_budget = max(6, available // 2 - fragment_overhead)
    preview_budget = max(1, min(2048, block_budget, fair_budget))
    remaining = deque()
    for descriptor in descriptors:
        yield descriptor
        patch = patches.get(descriptor["path"])
        if patch is None:
            continue
        text = patch["diff"]
        size = _text_prefix(text, preview_budget)
        fragment = {
            "kind": "diff",
            "path": patch["path"],
            "offset": 0,
            "source_truncated": patch["truncated"],
        }
        yield {**fragment, "text": text[:size]}
        if size < len(text):
            remaining.append((fragment, text, size))
    # Every representable file gets a preview before long patches receive more capacity.
    while remaining:
        fragment, text, offset = remaining.popleft()
        size = _text_prefix(text[offset:], block_budget)
        yield {**fragment, "offset": offset, "text": text[offset : offset + size]}
        offset += size
        if offset < len(text):
            remaining.append((fragment, text, offset))
    # Release-note messages must not consume the cap before changed code is seen.
    yield {"kind": "message", "offset": 0, "text": commit["message"]}


def prepare(commit: dict, config: Config, model: str) -> tuple[list[dict], bool]:
    question_map = questions(config)
    message = commit["message"]
    context = {
        "message_head": message[:500],
        "message_tail": message[-500:] if len(message) > 500 else "",
    }
    identity = {key: commit[key] for key in ("sha", "parent", "date")}
    identity["has_file_changes"] = bool(commit["files"])
    identity["changed_file_count"] = len(commit["files"])
    if commit.get("is_merge"):
        identity["merge_comparison"] = "Changes relative to the first parent of a merge commit"
    identity["title"] = commit["title"][:300]
    identity["title_truncated"] = len(commit["title"]) > 300

    def payload(fragments: list[dict], part: int) -> dict:
        return {
            "model": model,
            "questions": question_map,
            "state": {
                "commit": identity,
                "context": context,
                "fragments": fragments,
                "part": part,
                "parts": config.limits.max_chunks,
                "complete": False,
            },
        }

    limit = config.limits.input_bytes
    if len(encode(payload([], 1))) + 256 > limit:
        raise ValueError("Questions and shared context exceed input_bytes; increase the limit")
    available = limit - len(encode(payload([], config.limits.max_chunks)))
    elements = _elements(commit, available, config.limits.max_chunks)
    requests = []
    fragments: list[dict] = []
    capped = False
    for element in elements:
        while True:
            part = len(requests) + 1
            if len(encode(payload([*fragments, element], part))) <= limit:
                fragments.append(element)
                break
            text = element.get("text", "")
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                candidate = [*fragments, {**element, "text": text[:middle]}]
                if len(encode(payload(candidate, part))) <= limit:
                    low = middle
                else:
                    high = middle - 1
            if low == 0:
                if fragments:
                    requests.append(payload(fragments, part))
                    fragments = []
                    if len(requests) == config.limits.max_chunks:
                        capped = True
                        break
                    continue
                if "text" not in element:
                    raise ValueError("A file descriptor exceeds input_bytes; increase the limit")
                raise ValueError("No room for evidence within input_bytes")
            # Keep hunk/line boundaries when possible; generated single lines must still progress.
            boundary = text.rfind("\n@@", 0, low)
            if boundary < low // 2:
                boundary = text.rfind("\n", 0, low)
            size = boundary + 1 if boundary >= low // 2 else low
            requests.append(payload([*fragments, {**element, "text": text[:size]}], part))
            fragments = []
            element = {**element, "text": text[size:], "offset": element["offset"] + size}
            if len(requests) == config.limits.max_chunks:
                capped = True
                break
        if capped:
            break
    if fragments:
        requests.append(payload(fragments, len(requests) + 1))
    complete = not capped and not commit["truncated"]
    for request in requests:
        request["state"]["parts"] = len(requests)
        request["state"]["complete"] = complete
        assert len(encode(request)) <= limit
    return requests, complete
