# E2E study: reproduce the numbers

This package analyzes saved HekaJev answers and Git history for 21 repositories,
frozen on September 21, 2026. It counts why application E2E tests change and
tracks later edits to the same paths. See the [report](https://quretests.com/research/e2e-changes),
[LinkedIn post](https://www.linkedin.com/posts/kslavnov_ai-was-supposed-to-cut-e2e-test-debt-its-share-7508877848523472896-v0GH),
[method](https://quretests.com/research/e2e-changes/methodology), and
[downloads](https://quretests.com/research/e2e-changes/data).

`study.yaml` pins repositories, revisions, paths, and questions. The Python code
imports saved answers, adds Git features, calculates statistics, and builds a
standalone technical report. `schema.json` describes the enriched records.
Generated files go under the ignored `results/` directory.

## Check published numbers

The [Data page](https://quretests.com/research/e2e-changes/data) has
`commits.zip` with 20,026 selected commits and their Jev answers. It also has
`followup-origins.zip` and the study and follow-up summary JSON files. Save the
four files in `results/public-e2e/`; name the summary files `study.json` and
`insights.json`. Their download filenames include build hashes.

Run from the repository root:

```bash
uv run --project research/e2e --frozen e2e-research verify-public \
  --commits results/public-e2e/commits.zip \
  --origins results/public-e2e/followup-origins.zip \
  --study results/public-e2e/study.json \
  --insights results/public-e2e/insights.json \
  --output results/public-e2e/recalculated.json
```

This counts E2E commits, reasons, the 2025–2026 coverage change, and 30-day
follow-ups from the downloaded records. It checks headline counts against the
published summaries. It makes no Git or Jev calls.

## Rebuild the full analysis

The public downloads cover selected commits and follow-up origins. The full
468,566-commit nonmerge census is in `e2e-data.tar.gz`, a portable archive from
the study author that is not stored in this repository. Unpack its `e2e-study/`
contents under `results/e2e-study-2026-09-21/reproducible/`. Then run:

```bash
uv run --project research/e2e --frozen e2e-research aggregate \
  --dataset results/e2e-study-2026-09-21/reproducible/dataset \
  --output results/reproduced-e2e/aggregates
uv run --project research/e2e --frozen e2e-research report \
  --aggregates results/reproduced-e2e/aggregates \
  --output results/reproduced-e2e/report
```

`aggregate` needs `commits.enriched.jsonl.gz` and `dataset.json`; it reads no
repository or network data. Open `results/reproduced-e2e/report/report.html`
locally. Add `--figures` to `report` with `--extra figures` to export PNG/SVG.

To rebuild Git features, use the archive's `sources/` directory and complete
local Git histories at the pinned SHAs:

```bash
uv run --project research/e2e --frozen e2e-research enrich \
  --sources results/e2e-study-2026-09-21/reproducible/sources \
  --repo-root results/full-study-2026-09-21/repos \
  --output results/reproduced-e2e/dataset
```

Repositories may be bare or ordinary Git checkouts; name each directory
`owner__repository.git`. `sources.json` lists their URLs and frozen SHAs.
The `ingest` command imports the author's `population.json` and saved HekaJev
batch journals when rebuilding `sources/` from the original runs. Run
`e2e-research ingest --help` for its input paths. A fresh
`hekajev scan --config research/e2e/study.yaml` makes new, billable Jev calls;
it cannot recover the saved answers exactly.

## How the counts work

Each enriched JSONL row is one nonmerge commit. Selected rows retain the saved
filter and reason answers; other rows supply history denominators. An undecided
filter stays undecided. Missing AI fields on unselected rows do not mean no AI.
Maintenance is the union of five non-coverage reasons and may overlap coverage.

A follow-up starts at an E2E-positive commit and tracks the same path along Git
ancestry. It needs an eligible 14-, 30-, or 90-day window. Deletion, rename, or
recreation ends the path episode; merge-only edits do not count. One origin
counts once even if several files or later commits qualify. A later flakiness
label shows observed repair work, not proof that the origin caused the failure.

AI attribution requires an explicit marker in the origin's full commit message.
An unmarked commit is not a verified human-written control. Comparisons use
shared project and time groups, with sensitivity checks for file count, size,
prior activity, and author. These are associations, not causal effects.
The 2025–2026 coverage comparison uses January 1–September 21 in both years.

The aggregate directory holds census, time, AI, follow-up, and provenance JSON.
`metrics.jsonl.gz` points to numeric results; `edges-30d.jsonl.gz` records
source, target, and path links. `e2e-research bundle` packages the portable
inputs and outputs with checksums. `e2e-research verify` checks aggregates
against a saved reference. Hand-reviewed accuracy and external studies need
separate evidence; commit records alone cannot verify them.

Run the checks with `uv run --project research/e2e --frozen pytest research/e2e/tests`
and `uv run --project research/e2e --frozen ruff check research/e2e`.

Research by [Konstantin S.](https://www.linkedin.com/in/kslavnov/) at [Qure](https://quretests.com/).
