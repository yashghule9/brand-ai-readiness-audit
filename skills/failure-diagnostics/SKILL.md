---
name: failure-diagnostics
description: Maps raw technical signals collected by other skills (website-observer, crawl-render-audit, content-cleaner, query-guided-discovery) onto the Web Failure Ontology knowledge base (references/ontology.yaml) to produce scored, human-readable audit findings with severity, evidence, and suggested remediation. Use whenever you have a signal bundle (e.g. "raw_text_length < 500 and script_count > 15" or "robots_txt_disallow") and need to classify it into a named failure mode for the audit report — this is the shared classification brain every other skill routes signals through.
allowed-tools: Bash, Read
license: MIT
---

# Failure Diagnostics

> **Reference implementation:** `braiaudit.ontology.diagnose()`
> ([src/braiaudit/ontology.py](../../src/braiaudit/ontology.py)), runnable directly via
> `python skills/failure-diagnostics/scripts/diagnose.py < bundle.json`. The
> ontology itself is validated at load time against
> [schemas/ontology.schema.json](../../schemas/ontology.schema.json); findings
> are validated against
> [schemas/failure-diagnostics.output.schema.json](../../schemas/failure-diagnostics.output.schema.json).

## Two Knowledge Bases In One File

`references/ontology.yaml` holds both halves of the audit's advice:

- `failure_modes` — signal patterns that constitute a defect. Each declares
  an `axis`, and a `remediation` written **for the site owner**. Note that
  `recovery_strategy` / `recommended_tool` name *this auditor's* next step
  ("crawl-render-audit", "playwright") and must never be surfaced to a brand
  as advice — use `remediation`.
- `opportunities` — proactive recommendations emitted independently of any
  detected defect, so a clean audit still returns useful advice. Each is
  dropped when one of its `suppressed_by` failure modes fired, since that
  finding's own remediation is the more specific guidance. Opportunities
  carry no evidence and never affect the readiness score: they are advice,
  not observations about the site.

## Operational Mission

Convert loose, per-skill signal tokens into rigorous, named findings by
matching them against the **Web Failure Ontology**
(`skills/failure-diagnostics/references/ontology.yaml`). This skill owns the
mapping from "what was observed" to "what it means and what to do about
it" — no other skill should hard-code severities or finding titles; they
all defer to this ontology so the taxonomy stays in one place.

## Preconditions

- `references/ontology.yaml` is loaded and parsed before any classification
  is attempted. If it fails to parse, halt diagnostics for the run and
  surface that as its own `critical` operational finding rather than
  silently skipping classification.
- A signal bundle is supplied per URL/page, in the shape each upstream
  skill's Output Schema already produces (`signals: [...]` arrays plus
  whatever raw metrics justify them, used for the `evidence` string).

## Input Schema

```json
{
  "url": "https://example.com/",
  "signals": ["low_raw_text", "high_script_count", "root_container_detected"],
  "metrics": {
    "raw_text_length": 312,
    "script_count": 22
  }
}
```

Multiple such bundles (one per URL, and one per skill stage) are typically
supplied across an audit run; this skill classifies each independently and
returns a flat findings list for `freshness-corroboration` to deduplicate
across pages.

## Output Schema

```json
{
  "findings": [
    {
      "id": "F-001",
      "url": "https://example.com/",
      "failure_mode": "APP_SHELL_EMPTY_DOM",
      "category": "rendering_and_execution",
      "title": "JavaScript-Heavy Rendering Hides Content From Static Crawlers",
      "severity": "high",
      "evidence": "Static GET returned raw_text_length=312 (<500) with script_count=22 (>15) and a detected root container div — page appears CSR-only.",
      "suggested_action": {
        "summary": "Render via headless Chromium (crawl-render-audit) before extraction; consider server-side rendering or prerendering for AI crawlers.",
        "priority": "high"
      }
    }
  ]
}
```

## Step-by-Step Execution Sequence

1. **Load the ontology** once per audit run (not per URL) — cache
   `failure_modes` keyed by failure-mode name, each with its `signals` set.
2. **For each incoming signal bundle**, evaluate every ontology entry
   against its declared `match_mode` (defaults to `all` when the field is
   absent):
   - `match_mode: all` — every signal in the entry's `signals` list must be
     present in the bundle. Use this when the signals are only meaningful
     together (e.g. low text length alone is common on legitimately terse
     pages; it only means "CSR app shell" combined with a high script count
     *and* a detected root container).
   - `match_mode: any` — a single listed signal is sufficient. Use this
     when the signals are independent alternative symptoms of the same
     problem (e.g. a cookie banner *or* a newsletter modal are both,
     independently, an interrupt overlay).
   - **A failure mode never fires on a weaker partial overlap than its
     declared `match_mode` requires.** An earlier version of this skill
     surfaced any nonzero overlap as a lower-confidence "partial match" —
     in practice this produced false positives (a single `low_raw_text`
     signal alone tripping the CSR-app-shell finding on ordinary short
     pages) and was removed rather than tuned. If a mode's true criterion
     really is "any one of these," declare it as `match_mode: any`
     explicitly instead of relying on partial-match leniency.
   - No match → no finding for that failure mode; this is the expected
     common case, not an error. Signals matching no ontology entry's
     criterion at all are reported separately (see `unclassified_signals`
     in the Output Schema) rather than silently dropped.
3. **Rank matches** for a given URL by the ontology's declared `severity`
   (`critical` > `high` > `medium` > `low`), then by category declaration
   order in the ontology.
4. **Compose `evidence`** as a concrete, specific sentence referencing the
   actual observed metric values from `metrics` — never a generic
   restatement of the signal name. E.g. prefer "Crawled 12 pages; 0/12
   contain schema.org markup" over "missing structured data detected".
5. **Attach `suggested_action`** using the ontology's `recovery_strategy`
   and `recommended_tool` fields, phrased as an engineering-actionable
   summary, with `priority` mirroring `severity` unless the orchestrator
   has explicitly downgraded it (e.g. a `low`-traffic orphaned page).
6. **Assign sequential `id`s** (`F-001`, `F-002`, ...) in the order findings
   are finalized for the run — final renumbering/dedup across pages is
   `freshness-corroboration`'s responsibility, not this skill's; this skill
   may emit multiple findings for the same failure mode across different
   URLs.
7. **Return** the findings list for hand-off.

## Error Handling & Edge Cases

- **Signal not present in any ontology entry**: log it as an unclassified
  signal rather than silently dropping it — surface unclassified signals in
  a side channel so the ontology can be extended later, but do not invent a
  finding for it.
- **Ontology file missing/corrupt**: do not fall back to a hard-coded
  duplicate list embedded in this skill. Fail loudly with a single
  operational finding: "Failure ontology unavailable — findings could not
  be classified for this run."
- **Conflicting matches** (two failure modes match the same bundle
  strongly, e.g. `APP_SHELL_EMPTY_DOM` and `JAVASCRIPT_NAVIGATION_TRAP`):
  emit both — they are not mutually exclusive real-world conditions — but
  note the overlap in `evidence` if they share the same root signals.
- **Severity ties**: preserve stable ordering by category order as declared
  in the ontology (`compliance_and_access` findings should generally surface
  first in a human read-out even at equal severity, since they are hard
  boundaries).
- **Never** override an ontology-declared `severity` based on guesswork —
  if a case seems to warrant a different severity, that is a signal the
  ontology entry itself needs updating, not that this skill should diverge
  from it at runtime.
