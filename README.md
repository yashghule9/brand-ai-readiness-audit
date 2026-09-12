# Brand AI Readiness Audit

An [Agent Skill](https://agentskills.io) marketplace that audits any website
for **AI readiness** — why AI assistants may not find, read, quote, or
trust its content, and why visitors who arrive don't engage — and emits one
structured JSON report of evidence-backed findings with prioritized fixes.

`marketplace.json` lists eight skills; exactly one,
**`brand-ai-readiness-audit`**, is the entrypoint. It receives the audit
request and composes the other seven; its reference implementation is
`braiaudit.pipeline` (`pip install -e .`, then `braiaudit audit example.com`).
Recommend-only: the audit performs read-only HTTP GETs, respects
`robots.txt`, and never modifies a site.

## The audit pipeline: six stages

1. **Fetch and Crawl** — `website-observer` (`braiaudit.fetch`) runs the
   always-on crawl: breadth-first over internal links with a page budget and
   runtime budget, per-URL `robots.txt` evaluation against our agent and
   every major AI crawler, sitemap discovery (index files expanded into
   their child sitemaps), duplicate-URL canonicalization, and one `www.`
   fallback for an unreachable apex. `crawl-render-audit`
   (`braiaudit.render`) adds headless Chromium rendering only where static
   HTML looks empty or interaction-gated; without the optional `[render]`
   extra installed its absence is reported as a declared limitation, never
   scored as a defect.
2. **Observe** — the same pass extracts the raw signals every later stage
   consumes: status codes and headers, soft-404 and anti-bot-challenge
   detection, JSON-LD blocks and their freshness/identity properties,
   internal links, script density, meta description, headings, breadcrumbs,
   contact markers, copyright year. Error stubs and challenge pages are
   halted here so they are never analysed as the site's content.
3. **Clean and Analyze** — `content-cleaner` (`braiaudit.clean`) strips
   navigation, overlays, and boilerplate, then scores main-content density,
   heading structure, question-register alignment, above-the-fold answer
   presence, and noise ratio. `query-guided-discovery`
   (`braiaudit.discovery`) tests whether a target question is answerable at
   all: TF-cosine link scoring, multi-page fragmentation, and orphaned pages
   missing from both the link graph and the sitemap.
4. **Corroborate** — `freshness-corroboration` (`braiaudit.corroborate`)
   checks the brand facts the site declares against public records:
   Wikipedia/Wikidata entity existence and official-site reciprocity, and a
   Wayback Machine first-seen date. Robots-honouring and read-only; absence
   is claimed only after every name variant was tried, and an unreachable
   source reads as "unknown", never as evidence against the brand.
5. **Score and Explain** — `failure-diagnostics` (`braiaudit.ontology` plus
   `references/ontology.yaml`) is the only place signals become verdicts:
   41 failure modes (38 scored defects + 3 unscored limitations) across 5
   categories and 4 axes, each finding carrying measured evidence, a
   severity, a confidence, and a mechanism-sound suggested action.
   `braiaudit.report` then assembles the final floor-schema JSON: findings
   grouped by failure mode with honest corroboration ratios, axis scores
   computed only from evidence actually obtained (`readiness_score: null`
   when nothing was analysable — an abstention, not a zero), opportunities,
   and limitations.
6. **Validate and Export** — every stage's output is validated against the
   JSON Schemas in `schemas/` (the report also carries a coverage block
   recording which checks ran and which could not, so "not evaluated" is
   never mistaken for "clean"). The report is exported as JSON to stdout or
   `--output report.json`, and any report can be re-validated with
   `braiaudit validate report.json --schema audit-report`.

Only `website-observer`, `crawl-render-audit`, and the corroboration lookups
touch the network. Cleaning, diagnosis, scoring, and assembly are pure
transforms over collected evidence — deterministic and unit-testable without
HTTP. The eighth skill, `assistant-representation` (experimental, Phase 3),
reads a *completed* report and observes how an assistant represents the
brand; it contributes no findings and never affects the score.

## Quickstart

```bash
pip install -e .
braiaudit audit example.com                    # full JSON report to stdout
braiaudit audit example.com --max-pages 5 --max-render-pages 3 --query "what does this cost"
pip install -e ".[render]" && playwright install chromium   # optional rendering
pytest -q                                      # 247 tests, no network required
```

License: MIT — see [LICENSE](LICENSE). Change history: [CHANGELOG.md](CHANGELOG.md).
