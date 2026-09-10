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

## For evaluators — the 30-second version

```bash
pip install -e .
braiaudit audit example.com
```

The domain is the only required argument. No flags, no API keys, no config
file, no database, no server — the audit runs in memory over plain HTTP and
prints a complete JSON report to stdout. A headless browser is optional; if
it is absent, render-dependent checks are reported as an explicit coverage
gap rather than silently skipped.

Opening this repository in Claude Code instead? The skill auto-loads from
`.claude/skills/` — just ask: *"run a brand AI readiness audit on
example.com"*.

Run the test suite with `pytest -q` (102 tests, no network required).

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

Useful flags: `--max-depth 2` (how far to follow internal links from the
seed URLs — depth 1 is the main nav, depth 2 reaches product/detail pages),
`--query "what does this product cost"` (repeatable — tests
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

Claude loads the entrypoint skill [`skills/brand-ai-readiness-audit/SKILL.md`](skills/brand-ai-readiness-audit/SKILL.md), sequences the six capability
skills under `skills/` via its own tool use (WebFetch, a browser tool,
etc.) instead of the Python pipeline, and returns the same floor-schema
JSON. Every skill's `SKILL.md` names the exact reference-implementation
module and script it corresponds to, if you want to compare behavior or
run the deterministic parts directly instead of re-deriving them via
tool calls.

## The marketplace: seven skills, one entrypoint

`marketplace.json` lists every skill and marks exactly one as the entrypoint.
Each skill folder holds an agentskills.io-compliant `SKILL.md` (frontmatter
with `name`, `description`, `allowed-tools`, `license`), with detailed
knowledge pushed to `references/` and executable checks to `scripts/`.

| Skill | Concern it owns |
| --- | --- |
| **`brand-ai-readiness-audit`** *(entrypoint)* | Receives the audit request, enforces preconditions, sequences the six skills below, and emits the single audit report. The only skill that produces the final JSON. |
| `website-observer` | One cheap JS-free HTTP pass: status, headers, `robots.txt` evaluated against every AI answer-engine crawler, `sitemap.xml`, static-HTML metrics, JSON-LD parsing, internal-link extraction. |
| `crawl-render-audit` | Headless Chromium only where static signals justify it: scroll-driven lazy load, tab/accordion interaction, Shadow DOM, JS navigation traps. Optional — degrades to a declared coverage gap. |
| `content-cleaner` | Boilerplate and overlay removal, main-content scoring, semantic-structure and question-register analysis, above-the-fold answer density. |
| `query-guided-discovery` | Whether a target question is answerable at all: link-graph scoring, multi-page fragmentation, orphaned pages. |
| `failure-diagnostics` | The shared brain. Maps raw signals onto the Web Failure Ontology (`references/ontology.yaml`) to produce named, severity-scored findings. Owns no I/O. |
| `freshness-corroboration` | Cross-page corroboration, coverage and structural meta-analysis, and final schema-conformant assembly. |

### How the entrypoint composes them

The entrypoint owns sequencing and hand-off contracts, not detection. It
runs **observe → render (conditional) → clean → discover → diagnose →
corroborate**, and the separation is real rather than cosmetic:

- **Only `website-observer` and `crawl-render-audit` touch the network.**
  `content-cleaner`, `failure-diagnostics` and `freshness-corroboration` are
  pure transforms over data already collected, which is why they are
  deterministic and unit-testable without mocking HTTP.
- **Only `failure-diagnostics` decides what counts as a problem.** Every
  other skill emits raw signal strings and never a verdict, so severity
  policy lives in exactly one file — a new failure mode is a YAML edit, not
  a code change.
- **Only `freshness-corroboration` emits the report.** Findings are grouped
  by failure mode rather than repeated per page, so a sitewide issue is one
  entry with a corroboration ratio.
- **`crawl-render-audit` is skipped by default**, and its absence is
  reported as an explicit coverage gap rather than passed off as clean.

## Architecture

```
brand-ai-readiness-audit/
├── README.md                         # This file
├── marketplace.json                  # Manifest: every skill + the one entrypoint
├── pyproject.toml                    # `braiaudit` package definition
├── schemas/                          # JSON Schemas for every I/O contract (source of truth)
├── src/braiaudit/                    # Reference implementation, one module per skill
├── tests/                            # pytest suite (102 tests) + HTML fixtures
├── tools/lint_skills.py              # CI-enforced SKILL.md / ontology / marketplace linter
├── .github/workflows/ci.yml          # lint + skill-lint + schema-validate + pytest, py3.10–3.13
└── skills/
    ├── brand-ai-readiness-audit/     # ENTRYPOINT — composes the six below
    │   └── SKILL.md
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
   (`skills/failure-diagnostics/references/ontology.yaml`, 41 failure modes
   — 38 scored defects plus 3 unscored limitations — across 5 categories and 4
   brand-facing axes) to produce named, severity-scored findings with
   suggested remediations. A failure mode fires only on its declared
   `match_mode` (`all` signals together, or `any` one of them) — never on a
   weaker partial overlap; see the ontology file's header comment.
6. **`freshness-corroboration`** — the final assembly step: groups findings
   by failure mode across every crawled page (not one entry per page-hit),
   corroborates evidence honestly (states the hit ratio, never inflates a
   one-page fluke into a site-wide claim), computes summary counts, and
   emits the final JSON matching the audit report floor schema. It also
   runs a **meta-analysis stage** — a different question than "what's
   wrong," namely "are our conclusions correct, consistent, and
   well-supported": ontology **coverage measurement** (which failure modes
   could actually be checked given which skills ran this audit — so a
   never-installed render backend reads as "not evaluated," never as
   silently clean) plus structural **validation** (no duplicate/out-of-order
   ids, summary counts recomputable from the findings array) and
   informational **correlation notes** for findings that may share a root
   cause. See `meta.coverage` / `meta.validation` in the schema below.

The ontology sits underneath this pipeline purely as a **knowledge/taxonomy
layer** — `braiaudit.ontology` only loads and matches `ontology.yaml`, it
never fetches, renders, or cleans anything itself. Execution always lives
in the skill modules; the ontology's only job is naming and scoring what
they find. `skills_engaged` — the set of producer skills that actually ran
this audit — is threaded from `pipeline.py` through to
`coverage.compute_coverage()` for exactly this reason: coverage is a fact
about what ran, not about what the ontology contains.

## Audit Report Floor Schema

Every audit run emits (via `freshness-corroboration` /
`braiaudit.report.assemble_report`) a JSON document matching
[`schemas/audit-report.schema.json`](schemas/audit-report.schema.json):

```json
{
  "site": "example.com",
  "audited_at": "2026-09-05T00:00:00Z",
  "summary": { "total_findings": 3, "critical": 1, "high": 1, "medium": 1 },
  "readiness": {
    "score": 58,
    "by_axis": {
      "visibility":  { "score": 63, "findings": 2 },
      "staleness":   { "score": 95, "findings": 1 },
      "engagement":  { "score": 100, "findings": 0 },
      "identity":    { "score": 100, "findings": 0 }
    },
    "formula": "100 minus 25 per critical, 12 per high, 5 per medium and 2 per low finding, floored at 0. Proactive suggestions never affect the score."
  },
  "findings": [
    {
      "id": "F-001",
      "title": "Robots.txt Blocks AI Answer-Engine Crawlers",
      "severity": "critical",
      "axis": "visibility",
      "evidence": "robots.txt disallows 2 AI crawler(s): ClaudeBot, GPTBot, while still allowing Bingbot, Googlebot (confirmed on 1/1 page(s) attempted)",
      "suggested_action": { "summary": "...", "priority": "critical" },
      "affected_urls": ["https://example.com/"],
      "category": "compliance_and_access"
    }
  ],
  "suggested_actions": [
    { "id": "A-001", "priority": "critical", "axis": "visibility",
      "summary": "Remove or narrow the Disallow rules for GPTBot and ClaudeBot...",
      "rationale": "Addresses F-001: Robots.txt Blocks AI Answer-Engine Crawlers.",
      "derived_from": "F-001" },
    { "id": "A-004", "priority": "high", "axis": "staleness",
      "summary": "Add dateModified and datePublished to the JSON-LD on every page whose facts change...",
      "rationale": "Without a machine-readable date, a 2019 snapshot and a 2024 page look equally current...",
      "derived_from": "proactive" }
  ]
}
```

`meta` is additive — a consumer that only reads `site` / `summary` /
`findings` keeps working unmodified. It's what lets a reader distinguish
"we checked for Shadow DOM issues and found none" from "we never checked
for Shadow DOM issues" (no render backend installed, in the example
above) — the same findings array either way, but a very different claim.

## Testing & quality

```bash
pytest -q                                    # 102 tests, no network required
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
