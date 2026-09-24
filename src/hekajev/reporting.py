"""Summaries and portable reports for an immutable commit selection."""

import html
import json
import logging
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

logger = logging.getLogger(__name__)


def _counts(records: list[dict], expected: int, source_errors: int = 0) -> dict:
    counts = Counter()
    for record in records:
        counts["total"] += 1
        counts[record["status"]] += 1
        counts["chunks"] += record.get("chunks", 0)
        incomplete = record.get("complete") is False
        counts["incomplete"] += incomplete
        if record["status"] != "ok":
            continue
        outcome = (
            "matched"
            if record.get("matched") is True
            else "rejected"
            if record.get("matched") is False
            else "undecided"
        )
        counts[outcome] += 1
        counts["matched_incomplete"] += incomplete and outcome == "matched"
    return {
        "expected": expected,
        "missing": expected - len(records),
        "source_errors": source_errors,
        **{
            key: counts[key]
            for key in (
                "total",
                "ok",
                "error",
                "preview",
                "matched",
                "rejected",
                "undecided",
                "incomplete",
                "matched_incomplete",
                "chunks",
            )
        },
    }


def _classifications(records: list[dict], config: dict) -> dict:
    matched = [r for r in records if r["status"] == "ok" and r.get("matched") is True]
    configured = config.get("classifications", {})
    names = set(configured)
    for record in matched:
        names.update(record.get("classifications", {}))
    result = {}
    for name in sorted(names):
        mode = configured.get(name, {}).get("mode", "single")
        counts, undecided = Counter(), Counter()
        uncertain = missing = 0
        for record in matched:
            classification = record.get("classifications", {}).get(name)
            if classification is None:
                missing += 1
                continue
            mode = classification.get("mode", mode)
            uncertain += bool(classification.get("uncertain"))
            if mode == "multi":
                counts.update(set(classification.get("labels", [])))
                undecided.update(set(classification.get("undecided_labels", [])))
            else:
                label = classification.get("label", "uncertain")
                counts[label] += 1
                if label == "uncertain" and not classification.get("uncertain"):
                    uncertain += 1
        denominator = len(matched)
        result[name] = {
            "mode": mode,
            "denominator": denominator,
            "counts": dict(counts.most_common()),
            "percentages": {
                label: round(100 * count / denominator, 2) if denominator else 0.0
                for label, count in counts.most_common()
            },
            "undecided_counts": dict(undecided.most_common()),
            "undecided_percentages": {
                label: round(100 * count / denominator, 2) if denominator else 0.0
                for label, count in undecided.most_common()
            },
            "uncertain": uncertain,
            "missing": missing,
        }
    return result


def summarize_records(records: list[dict], manifest: dict, *, spend: dict | None = None) -> dict:
    """Summarize final records; spend is the caller's complete request ledger."""
    repositories = manifest.get("repositories", [])
    selected = {(repo["id"], sha) for repo in repositories for sha in repo.get("commits", [])}
    seen = set()
    for record in records:
        identity = (record["repo_id"], record["sha"])
        if identity in seen:
            raise ValueError("Duplicate commit records cannot be summarized")
        if identity not in selected:
            raise ValueError("Records outside the manifest selection cannot be summarized")
        if record["status"] not in {"ok", "error", "preview"}:
            raise ValueError(f"Unknown record status: {record['status']}")
        seen.add(identity)
    config = manifest.get("config", {})
    accounting = (
        dict(spend)
        if spend is not None
        else {
            "input_tokens": 0,
            "output_tokens": 0,
            "requests": 0,
            "cost_usd": None,
            "unknown_cost_requests": 0,
            "current_run_cost_usd": None,
            "cached_commits": 0,
        }
    )
    source_errors = sum(bool(repo.get("error")) for repo in repositories)
    by_repository = {}
    for repo in repositories:
        current = [r for r in records if r["repo_id"] == repo["id"]]
        item = {
            "name": repo.get("name", repo["id"]),
            "origin": repo.get("origin"),
            "revision": repo.get("revision"),
            "error": repo.get("error"),
            "counts": _counts(current, len(repo.get("commits", [])), int(bool(repo.get("error")))),
            "classifications": _classifications(current, config),
        }
        repo_spend = accounting.get("by_repository", {}).get(repo["id"])
        if repo_spend is not None:
            item["spend"] = repo_spend
        by_repository[repo["id"]] = item
    return {
        "run_id": manifest.get("run_id"),
        "created_at": manifest.get("created_at"),
        "model": manifest.get("model"),
        "counts": _counts(records, len(selected), source_errors),
        "classifications": _classifications(records, config),
        "by_repository": by_repository,
        "spend": accounting,
        **{
            name: accounting.get(name)
            for name in (
                "input_tokens",
                "output_tokens",
                "requests",
                "cost_usd",
                "unknown_cost_requests",
                "current_run_cost_usd",
                "cached_commits",
            )
        },
    }


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.6f}"


