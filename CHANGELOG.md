# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) for the `braiaudit` package
(`marketplace.json`'s `marketplace_version` tracks the *skill definitions*
separately and moves more slowly).

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
