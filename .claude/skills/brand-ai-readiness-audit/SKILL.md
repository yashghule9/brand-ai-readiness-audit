---
name: brand-ai-readiness-audit
description: Audit a brand's website for AI readiness — whether AI assistants (ChatGPT, Gemini, Claude, Perplexity, Copilot) can reach, render, read, trust and correctly attribute its content. Use when the user asks to "audit my site for AI readiness", "run a brand AI audit on <domain>", "why doesn't my brand appear in ChatGPT", "why is AI showing outdated information about us", or asks why visitors arriving from an AI assistant bounce. Produces a JSON report of findings with evidence and severity, plus prioritised suggested actions.
---

# Brand AI Readiness Audit

Auto-discovery entry point for this repository. The full specification is
[`skills/brand-ai-readiness-audit/SKILL.md`](../../../skills/brand-ai-readiness-audit/SKILL.md) — read it and follow
its six-step Execution Sequence. The six capability specs it sequences live
under [`skills/`](../../../skills/), and the failure ontology it classifies
against is
[`skills/failure-diagnostics/references/ontology.yaml`](../../../skills/failure-diagnostics/references/ontology.yaml).

## Fastest path: run the reference implementation

The deterministic pipeline is already built and tested. Prefer it over
re-deriving the audit through tool calls — it is reproducible, it respects
`robots.txt` throughout, and it needs no configuration:

```bash
pip install -e .
braiaudit audit <domain>
```

The only required argument is the domain. Everything else has a working
default: it crawls breadth-first to depth 2, up to 15 pages, renders at most
3 of them, stops at a 240s wall-clock budget, and prints the complete JSON
report to stdout. Add `--query "..."` (repeatable) to test
brand-specific customer questions instead of the generic default set, and
`--output report.json` to write to a file.

No API keys, no database, no server, no configuration file. The audit runs
in memory over plain HTTP. Headless rendering is optional
(`pip install -e ".[render]" && playwright install chromium`); without it,
render-dependent checks are reported as an explicit coverage gap rather
than silently skipped.

## What comes back

Two substantive halves, both required:

- `findings` — problems detected, each with machine-observed evidence, a
  severity, and the `axis` it belongs to (`visibility`, `staleness`,
  `engagement`, `identity`).
- `suggested_actions` — what to change, prioritised. A superset of the
  findings' fixes: entries with `derived_from: "proactive"` are
  recommendations that apply even where no defect was found.

`readiness` carries a 0–100 score with a per-axis breakdown and states its
own formula. `meta.coverage` reports how much of the ontology was actually
evaluated, so a check that could not run reads as "not evaluated" rather
than as "clean".

## Where Claude adds judgment

The Python pipeline measures; it never guesses. Claude reads its JSON and
adds what deterministic code cannot:

- **Choose brand-specific `target_queries`** from the site's actual products
  and category before running, rather than accepting the generic defaults.
  This is an input decision made once up front, so the run stays
  reproducible.
- **Interpret the findings for this brand** — which fix matters most given
  what the company sells, and what the pattern across findings implies.
- **Add brand-specific proactive advice** beyond the ontology's built-in
  opportunities.

Never invent evidence, severities, or findings the pipeline did not
produce. Evidence must stay machine-observed — that is what makes the
report defensible.

## Boundaries

This audit observes a site from the outside only. It installs nothing on
the target, requires no access, and reads no analytics or server logs.
Recommendations may go beyond what it can measure (segmenting bounce rate
by referrer, sampling what assistants actually say) — say plainly that
those are for the brand to instrument, not results this audit produced.
