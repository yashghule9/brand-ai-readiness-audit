---
name: website-observer
description: Performs a low-overhead HTTP-only inspection of a URL — raw GET request, response headers, status code, robots.txt rules, sitemap.xml presence, and static-HTML metrics (raw text length, script count, root-container detection) — without executing any JavaScript. Use this FIRST for every URL in a Brand AI Readiness Audit, before any headless rendering, to cheaply detect compliance blocks (robots disallow, rate limiting, anti-bot challenges) and to decide whether the static HTML already contains meaningful content or looks like an empty client-side-rendered app shell.
---

# Website Observer

## Operational Mission

Answer, as cheaply as possible and without executing any client-side code:
"What does a plain, JS-free AI crawler see when it fetches this URL?" This
is the audit's first pass — it must run before `crawl-render-audit` so that
expensive headless-browser budget is only spent where the static response
genuinely warrants it.

## Preconditions

- A fully-qualified URL (or a bare domain to normalize to `https://{domain}/`).
- A declared, honest `User-Agent` string for the audit bot — never spoof a
  real browser or a specific AI crawler's identity to bypass detection.
- `robots.txt` for the URL's origin must be fetched and evaluated **before**
  fetching any other path on that origin, and its directives (including
  `Crawl-delay`) must be honored for every subsequent request this skill or
  any downstream skill makes to that origin.

## Input Schema

```json
{
  "url": "https://example.com/",
  "user_agent": "BrandAIReadinessAuditBot/1.0",
  "timeout_ms": 10000
}
```

## Output Schema

```json
{
  "url": "https://example.com/",
  "http_status": 200,
  "headers": { "content-type": "text/html; charset=utf-8" },
  "response_time_ms": 240,
  "robots_txt": {
    "fetched": true,
    "disallowed_for_agent": false,
    "crawl_delay_seconds": null,
    "sitemap_urls": ["https://example.com/sitemap.xml"]
  },
  "sitemap_present": true,
  "raw_html_bytes": 48213,
  "raw_text_length": 312,
  "script_count": 22,
  "link_count": 8,
  "root_container_detected": true,
  "canonical_tag_present": false,
  "json_ld_present": false,
  "signals": [
    "low_raw_text",
    "high_script_count",
    "root_container_detected",
    "missing_schema_org"
  ]
}
```

`signals` is a flat list of ontology-recognizable tokens (see
`skills/failure-diagnostics/references/ontology.yaml`) — this skill's most
important output, since `failure-diagnostics` consumes it directly.

## Step-by-Step Execution Sequence

1. **Normalize** the target into a fully-qualified URL and resolve its
   origin (`scheme://host[:port]`).
2. **Fetch `robots.txt`** from the origin. Parse rules for the declared
   `user_agent` and for `*`.
   - If the target path is disallowed → set
     `robots_txt.disallowed_for_agent = true`, emit signal
     `robots_txt_disallow`, and **stop** — do not fetch the target URL. Hand
     this result directly to `failure-diagnostics`.
   - Record any `Sitemap:` directives and `Crawl-delay`.
3. **Fetch `sitemap.xml`** at the conventional root path (and any sitemap
   URLs declared in `robots.txt`) to record `sitemap_present` and the list
   of listed URLs — this feeds `query-guided-discovery`'s orphan-page check
   later in the pipeline.
4. **Fetch the target URL** with a plain HTTP GET (no JS engine, no
   headless browser). Respect `timeout_ms` and any `Crawl-delay`.
   - On `429 Too Many Requests`: read `Retry-After`, back off once, retry at
     most once total, then emit signal `http_429_rate_limit` and stop.
   - On `403`/`503` with a Cloudflare/Akamai/CAPTCHA fingerprint in the body
     or headers: emit signal `anti_bot_challenge_detected` and stop — do
     not attempt any circumvention technique.
5. **Parse the raw HTML statically** (no execution): compute
   `raw_text_length` (visible text stripped of tags/scripts/styles),
   `script_count`, `link_count` (`<a href>` count), presence of a root
   container (`<div id="app">`, `<div id="root">`, or equivalent single
   near-empty wrapper), presence of `<link rel="canonical">`, and presence
   of `<script type="application/ld+json">`.
6. **Detect soft-404 phrasing** in the title/body ("Page Not Found", "Item
   Unavailable") even on a `200` status.
7. **Derive signals**: apply the thresholds below and assemble the
   `signals` array, then return the full output object for
   `failure-diagnostics` to classify.

### Default Thresholds

| Signal | Condition |
|---|---|
| `low_raw_text` | `raw_text_length < 500` |
| `high_script_count` | `script_count > 15` |
| `root_container_detected` | matches `<div id="app">`/`<div id="root">` (or similar) with near-empty inner content |
| `missing_schema_org` | no `application/ld+json` and no Microdata/RDFa attributes found |
| `robots_txt_disallow` | matching `Disallow` rule for the agent/`*` on this path |
| `http_429_rate_limit` | response status `429` |
| `anti_bot_challenge_detected` | status `403`/`503` + known WAF/CAPTCHA fingerprint |
| `soft_404_suspected` | status `200` + error-phrase match in title/body |
| `canonical_missing` | no `<link rel="canonical">` present |

## Error Handling & Edge Cases

- **DNS failure / connection refused**: return `http_status: null` with an
  explicit `error` field describing the failure; do not fabricate a status
  code. Let `failure-diagnostics` classify this as `critical`.
- **`robots.txt` missing (404)**: treat as "no restrictions declared" —
  proceed, but still record `robots_txt.fetched = false`.
- **Redirect chains**: follow up to 5 redirects, record the final resolved
  URL, and flag more than 3 hops as a minor signal (`excessive_redirects`)
  for `failure-diagnostics` to optionally surface.
- **Non-HTML content-type** (e.g. PDF, JSON API endpoint): skip HTML-only
  metrics, record `content-type`, and pass through with `raw_text_length:
  null` rather than a misleading zero.
- **Never** execute inline `<script>` content or follow `javascript:` hrefs
  — that is explicitly out of scope for this skill (see
  `crawl-render-audit`).
