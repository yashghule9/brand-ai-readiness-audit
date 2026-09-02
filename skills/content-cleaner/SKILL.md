---
name: content-cleaner
description: Extracts the main readable content from raw or rendered HTML, strips boilerplate (navigation, footers, sidebars, ads, cookie banners, modal overlays), computes the main-text-to-markup ratio, and reconstructs semantic structure (headings/paragraphs) when the source HTML lacks it. Use after website-observer or crawl-render-audit has produced HTML, whenever clean article text is needed for downstream scoring or query matching, or when signals suggest HIGH_BOILERPLATE_DENSITY, MODAL_INTERRUPT_OVERLAY, or GENERIC_DIV_SOUP.
---

# Content Cleaner

## Operational Mission

Turn a raw or rendered HTML document into the text an AI system would
actually want to ingest: the real article/product/support content, with
navigation chrome, legal boilerplate, ads, and interstitials removed, and
with enough structural signal (headings, paragraph breaks) preserved that
downstream chunking doesn't produce a wall of undifferentiated text.

## Preconditions

- HTML input is available — prefer `rendered_html` from `crawl-render-audit`
  when it exists for the page, falling back to the raw HTML from
  `website-observer` otherwise. Never re-fetch the page itself; this skill
  is a pure transform over already-collected HTML.
- Called once per URL, after whichever of `website-observer` /
  `crawl-render-audit` produced the best available DOM for that URL.

## Input Schema

```json
{
  "url": "https://example.com/",
  "html": "<html>...</html>",
  "source": "rendered | raw"
}
```

## Output Schema

```json
{
  "url": "https://example.com/",
  "clean_text": "Our product helps teams ship faster...",
  "main_text_to_markup_ratio": 0.09,
  "boilerplate_removed_bytes": 41230,
  "structure": {
    "semantic_tags_present": false,
    "headings_found": 0,
    "paragraphs_found": 0
  },
  "overlays_removed": [
    { "type": "cookie_banner", "z_index": 9999 },
    { "type": "newsletter_modal", "z_index": 5000 }
  ],
  "content_hash": "sha256:...",
  "signals": [
    "low_main_text_ratio",
    "high_nav_footer_density",
    "zero_semantic_tags",
    "cookie_banner_detected"
  ]
}
```

`content_hash` is a stable hash of the cleaned text, used by
`query-guided-discovery`/`freshness-corroboration` to deduplicate pages that
render identically under different URLs (feeds `CANONICAL_URL_AMBIGUITY`).

## Step-by-Step Execution Sequence

1. **Strip non-content elements outright**: `<script>`, `<style>`,
   `<noscript>`, `<svg>` icon sprites, and HTML comments — these never count
   toward either the numerator or denominator of the content ratio in a way
   that would misrepresent it.
2. **Identify and remove structural chrome**: `<nav>`, `<header>` (site
   header, not per-article header), `<footer>`, `<aside>`, and elements
   carrying strong boilerplate signals in class/id/ARIA (`class="sidebar"`,
   `role="navigation"`, `role="banner"`, `role="contentinfo"`). Record the
   byte count removed as `boilerplate_removed_bytes`.
3. **Detect and remove interrupt overlays**: fixed/sticky-positioned
   elements with `z-index` above a high threshold (default `1000`), a body
   with `overflow: hidden` implying a modal lock, and known cookie-consent /
   newsletter-signup patterns. List each removed overlay in
   `overlays_removed` with its type and `z-index`. Do not remove content
   that merely *overlaps* visually without blocking — only remove elements
   that plausibly obstruct or gate the main content.
4. **Score candidate main-content containers**: apply a readability-style
   heuristic — text density per element, link-to-text ratio, tag depth — to
   identify the most likely main-content root (commonly `<article>`,
   `<main>`, or the highest-scoring `<div>`/`<section>`).
5. **Extract `clean_text`** from the winning container: visible text only,
   normalized whitespace, paragraph breaks preserved as line breaks.
6. **Compute `main_text_to_markup_ratio`**: `len(clean_text) /
   len(original_html_bytes)`. This is the ratio `failure-diagnostics` checks
   against the `HIGH_BOILERPLATE_DENSITY` threshold.
7. **Assess semantic structure**: check for the presence of `<article>`,
   `<section>`, and heading tags (`<h1>`–`<h6>`) in the main-content
   container. If none exist despite substantial `clean_text`, set
   `semantic_tags_present: false` and flag `zero_semantic_tags` — this is
   the `GENERIC_DIV_SOUP` case, and structure should be reconstructed
   heuristically (e.g. treat visually-larger/bolder lines as headings, blank
   line runs as paragraph breaks) so downstream chunking still has
   something to key off.
8. **Hash the cleaned text** (not the raw HTML) into `content_hash` for
   cross-page duplicate detection.
9. **Derive signals** from the thresholds below and return the result.

### Default Thresholds

| Signal | Condition |
|---|---|
| `low_main_text_ratio` | `main_text_to_markup_ratio < 0.15` |
| `high_nav_footer_density` | boilerplate byte removal exceeds ~40% of original HTML |
| `zero_semantic_tags` | no `<article>`/`<section>`/heading tags found in main content |
| `high_z_index_overlay_present` / `cookie_banner_detected` | any overlay removed in step 3 |
| `duplicate_content_detected` | `content_hash` matches a previously-seen hash for a different URL (reported by the orchestrator/freshness-corroboration, which holds cross-page state) |

## Error Handling & Edge Cases

- **No plausible main-content container found** (page is genuinely all
  chrome, e.g. a pure navigation hub page): return `clean_text: ""` and let
  the low ratio speak for itself rather than forcing a low-confidence guess
  into a false "main content."
- **Overlay removal accidentally strips real content**: bias conservatively
  — only remove an element as an overlay if it matches *both* a high
  `z-index`/fixed-position signal *and* a known interrupt pattern (cookie/
  consent/newsletter/paywall keywords, or a body-scroll-lock side effect).
  A single weak signal alone should not trigger removal.
- **Extremely large documents**: cap heuristic scoring to a reasonable
  document size/time budget; if truncated, note
  `truncated: true` and the byte offset so downstream skills know the
  cleaning was partial, not that the page is actually that short.
- **Non-article pages (e.g. pricing tables, contact forms)**: don't force
  prose extraction — extract visible structured text (table cells, form
  labels) as `clean_text` rather than returning empty just because it
  doesn't read like an article.
- **Malformed/unclosed HTML**: parse leniently (as a real browser/parser
  would); never fail the whole extraction over a single malformed tag —
  degrade to whatever partial structure can be recovered.
