---
name: query-guided-discovery
description: Given one or more target queries/topics and a page's internal link graph (from website-observer, crawl-render-audit, and sitemap.xml), scores and ranks candidate internal subpages by relevance, crawls the top-ranked candidates to check whether the query can actually be answered, and flags pages with zero incoming links or absent from the sitemap. Use when a single page only partially answers a target query (suspected DISTRIBUTED_INFORMATION_FRAGMENTATION), or when checking site-wide discoverability, including orphaned-page detection.
---

# Query-Guided Discovery

> **Reference implementation:** `braiaudit.discovery.discover()`
> ([src/braiaudit/discovery.py](../../src/braiaudit/discovery.py)), using
> lexical TF-cosine relevance scoring
> ([src/braiaudit/relevance.py](../../src/braiaudit/relevance.py)) rather than a
> heavyweight embedding model — sufficient to separate "clearly about
> pricing" from "clearly about careers", which is the level of judgment
> this skill actually needs. Runnable directly via
> `python skills/query-guided-discovery/scripts/discover.py < request.json`.
> Output is validated against
> [schemas/query-guided-discovery.output.schema.json](../../schemas/query-guided-discovery.output.schema.json).

## Operational Mission

Simulate how an AI agent or RAG retriever, armed with a real user question,
would navigate this site to find the answer — and surface where that
journey breaks down: information split across too many subpages, or pages
that exist but are unreachable through normal crawl traversal.

## Preconditions

- A cleaned content snapshot (from `content-cleaner`) for at least the seed
  page(s), plus the internal link graph collected by `website-observer`
  (static `<a href>`s) and `crawl-render-audit` (`discovered_internal_links`
  from non-standard navigation), and the `sitemap.xml` URL list from
  `website-observer`.
- `target_queries` from the orchestrator's input, if supplied. If none are
  supplied, this skill still runs a structural pass (link density, sitemap
  coverage, orphan detection) but skips answer-completeness scoring and
  should say so explicitly in its output rather than fabricating a score.
- `max_pages` budget from the orchestrator is respected for how many
  additional subpages this skill may crawl beyond the seed set.

## Input Schema

```json
{
  "seed_url": "https://example.com/",
  "target_queries": ["what does this product cost", "how do I contact support"],
  "internal_links": ["https://example.com/pricing", "https://example.com/about", "https://example.com/contact"],
  "sitemap_urls": ["https://example.com/", "https://example.com/pricing"],
  "max_additional_pages": 10
}
```

## Output Schema

```json
{
  "seed_url": "https://example.com/",
  "query_results": [
    {
      "query": "what does this product cost",
      "seed_page_answer_completeness": 0.3,
      "candidate_pages_ranked": [
        { "url": "https://example.com/pricing", "relevance_score": 0.92 }
      ],
      "answer_found_after_discovery": true,
      "pages_required_to_answer": ["https://example.com/", "https://example.com/pricing"]
    }
  ],
  "orphaned_pages": [
    { "url": "https://example.com/legacy-promo", "incoming_links": 0, "in_sitemap": false }
  ],
  "signals": [
    "partial_answer_match",
    "high_internal_link_density",
    "absent_from_sitemap"
  ]
}
```

## Step-by-Step Execution Sequence

1. **Build the internal link graph** for the site from the union of
   `website-observer`'s static links, `crawl-render-audit`'s
   `discovered_internal_links`, and `sitemap_urls`. Track, per URL, incoming
   link count and sitemap membership.
2. **For each `target_query`**:
   a. Score the seed page's already-cleaned text for answer completeness
      (does it plausibly contain the answer, partially, or not at all?)
      using lexical/semantic relevance — not just keyword matching, since
      an AI-facing audit should judge the way an LLM retriever would.
   b. If completeness is high, record it and move to the next query — no
      further crawling needed for this query.
   c. If completeness is partial or low, **score every candidate internal
      link** for relevance to the query (title, anchor text, URL path
      tokens, and — if already cleaned — page content), rank descending.
   d. **Crawl the top-ranked candidates** (bounded by
      `max_additional_pages` and the orchestrator's overall `max_pages`),
      pulling their cleaned content via `content-cleaner` if not already
      available, and re-check answer completeness against the *combined*
      content of the seed + visited candidates.
   e. Record whether the answer was ultimately found
      (`answer_found_after_discovery`) and exactly which pages were
      required to assemble it (`pages_required_to_answer`) — this list is
      the direct evidence for a `DISTRIBUTED_INFORMATION_FRAGMENTATION`
      finding when it spans more than one page.
3. **Run orphan detection independently of any query**: any URL discovered
   in the link graph (or explicitly seeded) with `incoming_links == 0` from
   any crawled page **and** absent from `sitemap_urls` is added to
   `orphaned_pages`.
4. **Derive signals** from the thresholds below and return the result.

### Default Thresholds

| Signal | Condition |
|---|---|
| `partial_answer_match` | `seed_page_answer_completeness` between 0.1 and 0.7 for any query |
| `high_internal_link_density` | seed page has an unusually high ratio of internal links to content length (supports fragmentation as plausible) |
| `zero_incoming_internal_links` | a known URL has no discovered inbound internal link |
| `absent_from_sitemap` | a known URL is not listed in `sitemap_urls` |

## Error Handling & Edge Cases

- **No `target_queries` supplied**: omit `query_results` entirely (do not
  fill it with fabricated queries) and clearly note in the output that
  answer-completeness scoring was skipped; still perform orphan detection.
- **Candidate page fails to crawl** (blocked by robots, rate-limited,
  errors): exclude it from `pages_required_to_answer`, note it as
  `attempted_but_unreachable` in the query result, and do not count it as
  contributing to answer completeness — never assume unreachable content
  would have answered the query.
- **`max_pages`/`max_additional_pages` budget exhausted before an answer is
  found**: report `answer_found_after_discovery: false` honestly along with
  the partial completeness reached, rather than either fabricating success
  or silently continuing past budget.
- **Every internal link scores low relevance** (site has no clear path to
  the answer): this itself is evidence — report the seed's low completeness
  and an empty/low-confidence candidate list rather than forcing a top pick.
- **Circular/duplicate link graph** (pagination loops, tracking-parameter
  URL variants): dedupe URLs by normalized path + `content_hash` (from
  `content-cleaner`) before scoring, so the same page isn't crawled or
  counted twice under different query strings.
