---
name: brand-ai-readiness-audit
description: Orchestrates a full Brand AI Readiness Audit of a website — evaluating how reliably its content can be discovered, rendered, cleaned, and semantically understood by AI crawlers, RAG ingestion pipelines, and autonomous browsing agents. Use this skill when the user asks to "audit my site for AI readiness", "check if my website is crawlable by LLMs/AI agents", "run a brand AI audit on <domain>", or asks for a structured report on AI-crawler compatibility. This skill coordinates website-observer, crawl-render-audit, content-cleaner, query-guided-discovery, failure-diagnostics, and freshness-corroboration, and is the only skill that emits the final audit JSON.
---

# Brand AI Readiness Audit — Master Orchestrator

> **Reference implementation:** `braiaudit.pipeline.run_audit()`
> ([src/braiaudit/pipeline.py](src/braiaudit/pipeline.py)) implements this exact
> six-step sequence end to end and is runnable directly:
> `braiaudit audit example.com` (see [README.md](README.md) Quickstart for
> install). It runs fully without any browser installed, degrading
> render-dependent checks to an honest `RENDER_COVERAGE_GAP` finding; install
> the optional `[render]` extra for full JavaScript-rendering coverage. When
> Claude drives the skills itself via tool use instead of this CLI, this
> document is the spec that sequence must follow.

## Operational Mission

Determine, for a given site (a domain or a small set of seed URLs), whether AI
systems — search-grounding crawlers, RAG ingestion jobs, and autonomous
browsing agents — can reliably **reach**, **render**, **read**, and
**trust** the brand's content. Produce one deterministic JSON report per
audit run, no exceptions to the schema.

This skill does not itself fetch pages, render DOMs, or classify failures —
it **sequences the five capability skills** below and is responsible only
for: preconditions, hand-off contracts between skills, short-circuit logic,
and final schema conformance (delegated to `freshness-corroboration` for
assembly, but owned by this skill for validation).

## Preconditions

- A target `site` (bare domain, e.g. `example.com`) or explicit seed URL list
  is provided. If neither is given, ask the user for one before proceeding —
  do not guess a domain.
- Network access to fetch the target is available. If it is not, halt and
  emit a single `critical` finding (`NETWORK_UNAVAILABLE`) via
  `failure-diagnostics` rather than a partial silent report.
- Respect for `robots.txt` and rate limits is non-negotiable for every
  downstream skill (see `USER_AGENT_ROBOTS_DISALLOW` in the ontology). This
  orchestrator halts the crawl the moment such a boundary is confirmed — it
  does not attempt to route around it.

## Input Contract

```json
{
  "site": "example.com",
  "seed_urls": ["https://example.com/"],
  "target_queries": ["what does this product cost", "how do I contact support"],
  "max_pages": 15,
  "options": {
    "respect_robots": true,
    "max_render_pages": 5,
    "user_agent": "BrandAIReadinessAuditBot/1.0"
  }
}
```

- `seed_urls` defaults to `["https://{site}/"]` when omitted.
- `target_queries` are optional prompts used by `query-guided-discovery` to
  test whether real-world AI queries about the brand can be answered from the
  crawled content. When omitted, discovery still runs using structural
  heuristics (link density, sitemap coverage) but skips answer-completeness
  scoring.

## Output Contract

The orchestrator's final emission MUST validate against the audit report
floor schema (produced via `freshness-corroboration`):

```json
{
  "site": "example.com",
  "audited_at": "2026-09-02T00:00:00Z",
  "summary": { "total_findings": 0, "critical": 0, "high": 0, "medium": 0 },
  "findings": [
    {
      "id": "F-001",
      "title": "Exact Title from Failure Ontology",
      "severity": "critical | high | medium | low",
      "evidence": "Raw observed metrics",
      "suggested_action": { "summary": "...", "priority": "critical | high | medium | low" }
    }
  ]
}
```

