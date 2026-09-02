---
name: freshness-corroboration
description: Assembles all evidence and findings gathered across the audit pipeline (website-observer, crawl-render-audit, content-cleaner, query-guided-discovery, failure-diagnostics) into the final structured JSON audit report — deduplicating and corroborating repeated findings across pages so a single-page fluke doesn't get reported as a site-wide problem, computing summary severity counts, timestamping the audit, and validating the result against the audit report floor schema. Use as the LAST step of a Brand AI Readiness Audit, once every other skill has run and raw findings need to be compiled into the final output.
---

# Freshness & Corroboration

> **Reference implementation:** `braiaudit.report.assemble_report()`
> ([src/braiaudit/report.py](../../src/braiaudit/report.py)), runnable directly via
> `python skills/freshness-corroboration/scripts/assemble.py < request.json`.
> The emitted report is validated against
> [schemas/audit-report.schema.json](../../schemas/audit-report.schema.json) — the
> authoritative floor schema — before being returned.

## Operational Mission

Be the single point where the audit's final JSON is produced. Take the raw,
possibly-overlapping, possibly-per-page findings emitted by
`failure-diagnostics` across every crawled URL, and turn them into one
clean, deduplicated, correctly-numbered, schema-conformant report — while
also judging whether a finding is *corroborated* (seen consistently, or
structurally certain) versus a one-off anomaly that shouldn't be reported
at the same severity as a site-wide pattern.

## Preconditions

- Every other skill in the pipeline has completed for the URLs in scope
  (or has explicitly reported a stop condition, e.g. robots disallow, that
  the orchestrator has decided to treat as final for that URL).
- The full findings list from `failure-diagnostics` (potentially many
  findings per URL, across many URLs) is available as input, along with the
  `site` identifier and the audit start time.

## Input Schema

```json
{
  "site": "example.com",
  "findings_by_url": {
    "https://example.com/": [
      { "failure_mode": "MISSING_STRUCTURED_DATA", "title": "No Schema.org / JSON-LD Structured Data Available", "severity": "high", "evidence": "0/1 pages contain JSON-LD" }
    ],
    "https://example.com/pricing": [
      { "failure_mode": "MISSING_STRUCTURED_DATA", "title": "No Schema.org / JSON-LD Structured Data Available", "severity": "high", "evidence": "0/1 pages contain JSON-LD" }
    ]
  },
  "pages_crawled": 12,
  "pages_unreachable": ["https://example.com/blocked"]
}
```

## Output Schema (Audit Report Floor Schema — authoritative)

```json
{
  "site": "example.com",
  "audited_at": "2026-09-02T00:00:00Z",
  "summary": {
    "total_findings": 2,
    "critical": 0,
    "high": 1,
    "medium": 1
  },
  "findings": [
    {
      "id": "F-001",
      "title": "No Schema.org / JSON-LD Structured Data Available",
      "severity": "high",
      "evidence": "Crawled 12 pages; 0/12 contain schema.org markup.",
      "suggested_action": {
        "summary": "Add JSON-LD structured data (Organization, Product, FAQPage as applicable) to primary templates.",
        "priority": "high"
      }
    }
  ]
}
```

This schema is the **floor** — every field shown is required and must be
present with the correct type on every finding. Do not add top-level fields
that could break a strict downstream consumer; extra per-finding detail
(e.g. `affected_urls`) may be included only as additive fields, never in
place of the required ones.

## Step-by-Step Execution Sequence

1. **Group findings by `failure_mode`** across all crawled URLs (not by
   URL) — the report's `findings` array is one entry *per failure mode*
   observed for the site, not one entry per page-hit. A site with 12 pages
   all missing structured data produces one `MISSING_STRUCTURED_DATA`
   finding, not twelve.
2. **Corroborate each group**:
   - If a failure mode was observed on multiple pages, merge its evidence
     into a single aggregate sentence (e.g. "Crawled 12 pages; 0/12 contain
     schema.org markup") rather than concatenating twelve near-identical
     evidence strings.
   - If a failure mode was observed on exactly one page out of many
     crawled, keep it but make the evidence explicit about the ratio
     (e.g. "1/12 pages returned a soft-404 pattern: /legacy-promo") so the
     reader isn't misled into thinking it's site-wide.
   - `compliance_and_access` category findings (robots disallow, rate
     limiting, anti-bot challenges) are never downgraded by low sample
     size — a single confirmed `USER_AGENT_ROBOTS_DISALLOW` is fully
     corroborated by definition; it is a policy statement, not a
     statistical pattern.
3. **Recompute severity per merged finding**: use the ontology's declared
   severity for the failure mode (via `failure-diagnostics`) as the floor;
   do not let aggregation invent a new severity level not present in the
   ontology.
4. **Assign final sequential IDs** (`F-001`, `F-002`, ...), ordered:
   `critical` → `high` → `medium` → `low`, and within a tier, by category
   order matching the ontology (`compliance_and_access` and
   `rendering_and_execution` findings surfaced before purely cosmetic ones
   at the same severity).
5. **Compute `summary`**: `total_findings` plus the count at each of
   `critical`/`high`/`medium` (per the floor schema's exact fields — note
   `low` is not a summary key in the floor schema even though it is a valid
   per-finding severity; still include `low`-severity findings in the
   `findings` array and in `total_findings`, just not as their own summary
   counter, matching the schema exactly as specified).
6. **Set `audited_at`** to the audit's actual run time in strict ISO-8601
   UTC (`YYYY-MM-DDTHH:MM:SSZ`).
7. **Validate** the assembled object against the floor schema field-by-field
   (required keys present, correct types, `severity`/`priority` values
   constrained to the enum) before returning it. If validation fails, fix
   the assembly — never return a non-conformant object with an apology
   attached.

## Error Handling & Edge Cases

- **Zero findings across the entire audit**: still emit a fully-formed
  report with `total_findings: 0` and an empty `findings` array — this is a
  valid, good outcome, not an error to explain away.
- **Unreachable pages** (`pages_unreachable`): do not let their absence
  silently deflate evidence ratios (e.g. don't say "0/10" when 12 were
  attempted and 2 failed to load) — state both the crawled count and the
  unreachable count in evidence when it's materially relevant to a finding.
- **Conflicting findings for the same URL** (e.g. both `APP_SHELL_EMPTY_DOM`
  and `JAVASCRIPT_NAVIGATION_TRAP`): keep both as separate entries in the
  final report — do not merge unrelated failure modes just because they
  came from the same page.
- **A finding's severity conflicts with orchestrator-level context** (e.g.
  a `MISSING_STRUCTURED_DATA` finding on a single low-value orphaned page
  vs. the homepage): reflect that distinction in `evidence`/scope rather
  than silently changing `severity` — the ontology's severity is about the
  failure mode's inherent risk, not this particular page's business
  importance.
- **Downstream schema drift** (a consumer expects additional fields not in
  the floor schema): only add fields additively and document them; never
  remove or rename a floor-schema field to accommodate a specific consumer.
