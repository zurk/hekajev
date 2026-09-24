# Agent guide

Read [README.md](README.md) for installation, CLI commands, output, and development checks.
The package is general-purpose: research questions belong in YAML, not Python special cases.
Keep changes compact; test behavior, component boundaries, and concrete regressions.

## Author a configuration

Define the commit population and what each label means. Start with the
[small example](examples/python.yaml), [one reason](examples/tests.yaml), or
[multiple reasons](examples/tests-multi.yaml). Use `single` when answers exclude
each other and `multi` when several can apply. Write criteria around visible
evidence, including nearby cases that do not qualify. Inspect a `--dry-run`,
then a small live sample. Review uncertain and incomplete results before scaling.

Use CLI `--path-pattern` for a cheap changed-path selection before model questions.
Inspect runner configuration and actual tests; include known historical paths and
specific support files. Repeated repo-root Git globs mean OR and preserve the full
commit context. Record what the selection cannot observe, such as root dependency
updates outside the selected paths. Questions must still establish semantic relevance.
For E2E research start with [examples/e2e.yaml](examples/e2e.yaml); an isolated
component rendered in a browser is not an application end-to-end workflow.

Use `TYPESAFE_API_KEY` from the environment. Keep secrets outside configs and output.
Use the actual CLI for integration checks; do not imitate its behavior with direct API calls.

## YAML contract

- `inputs`: optional list of `{repository, revision, path_patterns}`; revision defaults to `HEAD`, patterns to unrestricted.
- Pin each input to a full SHA for repeatable selection; local YAML paths resolve relative to the YAML file.
- CLI sources replace the YAML input list; explicit CLI revision/patterns override every selected input.
- `instructions`: optional shared text, limited to guidance that applies to every question.
- `filters`: nonempty list of question strings or `{question: text, aggregation: any|all}` objects.
- `filter_mode`: `and` (default) or `or`; combines whole-commit filter decisions.
- `classifications`: map from meaningful names to the fields below.
- `question`: required text; `categories`: map of stable label names to descriptions.
- `mode`: `single` (default) or `multi`; `threshold`: optional per-classification threshold.
- Single requires `uncertain` and uses `aggregation: consensus`.
- Multi has no `uncertain` category and uses `aggregation: any` (default) or `all`.
- Global defaults: `filter_threshold: 0.8`, `choice_threshold: 0.6`, `multi_threshold: 0.8`.
- `limits`: `input_bytes: 32768`, `max_chunks: 20`; CLI flags override these values.

Questions and category descriptions are strings; use YAML `>-` for paragraphs.
Keep repository paths, API keys, and display settings out of question text.

## Write questions literally

Ask one narrow question, name the evidence to inspect, and make a high yes/no
score mean the property is present. Put inclusion and exclusion rules in the
category descriptions. A `single` question needs a tie rule and an `uncertain`
answer. A `multi` question judges each label on its own; several or none may
apply. Reserve `mixed` for a directional judgment with both directions present.

Check the message against the patch. A filename, timeout, skip, retry, deletion,
or snapshot change alone cannot establish purpose. Test-only edits do not prove
that product behavior stayed the same. Fewer lines or assertions do not prove a
weaker oracle; compare checks that actually run. Count commits, dates, and costs
in code.

Example boundary: adding a regression test alongside a bug fix is `coverage`.
Changing an existing expectation to match an intended API change is `product_adaptation`.
If both occur, multi can select both; single requires a supported dominant purpose.
An isolated browser component test is `unit`; a full application workflow is `e2e`.
A timeout adjustment needs evidence of nondeterminism before it is labeled `flakiness`.

## State and question separation

Jev receives commit data in a named JSON `state` and judgments in `questions`.
Keep these fields separate; preserve source text without adding XML wrappers or stripping source tags.
Question IDs route answers and are not model-visible, so the question must carry its own meaning.
Questions run independently; never rely on a filter or another classification's answer being visible.
Keep the short shared boundary instruction in the core prompt; do not repeat it in YAML.
API field separation does not guarantee that hostile text cannot influence a decision.
Include adversarial commit messages in an audit when the source population warrants it.

## Match aggregation to meaning

`any` fits existence questions: one chunk can establish a test change. Use `all`
only when every chunk must support the answer. `filter_mode` combines separate
filters after their chunk answers. Single-label consensus needs complete evidence
and agreement across chunks; unrelated chunks can make it uncertain. Chunk
scores are not whole-commit probabilities. Keep incomplete evidence visible, and
avoid causal or numeric questions that one chunk cannot answer.

## Validate an interpretation

Look for counterexamples to each category boundary. When comparing `single` and
`multi`, hold SHAs, evidence, model, and sampling fixed. Change the question from
dominant purpose to independent presence, then inspect extra labels against the
patches. More labels alone do not mean better accuracy. Report each category's
share separately; multi-label shares may total more than 100%.

Compare tokens, cost, latency, uncertainty, and reviewed errors. Test chunking on
commits that fit both split and unsplit inputs. Keep failures and undecided cases
in denominators. A model audit is not human ground truth, and commit counts do
not measure engineering time.

## Vendor guidance

TypeSafe documents [question design](https://docs.typesafe.ai/concepts/how-to-build-with-system-one),
[state](https://docs.typesafe.ai/concepts/state), and the [API](https://docs.typesafe.ai/api).
See [Noul](https://docs.typesafe.ai/primitives/noul) for yes/no questions,
[Choice](https://docs.typesafe.ai/primitives/choice) for categories, and
[Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13) for failure
cases. Pin a [model](https://docs.typesafe.ai/models) and check its price.
