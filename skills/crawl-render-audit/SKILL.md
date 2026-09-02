---
name: crawl-render-audit
description: Performs full headless-browser (Playwright/Chromium) rendering of a page — executing JavaScript, waiting for DOM-settle/network-idle, driving scroll to trigger lazy-loaded content, clicking through tabs/accordions/"Load More" controls, and traversing Shadow DOM trees — to capture the page as an AI agent with JS execution would actually see it. Use this when website-observer flags an app-shell/CSR page (low raw text, high script count, root container div), when content is gated behind lazy-load or required UI interaction, or when internal links use non-standard navigation (onclick/javascript:void handlers) that static parsing cannot follow.
---

# Crawl & Render Audit

> **Reference implementation:** `braiaudit.render.render()`
> ([src/braiaudit/render.py](../../src/braiaudit/render.py)), runnable directly via
> `python skills/crawl-render-audit/scripts/render_audit.py <url>`. Requires
> the optional extra: `pip install -e ".[render]" && playwright install chromium`.
> Without it, returns `{"available": false, ...}` per the Error Handling
> section below rather than failing. Output is validated against
> [schemas/crawl-render-audit.output.schema.json](../../schemas/crawl-render-audit.output.schema.json).

## Operational Mission

Determine what an AI crawler or agent *with* a JavaScript-executing browser
would actually see and be able to reach on this page — closing the gap left
by `website-observer`'s static-only pass. This skill is deliberately more
expensive (a real browser context per page) and should only run on pages
`website-observer` (or the orchestrator) has flagged as needing it.

## Preconditions

- A working headless Chromium (Playwright) runtime is available. If it is
  not, do not silently skip — report back that render-dependent checks
  could not be performed so the orchestrator can add the appropriate
  `medium`-severity coverage-gap finding.
- The URL has already cleared `website-observer`'s `robots.txt` and
  rate-limit checks. This skill never fetches a URL that was disallowed or
  rate-limited upstream — it trusts that gate rather than re-deciding it.
- A render budget (`max_render_pages`, from the orchestrator's `options`) is
  respected — this skill must not silently render more pages than budgeted.

## Input Schema

```json
{
  "url": "https://example.com/app",
  "user_agent": "BrandAIReadinessAuditBot/1.0",
  "wait_strategy": "networkidle",
  "max_wait_ms": 8000,
  "drive_scroll": true,
  "drive_interactions": true,
  "traverse_shadow_dom": true
}
```

## Output Schema

```json
{
  "url": "https://example.com/app",
  "pre_render_text_length": 312,
  "post_render_text_length": 8420,
  "dom_diff_ratio": 26.9,
  "scroll_triggered_nodes_found": 14,
  "interactions_performed": [
    { "type": "click", "selector": "[role=tab]:nth-child(2)", "revealed_text_delta": 640 }
  ],
  "shadow_dom_components_found": ["user-card", "pricing-widget"],
  "shadow_dom_text_recovered": 1120,
  "discovered_internal_links": [
    "https://example.com/pricing",
    "https://example.com/contact"
  ],
  "rendered_html": "<html>...</html>",
  "signals": [
    "lazy_load_confirmed",
    "shadow_dom_encapsulation_confirmed",
    "onclick_div_navigation"
  ]
}
```

## Step-by-Step Execution Sequence

1. **Launch** a headless Chromium context with the declared `user_agent` —
   never impersonate a real end-user browser's UA string or a specific named
   AI crawler you are not.
2. **Navigate** to the URL and wait per `wait_strategy` (default
   `networkidle`, capped at `max_wait_ms`) so client-side rendering has a
   fair chance to complete before measurement.
3. **Capture baseline**: record `post_render_text_length` and full
   `rendered_html` immediately after settle, before any driven interaction.
   Compare against `pre_render_text_length` (passed in from
   `website-observer`'s `raw_text_length`) to compute `dom_diff_ratio`.
4. **Drive scroll** (if `drive_scroll`): incrementally scroll the viewport
   to the bottom in steps, pausing for DOM-settle after each step, capturing
   any newly-appeared nodes (especially elements that had `data-src` instead
   of `src`). Record `scroll_triggered_nodes_found`.
5. **Drive interactions** (if `drive_interactions`): identify obvious
   interactive-disclosure controls (`role="tab"`, accordion headers,
   "Load More"/pagination buttons) and click through each once, recording
   the text-length delta revealed per interaction. Do not perform any
   interaction with side effects beyond content disclosure (never submit
   forms, never complete purchases/sign-ups, never click destructive or
   account-mutating controls).
6. **Traverse Shadow DOM** (if `traverse_shadow_dom`): recursively walk
   `element.shadowRoot` for any custom elements found, extracting their
   text content into `shadow_dom_text_recovered` and listing the custom tag
   names found.
7. **Extract the true internal link graph**: in addition to standard
   `<a href>` collection, intercept in-page navigation triggers
   (`onclick` handlers producing route changes, `javascript:void(0)` links
   paired with client-side router calls) via runtime navigation/network
   observation, and list the resolved destination URLs in
   `discovered_internal_links` for `query-guided-discovery` to consume.
8. **Derive signals** from the deltas above (see thresholds) and return the
   full result.

### Signal Thresholds

| Signal | Condition |
|---|---|
| `app_shell_confirmed` | `dom_diff_ratio > 5` (post-render text is >5x pre-render) |
| `lazy_load_confirmed` | `scroll_triggered_nodes_found > 0` |
| `dynamic_interaction_confirmed` | any `interactions_performed` entry has `revealed_text_delta > 0` |
| `shadow_dom_encapsulation_confirmed` | `shadow_dom_components_found` non-empty |
| `onclick_div_navigation` / `javascript_void_href` | non-standard navigation handlers detected during link extraction |

## Error Handling & Edge Cases

- **Render timeout** (`networkidle` never reached within `max_wait_ms`):
  capture whatever DOM state exists at timeout, mark
  `render_timed_out: true` in the result, and still compute a best-effort
  `dom_diff_ratio` — a timeout on a heavy page is itself evidence worth
  reporting (potential performance/AI-crawl-budget concern), not a reason to
  discard the attempt.
- **Interaction crashes the page or triggers a navigation away**: catch the
  error, record the failed interaction in `interactions_performed` with
  `revealed_text_delta: null` and an `error` note, and continue with
  remaining interactions rather than aborting the whole page.
- **Infinite scroll with no natural end**: cap scroll steps at a fixed
  budget (e.g. 20 iterations or until two consecutive steps reveal no new
  content) rather than scrolling indefinitely.
- **CAPTCHA/anti-bot challenge appears only after JS execution** (not
  visible to `website-observer`'s static pass): stop immediately, do not
  attempt to solve or bypass it, and emit `anti_bot_challenge_detected` for
  `failure-diagnostics` exactly as `website-observer` would have.
- **Shadow DOM is `mode: "closed"`**: closed shadow roots are intentionally
  inaccessible via `element.shadowRoot` — record that a closed shadow root
  was detected but its contents could not be recovered, rather than
  attempting any engine-level workaround.
- **Render budget exhausted** (`max_render_pages` reached): stop rendering
  further pages and let the orchestrator know which flagged URLs were left
  unrendered, so that gap is reflected honestly rather than assumed clean.
