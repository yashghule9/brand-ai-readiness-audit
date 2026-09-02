# Brand AI Readiness Audit

A modular [Claude Agent Skill](https://docs.claude.com/en/docs/agents/skills)
set that audits a brand's website for **AI readiness** — how reliably its
content can be reached, rendered, cleaned, and understood by AI search
crawlers, RAG ingestion pipelines, and autonomous browsing agents.

The audit produces a single structured JSON report: a ranked list of
findings (crawl-blocking robots rules, JS-only rendering, missing
`schema.org` markup, fragmented information, boilerplate noise, etc.), each
tied to a concrete signal, a severity, and a suggested engineering fix.

## Architecture

```
brand-ai-readiness-audit/
├── SKILL.md                          # Master Orchestrator Skill
├── README.md                         # This file
├── marketplace.json                  # Manifest listing all active skills
└── skills/
    ├── website-observer/
    │   └── SKILL.md                  # Low-overhead HTTP/DOM inspection
    ├── failure-diagnostics/
    │   ├── SKILL.md                  # Signal-to-Ontology mapping
    │   └── references/
    │       └── ontology.yaml         # Web Failure Ontology Knowledge Base
    ├── crawl-render-audit/
    │   └── SKILL.md                  # Headless Playwright/Chromium rendering
    ├── content-cleaner/
    │   └── SKILL.md                  # Main-content extraction & noise removal
    ├── query-guided-discovery/
    │   └── SKILL.md                  # Multi-page internal link scoring
    └── freshness-corroboration/
        └── SKILL.md                  # Audit evidence assembly & JSON output
```

Each `SKILL.md` follows the two-level Claude Skill disclosure pattern: a
YAML frontmatter (`name` + trigger `description`) that Claude scans to
decide when to load the skill, and a body of operational directives
(mission, I/O contracts, execution steps, error handling) loaded only once
the skill is actually invoked.

## How the pipeline fits together

```
                       ┌──────────────────────────┐
                       │ brand-ai-readiness-audit  │   (orchestrator)
                       └────────────┬─────────────┘
                                    │
      ┌───────────────┬────────────┼───────────────┬──────────────────┐
      ▼               ▼            ▼                ▼                  ▼
website-observer  crawl-render- content-cleaner  query-guided-   failure-
(cheap HTTP pass)  audit        (main-content     discovery       diagnostics
                  (headless JS   extraction)       (link scoring, (ontology
                   rendering)                       fragmentation) mapping)
      │               │            │                │                  │
      └───────────────┴────────────┴────────────────┴──────────────────┘
                                    ▼
                       ┌──────────────────────────┐
                       │  freshness-corroboration  │
                       │  (dedupe, assemble, emit) │
                       └────────────┬─────────────┘
                                    ▼
                         final audit report (JSON)
```

1. **`website-observer`** — a cheap, JS-free HTTP GET pass: status codes,
   headers, `robots.txt`, `sitemap.xml`, raw HTML size/script density. This
   is always the first step, and alone is often enough to catch compliance
   blocks before spending render budget.
2. **`crawl-render-audit`** — runs only for pages flagged as
   client-side-rendered or interaction-gated. Executes JS in headless
   Chromium, drives scroll/click interactions, and traverses shadow DOM.
3. **`content-cleaner`** — strips navigation, footers, ads, and overlay
   noise from whichever HTML is best available, scoring main-content
   density.
4. **`query-guided-discovery`** — scores internal links against target
   queries to find where an answer is fragmented across subpages, and flags
   orphaned pages missing from the site's link graph and sitemap.
5. **`failure-diagnostics`** — the shared brain: maps every raw signal
   bundle from the steps above onto the **Web Failure Ontology**
   (`skills/failure-diagnostics/references/ontology.yaml`) to produce named,
   severity-scored findings with suggested remediations.
6. **`freshness-corroboration`** — the final assembly step: deduplicates
   repeated findings, corroborates that a finding is real (not a one-page
   fluke), computes summary counts, and emits the final JSON matching the
   audit report floor schema.

## Audit Report Floor Schema

Every audit run emits (via `freshness-corroboration`) a JSON document of
this shape:

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

## Quickstart

1. Load this repository as a Claude Skills marketplace (see
   `marketplace.json`) or point a Claude Agent session at this directory.
2. Ask Claude to run a brand AI readiness audit, e.g.:
   > "Run a brand AI readiness audit on example.com"
3. Claude loads `SKILL.md` (the orchestrator), sequences the five capability
   skills, and returns the final JSON report described above, alongside a
   short human-readable summary.

## Extending the ontology

New failure modes are added purely as data — append an entry to
`skills/failure-diagnostics/references/ontology.yaml` with `severity`,
`signals`, `recovery_strategy`, `recommended_tool`, and
`audit_finding_title`. No code changes are required elsewhere in the
pipeline: `failure-diagnostics` reads the ontology at runtime.
