# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) for the `braiaudit` package
(`marketplace.json`'s `version` tracks the *skill definitions*
separately and moves more slowly).

## [0.9.1] — 2026-09-10

### Fixed

- **A 403/503 response with a real, non-empty, unrecognized body was
  analysed as the site's actual content.** The existing empty-body handling
  (`HTTP_ERROR_STATUS_BLOCKED`) only fires when a 4xx/5xx body is empty or
  non-HTML; a small but real HTML body — a block page worded outside the
  known anti-bot fingerprint list, live-observed against infosys.com and
  meesho.com — fell through unchanged into full content analysis, producing
  findings that described the block page rather than the site. Scoped
  narrowly to status 403/503 (the same pair the anti-bot fingerprint check
  already treats specially) with real HTML content and no matching
  fingerprint: the response body is no longer analysed, and a new signal,
  `http_error_status_unconfirmed`, reports the ambiguity as an unscored
  `kind: limitation` (`HTTP_ERROR_STATUS_UNCONFIRMED`) rather than guessing
  "blocked" or "genuine" in either direction. An ordinary 404 with a real
  body — the common case for a dead link found mid-crawl — is untouched.
  Verified live: infosys.com and meesho.com go from several fabricated
  content findings each to zero findings plus one honest limitation.

### Note on documentation