def _terminal(value: object) -> str:
    return "".join(char if char.isprintable() else " " for char in str(value))


def format_summary(summary: dict) -> str:
    """Return a small table for stdout; operational details belong in logs."""
    headers = ["Repository", "Processed", "Matched", "Undecided", "Errors", "Incomplete"]
    rows = []
    for item in summary["by_repository"].values():
        counts = item["counts"]
        rows.append(
            [
                _terminal(item["name"]),
                f"{counts['total']}/{counts['expected']}",
                str(counts["matched"]),
                str(counts["undecided"]),
                str(counts["error"] + counts["source_errors"]),
                str(counts["incomplete"]),
            ]
        )
    counts = summary["counts"]
    rows.append(
        [
            "TOTAL",
            f"{counts['total']}/{counts['expected']}",
            str(counts["matched"]),
            str(counts["undecided"]),
            str(counts["error"] + counts["source_errors"]),
            str(counts["incomplete"]),
        ]
    )
    widths = [max(len(row[i]) for row in [headers, *rows]) for i in range(len(headers))]
    lines = [
        "  ".join(value.ljust(widths[i]) for i, value in enumerate(headers)).rstrip(),
        "  ".join("─" * width for width in widths),
        *[
            "  ".join(value.ljust(widths[i]) for i, value in enumerate(row)).rstrip()
            for row in rows
        ],
    ]
    spend = summary["spend"]
    lines.append(
        f"Estimated cost: {_money(spend.get('cost_usd'))}; "
        f"this invocation: {_money(spend.get('current_run_cost_usd'))}; "
        f"requests without a known cost: {spend.get('unknown_cost_requests', 0)}"
    )
    if counts["preview"] or counts["missing"]:
        lines.append(f"Previews: {counts['preview']}; missing: {counts['missing']}")
    return "\n".join(lines)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _commit_url(origin: str | None, sha: str) -> str | None:
    if not origin or not re.fullmatch(r"[a-fA-F0-9]{7,64}", sha):
        return None
    if origin.startswith("git@") and ":" in origin:
        host, path = origin[4:].split(":", 1)
        origin = f"https://{host}/{path}"
    try:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            return None
    except ValueError:
        return None
    routes = {"github.com": "commit", "gitlab.com": "-/commit", "bitbucket.org": "commits"}
    route = routes.get(parsed.hostname.lower())
    if route is None:
        return None
    path = parsed.path.rstrip("/").removesuffix(".git")
    return urlunsplit(("https", parsed.netloc, f"{path}/{route}/{quote(sha)}", "", ""))


