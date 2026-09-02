# Brand AI Readiness Audit

[![CI](https://github.com/example/brand-ai-readiness-audit/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

A [Claude Agent Skill](https://docs.claude.com/en/docs/agents/skills) set —
**with a real, tested reference implementation** — that audits a brand's
website for **AI readiness**: how reliably its content can be reached,
rendered, cleaned, and understood by AI search crawlers, RAG ingestion
pipelines, and autonomous browsing agents.

The audit produces one structured JSON report: a ranked list of findings
(crawl-blocking robots rules, JS-only rendering, missing `schema.org`
markup, fragmented information, boilerplate noise, etc.), each tied to a
concrete observed signal, a severity, and a suggested engineering fix.

This repo is two things layered together:

1. **Six `SKILL.md` files** documenting how an AI agent should perform the
   audit — the spec, in the Claude Agent Skill two-level disclosure format.
2. **`braiaudit`**, a Python package (`src/braiaudit/`) that actually
   implements the deterministic parts of that spec — so the contracts in
   each `SKILL.md` are enforced by JSON Schema and covered by tests, not
   just prose. `braiaudit audit example.com` runs the real pipeline against
   a live site today, with zero required external services.

## Quickstart

```bash
git clone <this repo> && cd brand-ai-readiness-audit
pip install -e ".[dev]"

braiaudit audit example.com --max-pages 5
```

This runs website-observer + content-cleaner + query-guided-discovery +
failure-diagnostics + freshness-corroboration for real, over live HTTP, and
prints a floor-schema-conformant JSON report to stdout. Render-dependent
checks (client-side-rendered content, Shadow DOM, lazy-load) are reported
as an honest `RENDER_COVERAGE_GAP` finding rather than skipped silently,
unless you install the optional headless-browser extra:

```bash
pip install -e ".[render]"
playwright install chromium

braiaudit audit example.com --max-pages 5 --max-render-pages 3
```

Useful flags: `--query "what does this product cost"` (repeatable — tests
whether target queries are answerable, per `query-guided-discovery`),
`--output report.json`, `--fail-on-critical` (nonzero exit if any critical
finding is present, for CI gating on your own site).

Validate any JSON file against one of the repo's schemas:

```bash
braiaudit validate report.json --schema audit-report
```

### Using it as a Claude Agent Skill instead of the CLI

Point a Claude Code / Claude Agent session at this directory (or load it as
a skills marketplace via `marketplace.json`) and ask:

> "Run a brand AI readiness audit on example.com"

Claude loads the root [`SKILL.md`](SKILL.md), sequences the five capability
skills under `skills/` via its own tool use (WebFetch, a browser tool,
etc.) instead of the Python pipeline, and returns the same floor-schema
JSON. Every skill's `SKILL.md` names the exact reference-implementation
module and script it corresponds to, if you want to compare behavior or
run the deterministic parts directly instead of re-deriving them via
tool calls.

## Architecture

```
brand-ai-readiness-audit/
├── SKILL.md                          # Master Orchestrator Skill
├── README.md                         # This file
├── marketplace.json                  # Manifest listing all active skills
├── pyproject.toml                    # `braiaudit` package definition
├── schemas/                          # JSON Schemas for every I/O contract (source of truth)
├── src/braiaudit/                    # Reference implementation, one module per skill
├── tests/                            # pytest suite (56 tests) + HTML fixtures
├── tools/lint_skills.py              # CI-enforced SKILL.md / ontology / marketplace linter
├── .github/workflows/ci.yml          # lint + skill-lint + schema-validate + pytest, py3.10–3.13
└── skills/
    ├── website-observer/
    │   ├── SKILL.md                  # Low-overhead HTTP/DOM inspection
    │   └── scripts/observe.py
    ├── failure-diagnostics/
    │   ├── SKILL.md                  # Signal-to-Ontology mapping
    │   ├── references/ontology.yaml  # Web Failure Ontology Knowledge Base
    │   └── scripts/diagnose.py
    ├── crawl-render-audit/
    │   ├── SKILL.md                  # Headless Playwright/Chromium rendering
    │   └── scripts/render_audit.py
    ├── content-cleaner/
    │   ├── SKILL.md                  # Main-content extraction & noise removal
    │   └── scripts/clean_content.py
    ├── query-guided-discovery/
    │   ├── SKILL.md                  # Multi-page internal link scoring
    │   └── scripts/discover.py
    └── freshness-corroboration/
        ├── SKILL.md                  # Audit evidence assembly & JSON output
        └── scripts/assemble.py
```

Each `SKILL.md` follows the two-level Claude Skill disclosure pattern: a
YAML frontmatter (`name` + trigger `description`) that Claude scans to
decide when to load the skill, and a body of operational directives
(mission, I/O contracts, execution steps, error handling) loaded only once
the skill is actually invoked. Each also links directly to the Python
module and script that implements it, and to the JSON Schema its output is
validated against.

## How the pipeline fits together

```
                       ┌──────────────────────────┐
                       │ brand-ai-readiness-audit  │   (orchestrator)
                       │  braiaudit.pipeline       │
                       └────────────┬─────────────┘
                                    │
      ┌───────────────┬────────────┼───────────────┬──────────────────┐
      ▼               ▼            ▼                ▼                  ▼
website-observer  crawl-render- content-cleaner  query-guided-   failure-
(cheap HTTP pass,  audit        (main-content     discovery       diagnostics
 always real)     (headless JS,  extraction,      (link scoring,  (ontology
                   optional      always real)      fragmentation,  matching,
                   Playwright                       always real)   always real)
                   extra)
      │               │            │                │                  │
      └───────────────┴────────────┴────────────────┴──────────────────┘
                                    ▼
                       ┌──────────────────────────┐
                       │  freshness-corroboration  │
                       │  braiaudit.report          │
                       │  (dedupe, assemble, emit) │
                       └────────────┬─────────────┘
                                    ▼
                         final audit report (JSON)
```

1. **`website-observer`** — a cheap, JS-free HTTP GET pass: status codes,
   headers, `robots.txt`, `sitemap.xml`, raw HTML size/script density,
   soft-404 phrase detection. Always runs first; alone it's often enough to
   catch compliance blocks before spending render budget.
2. **`crawl-render-audit`** — runs only for pages flagged as
   client-side-rendered or interaction-gated. With the optional `[render]`
   extra installed, executes real JS in headless Chromium, drives
   scroll/click interactions, traverses Shadow DOM, and detects
   JS-only navigation traps. Without it, reports the gap honestly instead
   of guessing.
3. **`content-cleaner`** — strips navigation, footers, ads, and overlay
   noise from whichever HTML is best available, using a lightweight
   readability-style text-density heuristic, and scores main-content
   density.
4. **`query-guided-discovery`** — scores internal links against target
   queries (via a dependency-free TF-cosine relevance scorer) to find where
   an answer is fragmented across subpages, and flags orphaned pages
   missing from the site's link graph *and* its sitemap.
5. **`failure-diagnostics`** — the shared brain: maps every raw signal
   bundle from the steps above onto the **Web Failure Ontology**
   (`skills/failure-diagnostics/references/ontology.yaml`, 17 failure modes
   across 5 categories) to produce named, severity-scored findings with
   suggested remediations. A failure mode fires only on its declared
   `match_mode` (`all` signals together, or `any` one of them) — never on a
   weaker partial overlap; see the ontology file's header comment.
6. **`freshness-corroboration`** — the final assembly step: groups findings
   by failure mode across every crawled page (not one entry per page-hit),
   corroborates evidence honestly (states the hit ratio, never inflates a
   one-page fluke into a site-wide claim), computes summary counts, and
   emits the final JSON matching the audit report floor schema.

## Audit Report Floor Schema

Every audit run emits (via `freshness-corroboration` /
`braiaudit.report.assemble_report`) a JSON document matching
[`schemas/audit-report.schema.json`](schemas/audit-report.schema.json):

```json
{
  "site": "example.com",
  "audited_at": "2026-09-02T00:00:00Z",
  "summary": {
    "total_findings": 3,
    "critical": 1,
    "high": 1,
    "medium": 1
  },
  "findings": [
    {
      "id": "F-001",
      "title": "Robots.txt Rules Block AI Scrapers",
      "severity": "critical",
      "evidence": "robots.txt: 'User-agent: * Disallow: /' on https://example.com/robots.txt",
      "suggested_action": {
        "summary": "Allow the audited/target AI user-agent(s) on public marketing routes, or scope Disallow rules to private paths only.",
        "priority": "critical"
      }
    }
  ]
}
```

## Testing & quality

```bash
pytest -q                                    # 56 tests, no network required
ruff check src tests tools                   # lint
python tools/lint_skills.py                  # SKILL.md / ontology / marketplace lint
braiaudit validate marketplace.json --schema marketplace
```

All four run in CI (`.github/workflows/ci.yml`) across Python 3.10–3.13.
Notably, `tests/test_ontology.py` enforces that the ontology and the code
never drift apart: every signal named in `ontology.yaml` must actually be
emitted by some producer module, checked by static analysis of the source.
See [CHANGELOG.md](CHANGELOG.md) for the false-positive bug this caught and
fixed during development, and [CONTRIBUTING.md](CONTRIBUTING.md) for the
workflow to follow when adding a new failure mode.

## Extending the ontology

New failure modes are added purely as data — append an entry to
`skills/failure-diagnostics/references/ontology.yaml` with `category`,
`severity`, `match_mode`, `signals`, `description`, `recovery_strategy`,
`recommended_tool`, and `audit_finding_title`. No pipeline code changes are
required elsewhere: `failure-diagnostics` (`braiaudit.ontology`) reads the
ontology at runtime — but the signal(s) you list must actually be emitted
by a producer module, or the new mode will simply never fire (enforced by
`tests/test_ontology.py`). See [CONTRIBUTING.md](CONTRIBUTING.md) for the
full checklist.

## License

[MIT](LICENSE) © Yash Ghule
