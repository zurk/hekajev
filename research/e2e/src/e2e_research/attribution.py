"""Explicit attribution patterns; unmarked commits are not an AI-free control."""

import re

AI_PATTERN = re.compile(
    r"(?im)^co-authored-by:[ \t]*(?:"
    r"Claude(?:[ \t]+(?:Code|Sonnet|Opus|Haiku|Fable)\b|(?=[ \t]*(?:<|\(|$))|\[bot\])"
    r"|Devin[ \t]+AI\b|Copilot\b|copilot-swe-agent\[bot\]|Cursor(?:[ \t]+Agent)?(?=[ \t]*<)"
    r"|anthropic-code-agent\[bot\]|aider(?=[ \t]*(?:<|\())|Mastra Code[ \t]+\().*$"
    r"|(?:generated|written|created|implemented|assisted)\s+(?:with|by|using)\s+"
    r"(?:\[[^\]]*\]\([^)]*\)\s*)?(?:claude(?:\s+code)?|github\s+copilot|copilot|cursor|codex|gemini|chatgpt|aider)"
)

TRAILER = re.compile(r"(?i)^(?:co-authored-by|signed-off-by|reviewed-by|acked-by|tested-by):")


def tools_from_markers(markers):
    text = "\n".join(markers).lower()
    tools = []
    for name, pattern in [
        ("Claude", r"claude|anthropic-code-agent"),
        ("Copilot", r"copilot"),
        ("Cursor", r"cursor"),
        ("Devin", r"devin\s+ai"),
        ("Aider", r"aider"),
        ("Mastra Code", r"mastra code"),
    ]:
        if re.search(pattern, text):
            tools.append(name)
    return tools


def strip_attribution(message):
    return "\n".join(
        line
        for line in message.splitlines()
        if not TRAILER.match(line.strip()) and not AI_PATTERN.search(line)
    ).strip()


def size_bin(files):
    if files == 1:
        return "1"
    if files <= 5:
        return "2–5"
    if files <= 20:
        return "6–20"
    return "21+"
