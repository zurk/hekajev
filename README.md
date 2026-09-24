<div align="center">
  <h1>HekaJev</h1>
  <p><strong>A hundred hands through Git history.</strong></p>
  <p>Ask questions about Git history across one or many repositories.</p>
  <p>
    <a href="https://github.com/zurk/hekajev/blob/main/pyproject.toml"><img alt="Python 3.14+" src="https://img.shields.io/badge/python-3.14%2B-3776AB?logo=python&amp;logoColor=white"></a>
    <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-0F766E"></a>
    <a href="https://docs.typesafe.ai/"><img alt="Powered by Jev" src="https://img.shields.io/badge/powered%20by-Jev-D97757"></a>
  </p>
  <p>
    <a href="#quick-start">Quick start</a> ·
    <a href="#install-and-run">CLI</a> ·
    <a href="#examples">Examples</a> ·
    <a href="#configuration">Configuration</a> ·
    <a href="#research">Research</a> ·
    <a href="https://github.com/zurk/hekajev/issues">Issues</a>
  </p>
</div>

![HekaJev sorting commits through Git history](assets/hekajev.png)

HekaJev answers questions about Git history. It filters and classifies commits,
then saves the evidence, decisions, and cost behind the results.

Python 3.14+, Git, and `TYPESAFE_API_KEY` are required for live analysis.

## Highlights

- Analyze local and remote repositories together. Pin revisions and select paths.
- Write filters and categories in YAML. A commit may have one label or several.
- Split large commits, resume runs, and see progress and estimated cost.
- Inspect saved answers in JSONL, JSON, logs, and a standalone HTML report.

## Quick start

Give this prompt to your coding agent:

```text
Use HekaJev from https://github.com/zurk/hekajev to analyze Git history.

First read README.md and AGENTS.md. Then ask me:
- which repositories to analyze;
- what I want to learn or measure from their commit history.

Install HekaJev. If TYPESAFE_API_KEY is unavailable, ask me to set it in the
environment. Then carry out the requested analysis.
```

### API key