def _bars(classifications: dict) -> str:
    sections = []
    for name, item in classifications.items():
        bars = []
        for label, count in item["counts"].items():
            percent = item["percentages"][label]
            width = max(0.0, min(100.0, percent))
            bars.append(
                f'<div class="bar-label"><span>{_escape(label)}</span>'
                f"<span>{count} <small>{percent:.1f}%</small></span></div>"
                f'<div class="track"><div style="width:{width:.2f}%"></div></div>'
            )
        undecided = ", ".join(
            f"{label}: {count}" for label, count in item["undecided_counts"].items()
        )
        notes = (
            f"{item['denominator']} matched commits · {item['mode']} · "
            f"{item['uncertain']} uncertain · {item['missing']} missing classification"
        )
        if item["mode"] == "multi":
            notes += ". Multiple labels can make percentages sum above 100%."
        if undecided:
            notes += f" Undecided labels: {undecided}."
        sections.append(
            f'<article class="panel"><h3>{_escape(name)}</h3>'
            f'<p class="muted">{_escape(notes)}</p>'
            + ("".join(bars) or '<p class="muted">No confirmed labels.</p>')
            + "</article>"
        )
    return "".join(sections)


def _record_details(record: dict, repositories: dict) -> str:
    repo = repositories.get(record["repo_id"], {})
    sha = str(record["sha"])
    url = _commit_url(repo.get("origin"), sha)
    sha_html = _escape(sha[:12])
    if url:
        sha_html = f'<a href="{_escape(url)}" target="_blank" rel="noreferrer">{sha_html}</a>'
    if record["status"] == "ok":
        outcome = (
            "matched"
            if record.get("matched") is True
            else "rejected"
            if record.get("matched") is False
            else "undecided"
        )
    else:
        outcome = record["status"]
    complete = "incomplete evidence" if record.get("complete") is False else ""
    title = record.get("title", "")
    if not title:
        calls = record.get("calls", [])
        payload = calls[0].get("input", {}) if calls and isinstance(calls[0], dict) else {}
        state = payload.get("state", {}) if isinstance(payload, dict) else {}
        title = state.get("title", "") if isinstance(state, dict) else ""
    details = {
        key: record[key]
        for key in (
            "status",
            "matched",
            "complete",
            "chunks",
            "filters",
            "classifications",
            "error",
            "elapsed_seconds",
            "omitted_bytes",
            "omitted_chunks",
        )
        if key in record
    }
    body = _escape(json.dumps(details, ensure_ascii=False, indent=2))
    return (
        '<details class="commit"><summary>'
        f'<span class="badge {outcome}">{outcome}</span> '
        f"<span>{_escape(repo.get('name', record.get('repo', record['repo_id'])))}</span> "
        f"<code>{sha_html}</code> <span>{_escape(title)}</span> "
        f'<span class="warning">{complete}</span></summary><pre>{body}</pre></details>'
    )