This entry is the first CHANGELOG update since 0.5.0, though `pyproject.toml`
had already reached 0.9.0 — the intervening work (a scoring-model rework to
per-axis percentages, twelve new staleness/engagement/identity detectors,
and Phase 2's off-site corroboration module) was implemented but never
written up here. See `docs/PHASE2_BASELINE.md` for the closeout state as of
this entry; backfilling the missing 0.6.0–0.9.0 entries from memory was
judged too likely to be inaccurate and was not attempted.

## [0.5.0] — 2026-09-05

### Added

- **Five failure modes covering the staleness, identity and engagement
  axes**, which until now had almost no coverage — the audit could only
  really answer "can a machine read this page?".
  - `NO_FRESHNESS_SIGNAL` — structured data with no `dateModified` /
    `datePublished` and no visible "last updated" marker, so nothing
    distinguishes a current page from a years-old snapshot of the same
    claims elsewhere on the web.
  - `STALE_STRUCTURED_DATA_DESYNC` — JSON-LD asserting a brand string
    (slogan, legal name, alternate name) that appears nowhere in the text a
    human reads. The classic rebrand desync: the visible site was updated
    and the markup behind it was not, and the mismatch is invisible to
    anyone reviewing the page in a browser.
  - `UNCLAIMED_ENTITY_IDENTITY` — `Organization` markup with no `sameAs`
    links, so nothing connects the site to an entity record anywhere else
    and a shared brand name resolves to whichever entity is better
    corroborated.
  - `QUERY_REGISTER_MISMATCH` — headings that never take the shape of a
    question a person would actually ask, so the page does not align with
    how users prompt assistants.
  - `ANSWER_BURIED_BELOW_FOLD` — concrete facts (prices, sizes, specs)
    present on the page but absent from its opening text.
- `website-observer` now parses JSON-LD properly — `@graph` flattened,
  malformed blocks skipped rather than raised — and reports `schema_types`
  and `structured_data_desync` alongside the existing presence flag.
- `content-cleaner` reports `structure.question_shaped_headings`.

### Changed

- Ontology to 1.5: 23 failure modes across all four axes (visibility 15,
  staleness 3, engagement 3, identity 2) plus 10 proactive opportunities.

### Not built (deliberately)

- **Off-site corroboration.** Measuring what third-party surfaces say about
  a brand — the root cause behind both the visibility and staleness cases —
  requires fetching pages on Reddit, Quora and review sites that actively
  challenge automated traffic. This auditor halts on an anti-bot challenge
  by design and does not route around one, so such a pass would return
  partial data unpredictably and make a run non-reproducible. The
  corresponding work is surfaced as advice instead, via the
  `THIRD_PARTY_CORROBORATION`, `ENTITY_DISAMBIGUATION_RECORD` and
  `ASSISTANT_ANSWER_PANEL` opportunities, which state plainly that the
  brand must sample assistant answers directly — no on-site change can
  measure that outcome.

## [0.4.0] — 2026-09-05

### Added

- **The report now has two substantive halves.** Alongside `findings`, every
  audit emits a top-level `suggested_actions` array: what to change,
  prioritised critical -> low, each entry carrying `derived_from` — either
  the finding id it fixes (`F-002`) or the literal `"proactive"`.
- **Proactive recommendations** (`opportunities` in `ontology.yaml`), emitted
  independently of whether any defect fired, so a site with zero findings
  still receives useful advice — answer-first content blocks, query-shaped
  landing pages, explicit freshness markers, a dated canonical-facts page,
  discontinued-product succession, third-party corroboration, a claimed
  entity record, AI-referral landing experience and measurement, and a
  standing assistant-answer probe panel. Each declares `suppressed_by`: when
  a failure mode that already covers the same ground fired, the opportunity
  is dropped rather than repeating the finding's own remediation.
  Opportunities carry no evidence and never affect the score — they are
  advice, not observations about the site.
- **`axis` on every failure mode and finding** — `visibility`, `staleness`,
  `engagement` or `identity` — so the report can be read against the
  problems a brand actually reports rather than the auditor's internal
  categories.
- **A headline readiness score** (`readiness.score`, 0-100) with a per-axis
  breakdown, and `readiness.formula` stating the rule in words so the number
  is reproducible by hand rather than a black box. Only findings score;
  proactive advice never penalises a brand.
- **`remediation` on all 18 failure modes**, written for the site owner.
  Previously every suggested action was assembled from `recovery_strategy`
  and `recommended_tool` — which name *the auditor's* next step — so a
  brand's engineer read "Recommended remediation path: crawl-render-audit
  (tool: playwright)" and learned nothing about what to change. Those fields
  remain, for the pipeline; they are no longer surfaced as advice.
- Two more meta-analysis checks: `sequential_action_ids`, and
  `every_finding_has_an_action` — no problem is reported without telling the
  reader what to do about it.

## [0.3.0] — 2026-09-05

### Added

- **AI answer-engine crawler access matrix.** `website-observer` now
  evaluates `robots.txt` against the crawlers that actually feed AI
  assistants — `GPTBot`, `OAI-SearchBot`, `ChatGPT-User`, `ClaudeBot`,
  `anthropic-ai`, `PerplexityBot`, `Google-Extended`, `Applebot-Extended`
  and `CCBot` — not just this auditor's own user-agent, and emits
  `ai_crawler_robots_disallow` → the new `critical` failure mode
  `AI_CRAWLER_ROBOTS_DISALLOW`. The check reuses the already-parsed
  `robots.txt`, so covering ten agents costs zero extra requests. Evidence
  names both the blocked AI crawlers and any classic search crawlers still
  allowed, because "allows Googlebot, disallows GPTBot" is the single most
  direct cause of a brand being absent from AI answers — and is usually
  unintentional.
- **A real crawl frontier.** `run_audit` now walks the site breadth-first
  from the seed URLs to `--max-depth` (default 2), capped by `--max-pages`.
  Breadth-first so every depth-1 page is audited before any budget goes to
  depth 2 — depth-first would spend the whole budget descending one blog
  subtree and never reach `/pricing`. With `target_queries` supplied, newly
  discovered links are ordered by lexical relevance to those queries so a
  limited budget goes to the pages most likely to answer them.
- `remediation:` as an optional per-failure-mode field in `ontology.yaml`,
  used for a finding's `suggested_action` when present. `recovery_strategy`
  / `recommended_tool` describe the *auditor's* next step ("crawl-render-
  audit", "playwright") and are useless as advice to a site owner; the new
  field carries what the brand's engineer should actually change. Populated
  for `AI_CRAWLER_ROBOTS_DISALLOW`; the remaining modes fall back to the
  previous text until written up.

### Fixed

- **Internal links were only ever discovered by the render backend.**
  `website-observer` parsed every `<a href>` to compute `link_count` and
  then discarded the URLs, so `internal_links` reached
  `query-guided-discovery` empty on any run without Playwright installed.
  That silently disabled fragmentation scoring, orphaned-page detection and
  `high_internal_link_density` — while `meta.coverage` still counted
  `ORPHANED_PAGE_ISOLATION` as *evaluated*, reporting a check that could not
  possibly fire as clean. Exactly the false-clean the coverage block exists
  to prevent. `observe()` now returns normalised, same-host
  `internal_links`, and the pipeline unions them with any render-discovered
  links.
- **Every finding's evidence embedded the entire page source.**
  `pipeline._diagnose` copied all scalar fields from a signal bundle into
  the finding's metrics, including `raw_html` and `clean_text` — producing
  multi-hundred-kilobyte evidence strings. It was masked in the final report
  only because cross-page corroboration overwrites evidence for most
  categories; `compliance_and_access` findings pass their raw evidence
  through untouched, so the new AI-crawler finding would have shipped the
  whole page with it. Bulk fields are now excluded and metric strings capped.
- URL spellings are normalised for crawling (`canonical_crawl_url`):
  fragments stripped and redundant trailing slashes collapsed, so
  `/pricing`, `/pricing/` and `/pricing#plans` are one page in the frontier
  and the link graph rather than three.

## [0.2.0] — 2026-09-03

### Added

- **Meta-analysis stage** in `freshness-corroboration`
  (`braiaudit.coverage` + `braiaudit.meta`), attached to every report as an
  additive `meta` block. This closes a gap identified against an external
  design review of the "ontology as knowledge/taxonomy layer under the
  orchestrator, not the execution mechanism" approach this pipeline
  already followed: the review's recommended architecture is
  `ONTOLOGY -> SKILLS -> CHECKS -> FINDINGS`, with a further
  `raw observations -> META ANALYZER (dedupe, validate, root-cause) ->
  recommendations -> final report` stage on top. Dedup and floor-schema
  validation already existed (`freshness-corroboration`'s per-failure-mode
  grouping, `braiaudit.schemas.validate`); this release adds the two
  pieces that didn't: coverage measurement and structural self-validation.
  - **`braiaudit.coverage.compute_coverage()`** — cross-references which
    signals each ontology failure mode needs against which producer skills
    actually ran a given audit (a `SIGNAL_SOURCES` map, e.g.
    `crawl-render-audit` only counts as engaged if a render actually
    executed with a backend available — not merely attempted). Reports
    per-category and overall coverage percentages plus the specific
    `not_evaluated` failure modes, so "no render backend installed" reads
    as an explicit coverage gap rather than a silently clean bill of
    health for every render-dependent check.
  - **`braiaudit.meta.validate_report()`** — structural self-checks on the
    assembled report (no duplicate finding titles/ids, ids sequential with
    no gaps, every finding's evidence non-empty, findings sorted by
    severity, `summary` counts independently recomputable from the
    `findings` array) plus informational — never automatically merged —
    correlation notes when multiple distinct findings affect the exact
    same set of URLs, as a root-cause hint for a human or Claude to weigh.
  - `braiaudit.pipeline.run_audit()` now tracks `skills_engaged` (which
    producer "sources" actually ran, including query-dependent ones like
    `query-guided-discovery:target_queries`) and threads it through to
    `assemble_report()`.
  - `schemas/audit-report.schema.json` documents the new `meta.coverage` /
    `meta.validation` shape as optional, additive properties — a consumer
    reading only `site`/`summary`/`findings` is unaffected.
  - 14 new tests (`tests/test_coverage.py`, `tests/test_meta.py`, plus two
    pipeline-level assertions) bring the suite to 70 tests.
- `skills/freshness-corroboration/SKILL.md` documents the meta-analysis
  stage as step 8 of its execution sequence, with the "normal analysis vs.
  meta-analysis" framing this addition is built around; the root `SKILL.md`
  and `README.md` reference it in the pipeline description.

## [0.1.0] — 2026-09-02

### Added

- Six Claude Agent Skills (`website-observer`, `crawl-render-audit`,
  `content-cleaner`, `query-guided-discovery`, `failure-diagnostics`,
  `freshness-corroboration`) plus a root orchestrator SKILL.md, all
  conforming to the two-level disclosure format (YAML frontmatter trigger +
  body directives).
- The Web Failure Ontology (`skills/failure-diagnostics/references/ontology.yaml`),
  covering 17 failure modes across 5 categories (Rendering & Execution,
  Content Noise, Discovery & Pathing, Semantic Deficit, Compliance &
  Access), each with a `severity`, `signals`, `match_mode`,
  `recovery_strategy`, `recommended_tool`, and `audit_finding_title`.
- `braiaudit`, a reference implementation (`src/braiaudit/`) of the
  deterministic, non-browser parts of the pipeline: HTTP/robots.txt/
  sitemap inspection, static HTML analysis, boilerplate stripping and
  main-content scoring, lexical query-relevance ranking, ontology-driven
  diagnosis, and floor-schema-conformant report assembly — runnable end to
  end via `braiaudit audit <site>` against a live site with zero optional
  dependencies.
- An optional Playwright-backed headless-render backend
  (`pip install braiaudit[render]`) implementing the full
  `crawl-render-audit` spec: scroll-driven lazy-load detection, tab/
  accordion/"Load more" interaction driving, Shadow DOM traversal, and
  JS-navigation-trap detection — degrading to an honest
  `RENDER_COVERAGE_GAP` finding rather than failing silently when it isn't
  installed.
- JSON Schemas (`schemas/`) for every skill's I/O contract, the audit
  report floor schema, and meta-schemas for `ontology.yaml` and
  `marketplace.json`, all enforced at runtime by `braiaudit.schemas`.
- A test suite (`tests/`, 56 tests) covering the ontology matcher, each
  producer module, cross-page report corroboration, and a full pipeline
  run against mocked HTTP — including a guard
  (`test_every_ontology_signal_is_emitted_by_some_producer`) that fails CI
  if the ontology and the code it classifies ever drift apart.
- `tools/lint_skills.py`, checked in CI, validating every `SKILL.md`'s
  frontmatter and cross-referencing `marketplace.json` against what's
  actually on disk.
- GitHub Actions CI (`.github/workflows/ci.yml`) running lint, the skill
  linter, schema validation, and the test suite across Python 3.10–3.13,
  plus a smoke job confirming the optional render extra installs cleanly.

### Fixed (during initial development, before any external release)

- **False positives from partial ontology matches.** The first cut of
  `failure-diagnostics` fired a finding on *any* overlap between observed
  signals and a failure mode's declared signal list — so a single
  legitimately-terse static page (no CSR, no heavy script use) tripped
  `APP_SHELL_EMPTY_DOM` on `low_raw_text` alone. Fixed by introducing
  explicit `match_mode: all | any` per failure mode and requiring the full
  match criterion — no more emitting on a weak partial overlap. See
  `ontology.yaml`'s header comment and `FailureMode.matches`.
- **Ontology entries that could never fire.** Several failure modes (e.g.
  `LAZY_LOAD_TRIGGER_REQUIRED`, `SOFT_404_MISDIRECTION`,
  `USER_AGENT_ROBOTS_DISALLOW`) declared signals that no producer module
  actually emitted, or combined signals from mutually-exclusive code paths
  (a page can't simultaneously be blocked by `robots_txt_disallow`, which
  short-circuits before the HTTP GET, and carry an `http_403_status` from
  that same GET). Reconciled every ontology entry against the real signal
  vocabulary; added the missing `data_src_attribute_present` (static) and
  `onclick_div_navigation` / `javascript_void_href` (render-time) detectors
  rather than dropping the corresponding failure modes.
- **`content-cleaner` never ran on static (non-rendered) pages** in the
  orchestrator — the pipeline only wired cleaned HTML through when a render
  pass had happened, silently skipping boilerplate/structure analysis for
  the majority of pages that don't need JS rendering at all.
- **Cross-page report assembly crashed on synthetic operational findings**
  (e.g. a hand-built "render backend unavailable" finding referencing a
  failure mode absent from the ontology) with a `KeyError`. Fixed by
  routing that case through the same `ontology.diagnose()` path as every
  other finding (`RENDER_COVERAGE_GAP`), rather than hand-assembling a
  finding dict that bypassed the ontology.
- **Bag-of-words relevance scoring never matched simple word-form
  variations** (e.g. "cost" vs. "costs"), making `query-guided-discovery`'s
  answer-completeness scoring far too conservative. Added lightweight
  suffix-stripping and re-tuned `ANSWER_THRESHOLD` against realistic short
  page-text samples.
