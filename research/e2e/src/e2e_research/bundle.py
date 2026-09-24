"""Deterministic artifact bundle; only explicitly named portable files are included."""

import gzip
import io
import tarfile
from pathlib import Path

from e2e_research.common import digest

FILES = (
    "sources/classified.jsonl.gz",
    "sources/sources.json",
    "dataset/commits.enriched.jsonl.gz",
    "dataset/dataset.json",
    "dataset/edges-30d.jsonl.gz",
    "aggregates/basic.json",
    "aggregates/temporal.json",
    "aggregates/ai-attribution.json",
    "aggregates/file-chains.json",
    "aggregates/ai-followup.json",
    "aggregates/calendar-followup.json",
    "aggregates/metrics.jsonl.gz",
    "aggregates/provenance.json",
    "report/report.html",
    "report/findings.md",
    "report/calendar-findings.md",
)
OPTIONAL_FILES = (
    "aggregates/coverage-rise.json",
    "report/calendar-followup.png",
    "report/calendar-followup.svg",
)


def create_bundle(artifacts: Path, destination: Path) -> None:
    for name in FILES:
        if not (artifacts / name).is_file():
            raise ValueError(f"Required artifact missing: {name}")
    files = (*FILES, *(name for name in OPTIONAL_FILES if (artifacts / name).is_file()))
    hashes = "".join(f"{digest(artifacts / name)}  {name}\n" for name in files)
    readme = (
        "Portable E2E research artifacts. No Git caches or credentials are included.\n"
        "From the HekaJev source checkout, with research/e2e and its uv.lock:\n\n"
        "uv run --project research/e2e --frozen e2e-research aggregate "
        "--dataset UNPACKED/dataset --output results/recomputed/aggregates\n"
        "uv run --project research/e2e --frozen e2e-research report "
        "--aggregates results/recomputed/aggregates --output results/recomputed/report\n\n"
        "UNPACKED is this directory. Recalculation needs no Git, network or model API.\n"
        "sources/ also supports rebuilding features from the frozen Git revisions.\n"
        "See research/e2e/README.md and schema.json for definitions and commands.\n"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with (
        temporary.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for name in files:
            path = artifacts / name
            info = tarfile.TarInfo("e2e-study/" + name)
            info.size = path.stat().st_size
            info.mode = 0o644
            with path.open("rb") as stream:
                archive.addfile(info, stream)
        for name, content in (("SHA256SUMS", hashes), ("README.txt", readme)):
            data = content.encode()
            info = tarfile.TarInfo("e2e-study/" + name)
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))
    temporary.replace(destination)
    print(f"Created {destination} ({destination.stat().st_size:,} bytes)")