def _html_report(
    summary: dict,
    records: list[dict],
    manifest: dict,
    *,
    batch_links: str | None = None,
) -> str:
    counts, spend = summary["counts"], summary["spend"]
    cards = [
        ("Processed", f"{counts['total']} / {counts['expected']}"),
        ("Matched", counts["matched"]),
        ("Undecided", counts["undecided"]),
        ("Incomplete evidence", counts["incomplete"]),
        ("Estimated cost", _money(spend.get("cost_usd"))),
    ]
    metrics = "".join(
        f'<div class="metric"><span>{_escape(label)}</span><strong>{_escape(value)}</strong></div>'
        for label, value in cards
    )
    rows = []
    for item in summary["by_repository"].values():
        c = item["counts"]
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{_escape(value)}</td>"
                for value in (
                    item["name"],
                    f"{c['total']}/{c['expected']}",
                    c["matched"],
                    c["rejected"],
                    c["undecided"],
                    c["error"] + c["source_errors"],
                    c["incomplete"],
                    c["missing"],
                )
            )
            + "</tr>"
        )
        if item.get("error"):
            rows.append(f'<tr><td colspan="8" class="warning">{_escape(item["error"])}</td></tr>')
    repository_table = (
        '<div class="table-wrap"><table><thead><tr>'
        + "".join(
            f"<th>{label}</th>"
            for label in (
                "Repository",
                "Processed",
                "Matched",
                "Rejected",
                "Undecided",
                "Errors",
                "Incomplete",
                "Missing",
            )
        )
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )
    notices = [
        f"{counts['ok']} analyzed; {counts['preview']} previews; {counts['error']} commit errors; "
        f"{counts['source_errors']} source errors; {counts['missing']} missing commits; "
        f"{counts['chunks']} chunks.",
        f"Estimated cost this invocation: {_money(spend.get('current_run_cost_usd'))}. "
        f"{spend.get('requests', 0)} requests; {spend.get('input_tokens', 0)} input tokens; "
        f"{spend.get('output_tokens', 0)} output tokens; "
        f"{spend.get('unknown_cost_requests', 0)} requests without a known cost. "
        f"{spend.get('cached_commits', 0)} cached commits.",
        "Category percentages use all matched commits. Labels count commits, "
        "not work hours or verified defects. Missing patch evidence may affect decisions.",
    ]
    if spend.get("unknown_cost_requests", 0):
        notices.append("The cost estimate omits requests with unknown charges.")
    notes = "".join(f"<p>{_escape(note)}</p>" for note in notices)
    if batch_links is None:
        repositories = {repo["id"]: repo for repo in manifest.get("repositories", [])}
        details = "".join(_record_details(record, repositories) for record in records)
        provenance = {
            key: manifest[key]
            for key in (
                "run_id",
                "created_at",
                "model",
                "price_per_million",
                "config",
                "repositories",
            )
            if key in manifest
        }
        decisions = (
            "<section><h2>Commit decisions</h2>"
            '<p class="muted">Open a commit to see its scores and available evidence.</p>'
            + (details or '<p class="muted">No records yet.</p>')
            + "</section>"
        )
        provenance_title = "Run configuration and selected commits"
        footer = "results.jsonl keeps the full requests and answers."
    else:
        provenance = _compact_provenance(manifest)
        statistics = _escape(json.dumps(summary, ensure_ascii=False, indent=2))
        decisions = (
            "<section><h2>Batch reports</h2>"
            '<p class="muted">Batch directories keep each commit decision and its evidence. '
            "Totals include every recorded result; pending and failed commits appear above.</p>"
            + (batch_links or '<p class="muted">No batch reports are available yet.</p>')
            + '</section><details class="provenance">'
            "<summary>Complete aggregate statistics</summary>"
            f"<pre>{statistics}</pre></details>"
        )
        provenance_title = "Corpus configuration and provenance"
        footer = "Batch outputs keep full requests, answers, and selected SHAs."
    metadata = _escape(json.dumps(provenance, ensure_ascii=False, indent=2))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>HekaJev report</title><style>
