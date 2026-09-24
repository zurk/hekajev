import gzip
import hashlib
import json
import logging
import random
import re
import subprocess
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path

from hekajev import __version__
from hekajev.chunks import prepare
from hekajev.config import Config, Input
from hekajev.git import Repository
from hekajev.jev import (
    ENDPOINT,
    PRICES,
    PROMPT_VERSION,
    Jev,
    ProviderError,
    aggregate,
    encode,
    interpret,
)
from hekajev.reporting import summarize_records, write_reports
from hekajev.sources import _cache_lock, prepare_source

logger = logging.getLogger(__name__)


class _SourceIdentityMismatch(ValueError):
    pass


def _open_jsonl(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open(encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for candidate in (path.with_suffix(path.suffix + ".gz"), path):
        if not candidate.exists():
            continue
        with _open_jsonl(candidate) as stream:
            for number, line in enumerate(stream, 1):
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("Expected object")
                    rows.append(value)
                except ValueError:
                    raise ValueError(
                        f"Malformed JSONL at {candidate.name}:{number}; repair before resuming"
                    ) from None
    return rows


def read_records(path: Path, manifest: dict) -> dict[tuple[str, str], dict]:
    records = {}
    for record in read_jsonl(path):
        if record.get("run_id") != manifest["run_id"]:
            raise ValueError("Output contains records from another run")
        records[record["repo_id"], record["sha"]] = record
    return records


def save_json(path: Path, data: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def accounting(events: list[dict], *, current_start: int = 0, cached: int = 0) -> dict:
    def total(rows):
        known = [event for event in rows if event.get("usage") is not None]
        priced = [event for event in known if event.get("price_per_million") is not None]
        price_known = not rows or len(priced) > 0
        return {
            "requests": len(rows),
            "input_tokens": sum(event["usage"]["input_tokens"] for event in known),
            "output_tokens": sum(event["usage"]["output_tokens"] for event in known),
            "cost_usd": sum(
                event["usage"]["input_tokens"] * event["price_per_million"] / 1_000_000
                for event in priced
            )
            if price_known
            else None,
            "unknown_cost_requests": len(rows) - len(priced),
        }

    result = total(events)
    result["current_run_cost_usd"] = total(events[current_start:])["cost_usd"]
    result["cached_commits"] = cached
    result["by_repository"] = {
        repo: total([event for event in events if event["repo_id"] == repo])
        for repo in {event["repo_id"] for event in events}
    }
    return result


class Ledger:
    def __init__(self, path: Path, run_id: str, price: float | None):
        self.path, self.run_id, self.price = path, run_id, price
        self.events = read_jsonl(path)
        if any(event.get("run_id") != run_id for event in self.events):
            raise ValueError("Usage ledger belongs to another run")
        self.start = len(self.events)
        self.lock = threading.Lock()

    def record(self, repo_id: str, sha: str, part: int, event: dict):
        row = {
            "run_id": self.run_id,
            "repo_id": repo_id,
            "sha": sha,
            "part": part,
            "time": datetime.now(UTC).isoformat(),
            "price_per_million": self.price,
            **event,
        }
        with self.lock, self.path.open("a") as stream:
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            self.events.append(row)

    def snapshot(self, cached: int) -> dict:
        with self.lock:
            return accounting(self.events.copy(), current_start=self.start, cached=cached)


def scan(
    sources: list[str],
    config: Config,
    output: Path,
    *,
    key: str | None,
    model: str,
    revision: str | None,
    limit: int,
    sample: int | None,
    seed: int,
    since: str | None,
    until: str | None,
    workers: int,
    resume: bool,
    dry_run: bool,
    cache_dir: Path,
    progress_every: int,
    progress_interval: float,
    price_per_million: float | None,
    commits_file: Path | None = None,
    include_merges: bool = False,
    requests_per_second: float | None = None,
    path_patterns: list[str] | None = None,
) -> dict:
    if revision is not None and revision.startswith("-"):
        raise ValueError("Revision cannot start with '-'")
    inputs = [Input(repository=source) for source in sources] if sources else config.inputs
    if not inputs:
        raise ValueError("Provide repository sources on the CLI or inputs in YAML")
    inputs = [
        Input(
            repository=(
                source.repository
                if source.is_remote
                else str(Path(source.repository).expanduser().absolute())
            ),
            revision=revision if revision is not None else source.revision,
            path_patterns=list(
                dict.fromkeys(path_patterns if path_patterns is not None else source.path_patterns)
            ),
        )
        for source in inputs
    ]
    selected_commits = None
    if commits_file is not None:
        if len(inputs) != 1 or sample is not None or since is not None or until is not None:
            raise ValueError("--commits-file requires one source and no sampling/date filters")
        selected_commits = [
            line.strip() for line in commits_file.read_text().splitlines() if line.strip()
        ]
        if not selected_commits or any(
            not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) for sha in selected_commits
        ):
            raise ValueError("Commit file must contain full hexadecimal SHAs, one per line")
        if len(set(selected_commits)) != len(selected_commits):
            raise ValueError("Commit file contains duplicate SHAs")
    price = PRICES.get(model) if price_per_million is None else price_per_million
    specification = {
        "version": __version__,
        "prompt_version": PROMPT_VERSION,
        "source_keys": [
            hashlib.sha256(source.repository.encode()).hexdigest() for source in inputs
        ],
        "inputs": [source.model_dump() for source in inputs],
        "revision": revision,
        "limit": limit,
        "sample": sample,
        "seed": seed,
        "since": since,
        "until": until,
        "config": config.model_dump(exclude={"inputs"}),
        "model": model,
        "endpoint": ENDPOINT,
        "dry_run": dry_run,
        "price_per_million": price,
        "selected_commits": selected_commits,
        "include_merges": include_merges,
        "path_patterns": path_patterns,
    }
    run_id = hashlib.sha256(encode(specification)).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    with _cache_lock(output / ".lock"):
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if not resume:
                raise ValueError("Output already exists; use --resume or a new directory")
            if manifest["run_id"] != run_id:
                raise ValueError("Resume settings differ from the manifest")
        else:
            if (output / "results.jsonl").exists() or (output / "usage.jsonl").exists():
                raise ValueError("Results exist without a manifest")
            manifest = {
                "run_id": run_id,
                "created_at": datetime.now(UTC).isoformat(),
                **specification,
                "repositories": [],
            }
        previous = {entry["source_key"]: entry for entry in manifest["repositories"]}
        previous_by_id = {entry["id"]: entry for entry in manifest["repositories"]}
        repositories, repo_objects, seen, conflicts = [], {}, {}, []
        for source_input, source_key in zip(inputs, specification["source_keys"], strict=True):
            old = previous.get(source_key)
            try:
                source = prepare_source(source_input.repository, cache_dir, refresh=not resume)
                if old and not old.get("error") and old["id"] != source.id:
                    raise _SourceIdentityMismatch("Source identity differs from the manifest")
                canonical_previous = previous_by_id.get(source.id)
                if canonical_previous and not canonical_previous.get("error"):
                    old = canonical_previous
                if source.id in seen:
                    selection = (source_input.revision, source_input.path_patterns)
                    if seen[source.id] != selection:
                        conflicts.append(source.name)
                    logger.info("Ignoring repeated source %s", source.name)
                    continue
                seen[source.id] = (source_input.revision, source_input.path_patterns)
                repository = Repository(source.path)
                entry = {
                    "id": source.id,
                    "name": source.name,
                    "identity": source.identity,
                    "origin": source.origin,
                    "path": str(source.path),
                    "source_key": source_key,
                    "path_patterns": source_input.path_patterns,
                }
                if old and "commits" in old and not old.get("error"):
                    entry.update({k: old[k] for k in ("commits", "revision", "candidate_count")})
                else:
                    candidates = (
                        [
                            sha
                            for sha in selected_commits
                            if not source_input.path_patterns
                            or repository.matches_paths(sha, source_input.path_patterns)
                        ]
                        if selected_commits is not None
                        else repository.commits(
                            source_input.revision,
                            limit,
                            since,
                            until,
                            include_merges=include_merges,
                            path_patterns=source_input.path_patterns,
                        )
                    )
                    selected = (
                        set(random.Random(seed).sample(candidates, min(sample, len(candidates))))
                        if sample
                        else set(candidates)
                    )
                    entry.update(
                        commits=[sha for sha in candidates if sha in selected],
                        candidate_count=len(candidates),
                        revision=repository.run(
                            "rev-parse", "--verify", source_input.revision
                        ).strip()
                        if ".." not in source_input.revision
                        else source_input.revision,
                    )
                entry["shallow"] = repository.shallow
                if repository.shallow:
                    logger.warning("%s has shallow history; boundary commits may fail", source.name)
                repo_objects[source.id] = repository
            except _SourceIdentityMismatch:
                raise
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                entry = (
                    dict(old)
                    if old
                    else {
                        "id": source_key[:16],
                        "name": "unavailable source",
                        "source_key": source_key,
                        "commits": [],
                    }
                )
                entry["error"] = str(exc)
                logger.error("Source preparation failed: %s", exc)
            repositories.append(entry)
        if conflicts:
            raise ValueError(
                "Conflicting selections for repeated repositories: " + ", ".join(conflicts)
            )
        manifest["repositories"] = repositories
        records = read_records(output / "results.jsonl", manifest)
        # Validate membership before any new paid requests.
        summarize_records(list(records.values()), manifest)
        save_json(manifest_path, manifest)
        jobs = [
            (entry, sha)
            for entry in repositories
            if not entry.get("error")
            for sha in entry["commits"]
            if records.get((entry["id"], sha), {}).get("status") not in ("ok", "preview")
        ]
        cached = sum(record["status"] in ("ok", "preview") for record in records.values())
        ledger = Ledger(output / "usage.jsonl", run_id, price)
        client = (
            Jev(key, requests_per_second=requests_per_second)
            if key is not None and jobs and not dry_run
            else None
        )
        stop = threading.Event()
        expected = sum(len(entry["commits"]) for entry in repositories)
        if not dry_run and jobs and client is None:
            raise ValueError("Set TYPESAFE_API_KEY")

        def evaluate(job):
            entry, sha = job
            start = time.monotonic()
            record = {
                "run_id": run_id,
                "repo_id": entry["id"],
                "repo": entry["name"],
                "sha": sha,
                "calls": [],
                "complete": False,
                "chunks": 0,
            }
            try:
                if stop.is_set():
                    raise ValueError("Stopped before analysis")
                commit = repo_objects[entry["id"]].commit(
                    sha, config.limits, include_merges=include_merges
                )
                payloads, complete = prepare(commit, config, model)
                record.update(
                    title=commit["title"],
                    date=commit["date"],
                    files=commit["files"],
                    complete=complete,
                    chunks=len(payloads),
                    is_merge=commit["is_merge"],
                    parents=commit["parents"],
                )
                old_calls = {
                    call["input_hash"]: call
                    for call in records.get((entry["id"], sha), {}).get("calls", [])
                    if call.get("valid") is True
                }
                decisions = []
                for part, payload in enumerate(payloads, 1):
                    if stop.is_set():
                        raise ValueError("Stopped before remaining chunks")
                    fingerprint = hashlib.sha256(encode(payload)).hexdigest()
                    call = {
                        "input_hash": fingerprint,
                        "input": payload,
                        "input_bytes": len(encode(payload)),
                    }
                    record["calls"].append(call)
                    if not dry_run:
                        if fingerprint in old_calls:
                            response = old_calls[fingerprint]["response"]
                        else:
                            assert client is not None
                            response = client.evaluate(
                                payload,
                                lambda event, p=part: ledger.record(entry["id"], sha, p, event),
                            )
                        call["response"] = response
                        decisions.append(interpret(response, config, model))
                        call["valid"] = True
                if not dry_run:
                    record.update(aggregate(decisions, config, complete))
                record["status"] = "preview" if dry_run else "ok"
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                record.update(status="error", error=str(exc), complete=False)
                if isinstance(exc, ProviderError) and exc.fatal:
                    stop.set()
                    record["fatal_http_status"] = exc.http_status
                    logger.error("Stopping this batch after authentication/payment failure")
                logger.error("%s %s: %s", entry["name"], sha[:12], exc)
            record["elapsed_seconds"] = round(time.monotonic() - start, 3)
            return record

        def progress():
            spend = ledger.snapshot(cached)
            cost = "unknown" if spend["cost_usd"] is None else f"${spend['cost_usd']:.6f}"
            logger.info(
                "Processed %d/%d; matched %d; errors %d; "
                "estimated cost %s; unknown-cost attempts %d",
                len(records),
                expected,
                sum(r.get("matched") is True for r in records.values()),
                sum(r["status"] == "error" for r in records.values()),
                cost,
                spend["unknown_cost_requests"],
            )

        try:
            progress()
            with (
                (output / "results.jsonl").open("a") as stream,
                ThreadPoolExecutor(max_workers=workers) as pool,
            ):
                remaining = iter(jobs)
                pending = {}
                for _ in range(workers):
                    job = next(remaining, None)
                    if job is not None:
                        pending[pool.submit(evaluate, job)] = job
                last_report, since_report = time.monotonic(), 0
                interrupted = False
                while pending:
                    deadline = max(0, progress_interval - (time.monotonic() - last_report))
                    try:
                        ready, _ = wait(pending, timeout=deadline, return_when=FIRST_COMPLETED)
                        for future in ready:
                            record = future.result()
                            encoded = json.dumps(record, ensure_ascii=False) + "\n"
                            position = stream.tell()
                            try:
                                stream.write(encoded)
                                stream.flush()
                            except KeyboardInterrupt:
                                # A partial JSON line would prevent safe resume.
                                stream.seek(position)
                                stream.truncate()
                                raise
                            records[record["repo_id"], record["sha"]] = record
                            pending.pop(future)
                            since_report += 1
                            job = next(remaining, None) if not stop.is_set() else None
                            if job is not None:
                                pending[pool.submit(evaluate, job)] = job
                        if (
                            since_report >= progress_every
                            or time.monotonic() - last_report >= progress_interval
                        ):
                            progress()
                            last_report, since_report = time.monotonic(), 0
                    except KeyboardInterrupt:
                        stop.set()
                        interrupted = True
                        logger.warning("Stopping new work; saving in-flight results")
                        continue
                if interrupted:
                    raise KeyboardInterrupt
            progress()
        finally:
            if client:
                client.close()
            summary = write_reports(
                output, list(records.values()), manifest, spend=ledger.snapshot(cached)
            )
        return summary


def summarize(paths: list[Path]) -> dict:
    manifests, records, events, selections = [], [], [], set()
    for path in paths:
        manifest = json.loads((path / "manifest.json").read_text())
        current = list(read_records(path / "results.jsonl", manifest).values())
        summarize_records(current, manifest)
        if manifests and any(
            manifest[key] != manifests[0][key]
            for key in ("config", "model", "version", "prompt_version", "dry_run")
        ):
            raise ValueError("Incompatible runs; summarize them separately")
        for repo in manifest["repositories"]:
            for sha in repo["commits"]:
                identity = (repo["id"], sha)
                if identity in selections:
                    raise ValueError("Overlapping samples cannot be combined")
                selections.add(identity)
        manifests.append(manifest)
        records.extend(current)
        ledger = read_jsonl(path / "usage.jsonl")
        if any(event.get("run_id") != manifest["run_id"] for event in ledger):
            raise ValueError("Usage ledger belongs to another run")
        events.extend(ledger)
    merged = dict(manifests[0])
    repos = {}
    for manifest in manifests:
        for entry in manifest["repositories"]:
            if entry["id"] not in repos:
                repos[entry["id"]] = {**entry, "commits": list(entry["commits"])}
            else:
                repos[entry["id"]]["commits"].extend(entry["commits"])
                if entry.get("error"):
                    repos[entry["id"]]["error"] = entry["error"]
    merged["repositories"] = list(repos.values())
    return summarize_records(records, merged, spend=accounting(events, current_start=len(events)))
