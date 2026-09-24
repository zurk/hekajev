import argparse
import json
import logging
import math
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import yaml
from pydantic import ValidationError

from hekajev import __version__
from hekajev.config import Limits, load_config
from hekajev.jev import MODEL, load_key
from hekajev.reporting import format_summary
from hekajev.sources import default_cache_dir

logger = logging.getLogger(__name__)


def bounded_integer(minimum: int, maximum: int | None = None):
    def parse(value: str) -> int:
        parsed = int(value)
        if parsed < minimum or (maximum is not None and parsed > maximum):
            bound = f"between {minimum} and {maximum}" if maximum else f"at least {minimum}"
            raise argparse.ArgumentTypeError(f"must be {bound}")
        return parsed

    return parse


def finite_number(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return parsed


def positive_interval(value: str) -> float:
    parsed = finite_number(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


@contextmanager
def cli_logging():
    package = logging.getLogger("hekajev")
    old_level, old_propagate = package.level, package.propagate
    httpx_logger = logging.getLogger("httpx")
    old_httpx_level = httpx_logger.level
    handlers = [logging.StreamHandler(sys.stderr)]
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    handlers[0].setFormatter(formatter)
    package.addHandler(handlers[0])
    package.setLevel(logging.INFO)
    package.propagate = False
    httpx_logger.setLevel(logging.WARNING)
    try:
        yield package, handlers, formatter
    finally:
        for handler in handlers:
            package.removeHandler(handler)
            handler.close()
        package.setLevel(old_level)
        package.propagate = old_propagate
        httpx_logger.setLevel(old_httpx_level)


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter and classify Git commits with Jev.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("scan", help="Analyze local or remote Git repositories")
    analyze.add_argument(
        "sources",
        nargs="*",
        metavar="SOURCE",
        help="Local path, HTTPS or SSH URL; overrides YAML inputs",
    )
    analyze.add_argument("--config", required=True, type=Path)
    analyze.add_argument("--output", required=True, type=Path, help="Output directory")
    analyze.add_argument("--key-file", type=Path, help=argparse.SUPPRESS)
    analyze.add_argument("--model", default=MODEL)
    analyze.add_argument(
        "--revision", help="Git ref, SHA or range; overrides each YAML input (default HEAD)"
    )
    analyze.add_argument(
        "--limit",
        type=bounded_integer(1),
        default=100,
        help="Latest nonmerge candidates per repository (default: 100)",
    )
    analyze.add_argument(
        "--sample", type=bounded_integer(1), help="Random subset size per repository"
    )
    analyze.add_argument("--seed", type=int, default=42)
    analyze.add_argument("--since")
    analyze.add_argument("--until")
    analyze.add_argument(
        "--path-pattern",
        action="append",
        default=None,
        metavar="GLOB",
        help="Changed path glob relative to repo root; repeat for OR; applied before limit/sample",
    )
    analyze.add_argument(
        "--commits-file",
        type=Path,
        help="Exact full SHAs, one per line; one source; overrides --limit",
    )
    analyze.add_argument(
        "--include-merges",
        action="store_true",
        help="Analyze merge commits relative to their first parent",
    )
    analyze.add_argument(
        "--requests-per-second",
        type=positive_interval,
        help="Maximum API attempt launch rate shared by workers",
    )
    analyze.add_argument(
        "--workers",
        type=bounded_integer(1, 128),
        default=8,
        help="Global concurrent commits (default: 8)",
    )
    analyze.add_argument("--cache-dir", type=Path, default=default_cache_dir())
    analyze.add_argument(
        "--max-chunks", type=bounded_integer(1, 1000), help="Override YAML chunk limit"
    )
    analyze.add_argument(
        "--input-bytes",
        type=bounded_integer(2048, 1_000_000),
        help="Override YAML byte limit for each complete request",
    )
    analyze.add_argument(
        "--progress-every",
        type=bounded_integer(1),
        default=10,
        help="Log progress after this many completed commits (default: 10)",
    )
    analyze.add_argument(
        "--progress-interval",
        type=positive_interval,
        default=2.0,
        help="Maximum seconds between progress logs (default: 2)",
    )
    analyze.add_argument(
        "--price-per-million",
        type=finite_number,
        help="USD per million input tokens; defaults to the known model rate",
    )
    analyze.add_argument(
        "--resume", action="store_true", help="Reuse successes; retry error records"
    )
    analyze.add_argument(
        "--dry-run", action="store_true", help="Write exact requests without contacting Jev"
    )
    report = commands.add_parser("summarize", help="Summarize output directories without API calls")
    report.add_argument("inputs", nargs="+", type=Path, metavar="DIRECTORY")
    report.add_argument("--json", action="store_true", help="Print the full JSON summary")
    args = parser.parse_args()
    with cli_logging() as (package, handlers, formatter):
        try:
            from hekajev.pipeline import scan, summarize

            if args.command == "summarize":
                summary = summarize([path.expanduser() for path in args.inputs])
                print(json.dumps(summary, indent=2) if args.json else format_summary(summary))
            else:
                output = args.output.expanduser()
                if output.exists() and not output.is_dir():
                    raise ValueError("--output must be a directory, not a file")
                output.mkdir(parents=True, exist_ok=True)
                handler = logging.FileHandler(output / "run.log", encoding="utf-8")
                handler.setFormatter(formatter)
                handlers.append(handler)
                package.addHandler(handler)
                config = load_config(args.config.expanduser())
                overrides = {
                    name: getattr(args, name)
                    for name in ("input_bytes", "max_chunks")
                    if getattr(args, name) is not None
                }
                limits = Limits.model_validate({**config.limits.model_dump(), **overrides})
                config = config.model_copy(update={"limits": limits})
                key = None if args.dry_run else load_key(args.key_file)
                summary = scan(
                    args.sources,
                    config,
                    output,
                    key=key,
                    model=args.model,
                    revision=args.revision,
                    limit=args.limit,
                    sample=args.sample,
                    seed=args.seed,
                    since=args.since,
                    until=args.until,
                    workers=args.workers,
                    resume=args.resume,
                    dry_run=args.dry_run,
                    cache_dir=args.cache_dir.expanduser(),
                    progress_every=args.progress_every,
                    progress_interval=args.progress_interval,
                    price_per_million=args.price_per_million,
                    commits_file=args.commits_file.expanduser() if args.commits_file else None,
                    include_merges=args.include_merges,
                    requests_per_second=args.requests_per_second,
                    path_patterns=args.path_pattern,
                )
                print(format_summary(summary))
            counts = summary["counts"]
            return int(
                any(
                    counts.get(name, 0)
                    for name in ("error", "missing", "incomplete", "source_errors")
                )
            )
        except ValidationError as exc:
            fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
            logger.error("Invalid configuration fields: %s", ", ".join(fields) or "root")
            return 2
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            logger.error("Invalid YAML syntax%s", location)
            return 2
        except subprocess.TimeoutExpired:
            logger.error("Git operation timed out")
            return 2
        except (ValueError, OSError) as exc:
            logger.error("%s", exc)
            return 2
        except KeyboardInterrupt:
            logger.warning("Interrupted; use --resume to continue")
            return 130


if __name__ == "__main__":
    sys.exit(main())