Create an account or sign in to the [TypeSafe console](https://console.typesafe.ai/),
get an API key from the dashboard, and expose it to HekaJev:

```bash
export TYPESAFE_API_KEY='your-key'
```

See the official [TypeSafe quick start](https://docs.typesafe.ai/introduction/quickstart).

## Data and accuracy

HekaJev sends commit messages, paths, and patches to TypeSafe. It saves the
requests and raw answers. Set `TYPESAFE_API_KEY` in the environment, never in
YAML or Git. Model scores guide decisions; measuring accuracy requires an
independent review. The CLI writes a log and prints a final summary.

## Install and run

```bash
uv sync
uv run hekajev scan /path/to/repo https://github.com/pallets/flask.git \
  --config examples/tests.yaml --output results/test-changes \
  --revision HEAD --limit 1000 --sample 100 --seed 42 --workers 8
uv run hekajev summarize results/test-changes
```

Sources can be local paths, HTTPS URLs, or SSH Git URLs. Remote sources use cached
bare clones; `--cache-dir` chooses the cache. Your Git authentication handles private repositories.
The cache is separate from the results. `--workers` limits concurrent requests.

Alternatively, put repository-specific selection in the same YAML as your questions:

```yaml
inputs:
  - repository: https://github.com/streamlit/streamlit.git
    revision: 3a84dfec4d46050b8834c862719cf8602d33714b
    path_patterns:
      - 'e2e_playwright/**'
      - 'e2e/**'
  - repository: ../another-repo
    revision: HEAD
    path_patterns:
      - 'tests/e2e/**'
# filters and classifications follow, as in examples/e2e.yaml
```

```bash
uv run hekajev scan --config analysis.yaml --limit 1000000 --output results/study
```

`inputs` is optional; each entry has `repository`, `revision` (default `HEAD`),
and `path_patterns` (default unrestricted). Local paths in YAML are relative to
the YAML file, while CLI paths are relative to the working directory. CLI source
arguments replace the entire YAML input list. Explicit `--revision` and
`--path-pattern` replace the corresponding setting for every selected repository;
other YAML questions and settings still apply. Use one input entry per repository.

To repeat a selection, pin full commit SHAs and save the command, package version,
and model. `--limit` defaults to 100 per repository; raise it for a full-history
study. Model answers may differ on another run, so keep the saved raw responses.

`--revision` accepts a ref, SHA, or revision range; `--since` and `--until` restrict
dates. For each repository, `--limit` bounds the latest nonmerge candidates, then
`--sample` selects a random subset with `--seed`. Omit `--sample` to process all
candidates within the limit. Shallow local repositories expose only their available history.

Use repeated `--path-pattern` arguments to select commits that changed any matching path:

```bash
uv run hekajev scan /path/to/repo --config examples/e2e.yaml \
  --path-pattern 'tests/e2e/**' --path-pattern '**/playwright.config.*' \
  --limit 1000 --output results/e2e
```

Patterns are case-sensitive Git globs relative to the repository root: `*` stays
within a path component; `**` crosses directories. Quote patterns to prevent shell
expansion. A directory subtree needs `directory/**`. Repeated patterns mean OR.
Adds, edits, deletions, and either side of a rename qualify; merge changes use the
first parent when enabled. Selection precedes `--limit` and `--sample` and makes no
API calls. The complete selected commit, including other changed paths, is sent
to Jev for context. Without patterns, every candidate reaches the YAML filters.
Zero matching commits is a valid empty result. Patterns are recorded in the
manifest and must remain identical on resume.

Inspect each repository's test runner and real scenarios before choosing patterns.
Browser component tests are not necessarily application E2E; historical directories
may need extra patterns. Path selection finds candidates; YAML determines relevance.
Root lockfile or general automation changes are outside the selection unless an
explicit pattern includes them.

For exact batches, use one source with `--commits-file selected-shas.txt` (one full SHA per line).
It overrides `--limit` and cannot be combined with sampling or dates. `--include-merges`
uses first-parent diffs; report merges separately because their changes overlap constituent commits.
With `--path-pattern`, only matching SHAs from the exact list are retained.
`--requests-per-second` limits API attempt launches across all workers in this process.

Use `--dry-run` with a separate output directory to inspect inputs without an API
key or calls. Add `--resume` to continue the same output and configuration. The
manifest records revisions and selected SHAs. Resume keeps successful
results and retries failures. `summarize --json` prints JSON.
See `hekajev scan --help` for all options.

Install the command outside this checkout with `uv tool install .`.

## Examples

Copy the closest example and edit its questions using the [authoring guide](AGENTS.md).

- **[Minimal Python](examples/python.yaml)** — keeps commits with Python changes,
  then classifies their scope as product code, tests, both, or uncertain.
- **[Primary test-change reason](examples/tests.yaml)** — finds automated-test
  changes, selects one dominant reason, detects test levels, and evaluates the
  effect on fault detection.
- **[Multiple test-change reasons](examples/tests-multi.yaml)** — uses the same
  test taxonomy, but allows several independently supported reasons per commit.
- **[Application E2E changes](examples/e2e.yaml)** — isolates application E2E
  tests and support code, then detects coverage, adaptation, test bugs,
  flakiness, refactoring, and environment work.

Preview an example without making API calls:

```bash
uv run hekajev scan /path/to/repo --config examples/python.yaml \
  --output results/example-preview --dry-run
```

## Configuration

YAML defines `filters`, named `classifications`, optional shared `instructions`,
and optional repository `inputs`.
Start with an [example](#examples). The [authoring guide](AGENTS.md) explains
the schema and how to write questions.

- `filter_mode: and` requires every filter; `or` requires any filter. Default: `and`.
- Filters use yes/no probabilities: ≥ `filter_threshold` means yes, ≤ its complement means no.
- Other filter probabilities are undecided. Default threshold: `0.8`.
- `mode: single` selects one category and requires an `uncertain` category. This is the default mode.
- A single winner below `choice_threshold` becomes `uncertain`. Default: `0.6`.
- `mode: multi` asks an independent yes/no question per category; labels can coexist.
- Multi uses `multi_threshold` (default `0.8`); uncertainty is separate, with no `uncertain` category.
- A classification's `threshold` overrides its mode's global threshold.

All questions for a chunk share one request and are evaluated independently.
Classifications cannot see filter answers; the report counts classification labels
only for matched commits. Excluded commits still incur the cost of those questions.
The test examples cover test code and directly supporting data, helpers, and tools.
Changing an automation or configuration file alone does not establish test relevance.

## Evidence and aggregation

Extraction includes title, message, changed paths, and parent-relative patches
with rename information and three context lines. Tests are not executed; full
files, PR discussions, neighboring commits, and runtime results are not fetched.
`state` is a named JSON object; questions and criteria occupy separate API fields.
The API separation is not a guarantee against adversarial commit content.

Large inputs are split across file and patch fragments. Defaults:

```yaml
limits:
  input_bytes: 32768
  max_chunks: 20
```

`input_bytes` bounds each complete serialized request, including questions and
metadata. `--input-bytes` and `--max-chunks` override YAML. Twenty 32 KiB requests
contain at most 640 KiB per commit before retries; repeated context reduces unique
diff capacity. These are byte budgets, not token measurements. The chunk limit
can leave evidence incomplete, which is recorded explicitly.
Patch capacity is divided across changed files while the byte and chunk budgets
allow it. Patch previews precede longer remainders; omitted or truncated evidence
marks the commit incomplete.

Aggregation runs in Python, with no extra model call:

- Filter `aggregation: any` accepts a yes in any chunk; `all` requires yes throughout. Default: `any`.
- Filter combination (`and` / `or`) happens after each filter's chunks are aggregated.
- Multi categories use the same `any` / `all` rules, independently. Default: `any`.
- Single uses `aggregation: consensus`: all chunks must agree confidently and evidence must be complete.
- Disagreement or insufficient evidence yields uncertainty; missing chunks do not count as negative evidence.

Chunk scores stay in the output. They are not averaged into a commit score.
Splitting can separate related changes and increase uncertainty.
Check unsplit versus split results on known examples when tuning the budget.

## Output and cost

`--output` is a directory containing `results.jsonl`, `manifest.json`, `summary.json`,
`report.html`, `run.log`, and `usage.jsonl`. Open the standalone HTML report locally
for project distributions, category counts, uncertainty, incomplete evidence, and cost.
The manifest records questions, settings, sources, sampling, model, and pricing.

Progress goes to stderr and the log every 10 finished commits or two seconds,
whichever comes first. Adjust `--progress-every` and `--progress-interval`.
The final table shows each repository and the total; JSON has more detail.
Library callers configure their own logging handlers.
Authentication and payment errors stop new batch work. An interrupt drains in-flight
results before writing reports. Compressed `results.jsonl.gz` and `usage.jsonl.gz`
are readable; plain JSONL files may hold later append-only records for resume.
Exit code 1 reports errors, missing records, or incomplete evidence. Exit code 0
also applies to a successful `--dry-run` preview, which has no model decisions.
Invalid CLI arguments or configuration return exit code 2.

Cost is estimated from returned usage at the saved model rate. The default
`jev-1.13.0` uses $0.042 per million input tokens; output tokens are free.
Use `--price-per-million` for an explicit rate. Resume separates new request cost
from accumulated run cost. Calls without usage make the estimate incomplete;
the estimate is not a reconciled invoice. Multi questions and retries can add cost.

## Research

HekaJev helped study E2E test changes across 21 public repositories. From
549,224 commits, path rules selected 20,026 candidates; Jev marked 16,024 as
E2E-related. The study tracks new coverage, maintenance, and later edits to
the same files. Its AI comparisons do not establish cause and effect.

Read the [report](https://quretests.com/research/e2e-changes),
[LinkedIn post](https://www.linkedin.com/posts/kslavnov_ai-was-supposed-to-cut-e2e-test-debt-its-share-7508877848523472896-v0GH),
or [download the data](https://quretests.com/research/e2e-changes/data).
[Research instructions](research/e2e/README.md) show how to check published
numbers offline. The [study configuration](research/e2e/study.yaml) pins the
repositories and questions. A fresh model run may give different answers.

## Repository layout

The [authoring guide](AGENTS.md) covers YAML questions. Start from the
[examples](#examples). The CLI lives in `src/hekajev/`, its tests in `tests/`,
and the E2E study in [research/e2e](research/e2e/README.md).

## Develop

```bash
uv run pytest
uv run ruff check .
uv build
```

## References

- [State and questions](https://docs.typesafe.ai/concepts/state), [API contract](https://docs.typesafe.ai/api).
- [Question design and composition](https://docs.typesafe.ai/concepts/how-to-build-with-system-one).
- [Noul](https://docs.typesafe.ai/primitives/noul), [Choice](https://docs.typesafe.ai/primitives/choice).
- [Model pricing](https://docs.typesafe.ai/models), [limits and failure modes](https://docs.typesafe.ai/model-jaggedness/jev-1.13).

## Why HekaJev?

`Heka` comes from the [Hecatoncheires](https://www.theoi.com/Titan/Hekatonkheires.html),
the hundred-handed giants of Greek mythology. `Jev` is the model that evaluates
the questions. Together they describe the tool: a hundred hands moving through
Git history, separating signal from noise.

Made by the [Qure team](https://quretests.com/).