:root{{color-scheme:light;--ink:#173335;--muted:#526967;--accent:#157a6e;--border:#d5e0da}}
*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f0;color:var(--ink);
font:15px/1.55 system-ui,-apple-system,sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:48px 28px}}
header{{border-bottom:2px solid var(--ink);padding-bottom:22px;margin-bottom:28px}}
.eyebrow{{text-transform:uppercase;letter-spacing:.18em;font-size:12px;color:var(--accent)}}
h1{{font-size:clamp(30px,5vw,48px);line-height:1.1;margin:10px 0 16px;letter-spacing:-.035em}}
h2{{font-size:22px;margin:36px 0 16px}}h3{{margin:0 0 10px;font-size:19px}}
p{{margin:8px 0}}.muted,small{{color:var(--muted)}}.metrics{{display:flex;gap:12px;flex-wrap:wrap}}
.metric{{flex:1;min-width:150px;background:#fff;border:1px solid var(--border);padding:18px}}
.metric span{{display:block;font-size:12px;color:var(--muted)}}
.metric strong{{display:block;font-size:25px;font-variant-numeric:tabular-nums;margin-top:4px}}
.table-wrap{{overflow:auto;border:1px solid var(--border);background:#fff}}
table{{border-collapse:collapse;width:100%;text-align:left;white-space:nowrap}}
th,td{{padding:12px 14px;border-bottom:1px solid var(--border)}}
th{{font-size:12px;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}}
.panel{{background:#fff;border:1px solid var(--border);padding:22px;overflow-wrap:anywhere}}
.panel .muted{{font-size:12px;margin-bottom:20px}}
.bar-label{{display:flex;justify-content:space-between;gap:12px}}
.bar-label small{{margin-left:6px}}.track{{height:7px;background:#e7ede7;margin:7px 0 17px}}
.track div{{height:100%;background:var(--accent)}}.notes{{font-size:13px;color:var(--muted)}}
.commit{{border-bottom:1px solid var(--border);padding:12px 0}}
summary{{cursor:pointer;overflow-wrap:anywhere}}
summary span,summary code{{margin-right:8px}}
.badge{{display:inline-block;padding:2px 7px;border-radius:3px;
font-size:11px;background:#e5ebe5}}.matched{{background:#d5eee2;color:#155044}}
.error,.undecided{{background:#fff0d7;color:#8e4b08}}.warning{{color:#8e4b08}}
a{{color:var(--accent);text-underline-offset:3px}}pre{{padding:18px;background:#e9eee7;
font-size:12px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;max-height:650px}}
.provenance{{margin-top:32px}}footer{{margin-top:40px;font-size:12px;color:var(--muted)}}
@media print{{body{{background:#fff}}main{{padding:0}}.commit pre{{max-height:none}}}}
</style></head><body><main>
<header><div class="eyebrow">HekaJev / research report</div>
<h1>What changed in the commits?</h1>
<p class="muted">{_escape(summary.get("model") or "Unknown model")} ·
{_escape(summary.get("created_at") or "Undated run")}</p></header>
<div class="metrics">{metrics}</div><section><h2>Repositories</h2>{repository_table}</section>
<section><h2>Classifications</h2><div class="grid">
{_bars(summary["classifications"])}</div></section>
<section class="notes"><h2>Coverage and cost</h2>{notes}</section>
{decisions}
<details class="provenance"><summary>{provenance_title}</summary>
<pre>{metadata}</pre></details>
<footer>Self-contained local report.
{footer}</footer>
</main></body></html>"""


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_reports(
    output: Path, records: list[dict], manifest: dict, *, spend: dict | None = None
) -> dict:
    """Atomically replace each report; never rewrite the caller's event data."""
    summary = summarize_records(records, manifest, spend=spend)
    output.mkdir(parents=True, exist_ok=True)
    _atomic_write(output / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(output / "report.html", _html_report(summary, records, manifest))
    logger.info("Reports written to %s", output)
    return summary


def _compact_provenance(value):
    if isinstance(value, dict):
        return {
            key: {"count": len(item), "details": "SHA list omitted from HTML provenance"}
            if key == "commits" and isinstance(item, list)
            else _compact_provenance(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_compact_provenance(item) for item in value]
    return value


def write_summary_reports(
    output: Path,
    summary: dict,
    provenance: dict,
    *,
    batch_reports: list[dict],
) -> dict:
    """Write preaggregated statistics without reading or embedding commit evidence.

    Each batch link has a relative ``path`` inside output and a display ``label``.
    The caller accounts for the complete frozen population in ``summary``;
    this function preserves every supplied statistic and does not combine runs.
    """
    root = output.resolve()
    links = []
    for batch in batch_reports:
        path = batch["path"]
        if not isinstance(path, str) or not path or "\\" in path or "\0" in path:
            raise ValueError("Batch report paths must be relative paths inside output")
        relative = Path(path)
        if relative.is_absolute() or not (root / relative).resolve().is_relative_to(root):
            raise ValueError("Batch report paths must be relative paths inside output")
        url = quote(relative.as_posix(), safe="/")
        links.append(f'<li><a href="{_escape(url)}">{_escape(batch["label"])}</a></li>')
    batch_links = "<ul>" + "".join(links) + "</ul>" if links else ""
    report = _html_report(summary, [], provenance, batch_links=batch_links)
    output.mkdir(parents=True, exist_ok=True)
    _atomic_write(output / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(output / "report.html", report)
    logger.info("Corpus summary reports written to %s", output)
    return summary
