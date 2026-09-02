# Contributing

Thanks for considering a contribution to brand-ai-readiness-audit. This
repository is two things at once, and most contributions touch both:

1. A set of **Claude Agent Skills** (`SKILL.md` files under `skills/` and
   the root) that document how an agent should perform the audit.
2. A **reference implementation** (`src/braiaudit/`) that actually performs
   the cheap, deterministic parts of that audit in real code, so the
   skills' contracts are testable rather than aspirational.

If you change one, check whether the other needs to change too — see
"Keeping SKILL.md and code in sync" below.

## Setting up

```bash
git clone <this repo>
cd brand-ai-readiness-audit
pip install -e ".[dev]"          # core + test/lint tooling
pip install -e ".[render,dev]"   # also pulls in Playwright (optional)
playwright install chromium      # only needed for the render extra
```

## Running the checks locally

```bash
pytest -q                                   # unit + integration tests
ruff check src tests tools                  # lint
python tools/lint_skills.py                 # SKILL.md / ontology / marketplace lint
python -m braiaudit.cli validate marketplace.json --schema marketplace
```

All four are required to pass in CI (`.github/workflows/ci.yml`) before a
PR can merge.

## Keeping SKILL.md and code in sync

This repo treats `skills/failure-diagnostics/references/ontology.yaml` as
the single source of truth for the failure taxonomy, and treats each
producer module (`fetch.py`, `clean.py`, `discovery.py`, `render.py`,
`pipeline.py`) as the single source of truth for which **signals** actually
get emitted. `tests/test_ontology.py::test_every_ontology_signal_is_emitted_by_some_producer`
enforces the link between them: if you add a signal to the ontology, a
producer module must emit that exact string, or the test fails.

When you add or change a failure mode:

1. Add/edit the entry in `ontology.yaml` (`category`, `severity`,
   `match_mode`, `signals`, `description`, `recovery_strategy`,
   `recommended_tool`, `audit_finding_title`).
2. Make sure the signal(s) it lists are actually emitted by the relevant
   producer module — add the detection logic if not.
3. Update the corresponding `SKILL.md`'s "Signal Thresholds" /
   "Step-by-Step Execution Sequence" section to describe the new detection
   in prose, matching what the code does.
4. Add a test in `tests/test_ontology.py` or the relevant producer's test
   file exercising the new signal end-to-end.

`match_mode: all` (default) means every listed signal must be present
together; `match_mode: any` means a single one is enough. Pick `all` when
the signals are only meaningful in combination, and `any` when they're
independent alternative symptoms of the same problem — see the comment
block at the top of `ontology.yaml` for worked examples of both.

## Adding a new skill

1. Create `skills/<new-skill-name>/SKILL.md` with YAML frontmatter
   (`name` matching the directory, a real trigger-phrase `description`) and
   the four body sections every other skill uses: Operational Mission &
   Preconditions, Input/Output Schema, Step-by-Step Execution Sequence,
   Error Handling & Edge Cases.
2. Add it to `marketplace.json`'s `skills[]` array and, if it participates
   in the standard pipeline, to `execution_order`.
3. If it has a machine-checkable I/O contract, add a schema under
   `schemas/` and validate against it from the implementation (see
   `braiaudit.schemas.validate`).
4. Run `python tools/lint_skills.py` — it will catch a missing
   marketplace.json entry or malformed frontmatter immediately.

## Style

- Python: `ruff check` must be clean (see `pyproject.toml` for the
  selected rule set). Prefer explicit dataclasses / typed dicts documented
  by a JSON Schema over loosely-shaped dicts for anything crossing a module
  boundary.
- Tests are pytest, using `responses` to mock HTTP — never make real
  network calls in the unit test suite (the CLI is exercised end-to-end
  manually against real sites, not in CI).
- Commit messages: short imperative summary line, body explaining *why*
  when the change isn't self-evident from the diff.

## Reporting issues

Open a GitHub issue with: what you ran, what you expected, what happened
instead, and (for a misclassification) the raw signal bundle or a URL that
reproduces it. For security-sensitive findings about the audit tooling
itself, email ecgenius.life@gmail.com instead of filing a public issue.