Do not emit prose in place of this JSON as the final artifact. Prose summary
(a short human read-out) may precede the JSON block for the user, but the
JSON block itself must be complete and standalone.

## Execution Sequence

1. **Observe** — invoke `website-observer` against every seed URL. This is
   the cheapest step and must run first for all URLs before any rendering is
   attempted. Collect: HTTP status, headers, `robots.txt` rules, sitemap
   presence, raw HTML, `raw_text_length`, `script_count`, root-container
   detection.
   - If `robots.txt` disallows the target user-agent for a URL, do **not**
     proceed to render or crawl that URL. Route the signal straight to
     `failure-diagnostics` (→ `USER_AGENT_ROBOTS_DISALLOW`) and drop the URL
     from the remaining pipeline.
   - If HTTP status is `429` or a CAPTCHA/anti-bot signature is detected,
     likewise stop on that URL, route to `failure-diagnostics`
     (→ `HTTP_429_RATE_LIMIT` / `ANTI_BOT_CAPTCHA_CHALLENGE`), and never
     retry more than once with backoff.

2. **Render (conditional)** — for any URL whose `website-observer` signals
   suggest client-side rendering or interaction-gated content (low
   `raw_text_length`, high `script_count`, root container div, `data-src`
   attributes, tab/accordion markup), invoke `crawl-render-audit`. Skip this
   step for URLs that already yielded substantial static text — do not waste
   render budget (`max_render_pages`) on pages that don't need it.

3. **Clean** — invoke `content-cleaner` on the best available HTML for each
   URL (rendered DOM if step 2 ran, otherwise raw HTML from step 1). Obtain
   clean text, `main_text_to_markup_ratio`, and semantic-structure presence.

4. **Discover** — invoke `query-guided-discovery` using the cleaned content,
   the site's internal link graph, `sitemap.xml` (from step 1), and any
   `target_queries`. This determines whether information is fragmented
   across subpages and whether any pages are orphaned. Respect `max_pages`.

5. **Diagnose** — for every raw signal bundle produced in steps 1–4, invoke
   `failure-diagnostics` to map signals onto the Web Failure Ontology and
   produce candidate findings with severity, evidence, and suggested action.

6. **Corroborate, Meta-Analyze & Assemble** — invoke `freshness-corroboration`
   with the full findings set to deduplicate repeated findings across
   pages, confirm findings are still current (not stale one-page flukes),
   run the meta-analysis stage (ontology coverage measurement — which
   failure modes could actually be checked given which skills ran this
   audit — plus structural self-validation of the assembled findings),
   compute the summary counts, and emit the final schema-conformant JSON.
   Track, across steps 1–5, which skills actually ran for each URL (not
   just which were available) — `freshness-corroboration` needs that to
   report coverage honestly rather than assuming everything was checked.

## Error Handling & Edge Cases

- **Total network failure on the seed URL**: do not fabricate signals or
  findings for pages never fetched. Emit a single `critical` finding
  documenting the failure and stop.
- **Partial crawl failure** (some subpages unreachable mid-audit): continue
  the pipeline with what was collected; note the unreachable URLs in the
  relevant finding's `evidence` field rather than silently omitting them.
- **Ambiguous or non-existent domain**: ask the user to confirm the target
  rather than auditing a guessed variant.
- **Skill unavailable / tool execution fails** (e.g. headless browser not
  installed): degrade gracefully — report the static-only findings from
  `website-observer` and `content-cleaner`, and add a `medium` finding
  noting that render-dependent checks (`APP_SHELL_EMPTY_DOM`,
  `LAZY_LOAD_TRIGGER_REQUIRED`, etc.) could not be verified, rather than
  omitting them or guessing a verdict. This degradation must also show up
  in the final report's `meta.coverage` — a skill that never ran is a
  `not_evaluated` failure mode there, not a silently-passed one.
- **Schema conformance failure**: never return the final report if it does
  not validate against the floor schema. Re-run `freshness-corroboration`
  assembly rather than hand-patching JSON inline.
