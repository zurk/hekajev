"""Explicit offline stages; no command invokes HekaJev's model runner."""

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="e2e-research")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="Import existing HekaJev runs")
    ingest.add_argument("--population", type=Path, required=True)
    ingest.add_argument("--runs", type=Path, required=True)
    ingest.add_argument("--output", type=Path, required=True)
    enrich = commands.add_parser("enrich", help="Add features from frozen local Git histories")
    enrich.add_argument("--sources", type=Path, required=True)
    enrich.add_argument("--repo-root", type=Path, required=True)
    enrich.add_argument("--output", type=Path, required=True)
    aggregate = commands.add_parser("aggregate", help="Calculate metrics from enriched JSONL only")
    aggregate.add_argument("--dataset", type=Path, required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    render = commands.add_parser("report", help="Render a portable report from aggregate JSON")
    render.add_argument("--aggregates", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument(
        "--figures", action="store_true", help="Export PNG/SVG; install figures extra"
    )
    verify = commands.add_parser(
        "verify", help="Check basic metrics against saved research reports"
    )
    verify.add_argument("--aggregates", type=Path, required=True)
    verify.add_argument("--reference", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    public = commands.add_parser("verify-public", help="Recalculate counts from public downloads")
    public.add_argument("--commits", type=Path, required=True)
    public.add_argument("--origins", type=Path, required=True)
    public.add_argument("--study", type=Path, required=True)
    public.add_argument("--insights", type=Path, required=True)
    public.add_argument("--output", type=Path)
    bundle = commands.add_parser(
        "bundle", help="Package portable data and reports without Git caches"
    )
    bundle.add_argument("--artifacts", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "ingest":
        from e2e_research.ingest import ingest_runs

        result = ingest_runs(args.population, args.runs, args.output)
        print(json.dumps({"classified": result["classified_count"], "model_calls": 0}))
    elif args.command == "enrich":
        from e2e_research.enrich import enrich_dataset

        result = enrich_dataset(args.sources, args.repo_root, args.output)
        print(json.dumps({"records": result["records"], "classified": result["classified"]}))
    elif args.command == "aggregate":
        from e2e_research.aggregate import aggregate_dataset

        aggregate_dataset(args.dataset, args.output)
    elif args.command == "report":
        from e2e_research.report import render_report

        render_report(args.aggregates, args.output, figures=args.figures)
    elif args.command == "verify":
        from e2e_research.verify import verify_reference

        verify_reference(args.aggregates, args.reference, args.output)
    elif args.command == "verify-public":
        from e2e_research.verify_public import verify_public

        result = verify_public(args.commits, args.origins, args.study, args.insights, args.output)
        print(json.dumps(result, ensure_ascii=False))
    else:
        from e2e_research.bundle import create_bundle

        create_bundle(args.artifacts, args.output)
