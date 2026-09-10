# Phase 2 Baseline — Off-Site Corroboration

Frozen closeout snapshot for Phase 2 of the Brand AI Readiness Audit,
recorded before Phase 3 (assistant-querying) begins. This document is the
reference point for "did a later change silently break something Phase 2
already established."

## Architectural principle (unchanged by Phase 2)

```
Producers observe.
The ontology decides.
The assembler reports.
```

A model may interpret a finished audit's results. It never invents,
overrides, or directly measures a finding. Phase 2 added one new producer
(`corroborate.py`) and one new signal on the existing producer (`fetch.py`);
neither changes this principle.

## Scope

Phase 2 answers the one question no on-site check can: does an independent,
public record of this brand exist, and does it agree with what the site says
about itself? Implemented in `src/braiaudit/corroborate.py` — the only
module in the codebase that fetches anything outside the audited site.

Constraints, unchanged since Phase 2 began and re-verified at this closeout:

- HTML-only sources (Wikipedia, Wikidata, Wayback Machine parsed as rendered
  pages, never their JSON APIs)
- `robots.txt` respected on these third parties, not only the audited site
- absence is only claimed after exhausting name variants (brand name,
  legal-suffix-stripped form, domain-derived label)
- no challenge bypassing — a blocked or unreachable source yields
  `parse_status: unknown`, never a claim about the brand

## Ontology state at closeout

| | |
|---|---|
| Version | **2.3** |
| Failure modes | **40** total — 39 defect, 1 limitation |
| Opportunities | 10 |
| Axis distribution | visibility 17\*, engagement 8, staleness 7, identity 7 |

\* Visibility's count includes the two modes this closeout added
(`HTTP_ERROR_STATUS_BLOCKED`, `HTTP_ERROR_STATUS_UNCONFIRMED`); the
axis distribution recorded at the start of this closeout was 17/8/7/7
counting only the first of those two.

New in Phase 2 specifically: `ENTITY_RECORD_ABSENT` (medium, identity),
`ENTITY_RECORD_NOT_RECIPROCAL` (high, identity) — the concrete
entity-collision finding, confirmed live against a real name-collision case
(querying "Adobe" resolves to the Wikidata entity for the building material,
not Adobe Inc.).

`first_seen()` (Wayback earliest-snapshot date) is wired in as **unscored
context** appended to the entity finding's evidence — not a scored
"unchanged since" or multi-snapshot staleness detector. That fuller
change-history feature was not attempted; a single earliest-snapshot lookup
is materially less work than diffing multiple snapshots, and claiming the
latter would overstate what is built.

## Test / lint status at closeout

- **159 tests pass**
- `ruff check .` — clean
- `python tools/lint_skills.py` — clean

## Real-world corpus

Ten real sites, run at default settings (`braiaudit audit <domain>`, zero
flags — matching real evaluator usage), used to find bugs rather than to
assert what any specific site's score "should" be:

Tata, Infosys, Zerodha, Razorpay, CRED, Meesho, Swiggy, Zoho, The Hindu,
ISRO (audited as `www.isro.gov.in` — the bare `isro.gov.in` apex has no DNS
A record at all; not a code defect, confirmed by direct resolution check).

Full report JSON for each site is preserved under `tests/regression/` as a
regression artifact (not a hard-coded expectation — see that directory's own
note).

### Final validation run (post Bug-3 fix)

| Site | Pages | Rendered | Findings | Score | Time (s) |
|---|--:|--:|--:|--:|--:|
| Tata | 15 | 0 | 9 | 82 | 26.3 |
| Infosys | 1 | 0 | 0 | 100 | 4.8 |
| Zerodha | 15 | 0 | 14 | 73 | 9.8 |
| Razorpay | 15 | 3 | 16 | 69 | 168.2 |
| CRED | 15 | 3 | 16 | 71 | 81.9 |
| Meesho | 1 | 0 | 0 | 100 | 5.5 |
| Swiggy | 1 | 0 | 1 | 90 | 6.2 |
| Zoho | 15 | 1 | 14 | 72 | 55.8 |
| The Hindu | 15 | 3 | 11 | 74 | 131.9 |
| ISRO | 15 | 0 | 8 | 87 | 11.5 |

**Runtime:** min 4.8s · max 168.2s · mean 50.2s · median 18.9s.
**Budget:** 300s. **Nothing exceeded it.** No timeouts.

Runtime improved materially versus the pre-fix baseline (min 6.8s / max
212.9s / mean 67.8s / median 23.5s) as an honest side effect of Bug 3's fix,
not a deliberate optimization: Infosys and Meesho no longer trigger a render
pass off a fabricated `low_raw_text` signal computed from a block page's
thin body. Razorpay and The Hindu remain the slowest (full 15-page crawl
plus 3 rendered pages each) — both comfortably under budget; per the
closeout instructions, this was not optimized further since neither
violates the 300s budget.

## Bugs found and fixed this closeout

### Bug 1 — false-clean on a 403 with an empty body (fixed earlier this
Phase 2 track, unchanged by this closeout)

`swiggy.com` returned HTTP 403 with zero bytes and no matching anti-bot
fingerprint. Fell through every detector: no signal, no finding, page
counted as crawled, score could read 100 while nothing was retrieved.

Fixed with signal `http_error_status_blocked` → ontology mode
`HTTP_ERROR_STATUS_BLOCKED` (critical, visibility, compliance_and_access,
`kind: defect`). In the halt-signal set. Regression:
`test_error_status_with_no_body_is_a_finding_not_a_false_clean`.

### Bug 2 — apex→www redirect silently collapsed multi-page crawls (fixed
earlier this Phase 2 track, unchanged by this closeout)

Five sites (Tata, Zerodha, Zoho, The Hindu, ISRO) redirect their bare domain
to `www.`. The crawl frontier's same-host guard was computed from the
originally-typed host, before the seed's own redirect resolved — so every
link on the (correctly fetched) redirected homepage was rejected as
off-host, and the crawl silently degraded to one page.

Fixed by expanding allowed hosts once, from the seed URL's own redirect
target, at depth 0 only. A redirect met deeper in the crawl still cannot
expand scope. Regression:
`test_seed_redirect_expands_allowed_hosts_but_a_later_redirect_does_not`
(includes the negative case: a mid-crawl redirect toward a third party is
still rejected).

### Bug 3 — 403 with a non-empty, unrecognized body (diagnosed and fixed
this closeout)

**Diagnosis.** `infosys.com` and `meesho.com` return HTTP 403 with a small,
real HTML body that is not empty and matches no anti-bot fingerprint. Bug
1's fix only fires when the body is empty or non-HTML, so this case fell
through unchanged and continued into full content analysis — the block
page's own thin, generic text was analyzed as if it were the site's real
homepage, producing findings (`low_raw_text`, `meta_description_absent`,
etc.) that describe the block page, not the site.

**Fix.** In `fetch.observe()`, a response is now checked for a usable body
*before* the error-status branch runs. When the status is 403 or 503 (the
same pair `_detect_anti_bot` already treats as bot-mitigation-relevant),
carries a real HTML body, but matches no fingerprint, the body is not
analyzed further. A new signal, `http_error_status_unconfirmed`, is emitted
instead, and the pipeline halts that page there (added to
`_HALT_SIGNALS`) — no render, no clean, no discover.

New ontology mode `HTTP_ERROR_STATUS_UNCONFIRMED`: `kind: limitation`,
severity medium, axis visibility, category compliance_and_access. As a
limitation it is reported under `audit_limitations`, never in `findings`,
and **never affects the readiness score** — an unconfirmed case must not
read as an ordinary website defect.

**Why the fix avoids false positives in both directions.** The scope is
deliberately narrow:

- Limited to status codes **403 and 503** — the identical pair the existing
  anti-bot fingerprint detector already targets, not "any 4xx/5xx." An
  ordinary 404 with a real "page not found" body (the overwhelmingly common
  case for a dead link found mid-crawl) is untouched and continues through
  normal content analysis exactly as before — confirmed by
  `test_a_genuine_404_with_real_content_is_still_processed_normally`.
- The outcome is a `limitation`, not a `defect`. Even in the case this
  scope does *not* handle perfectly — a legitimate paywall or age-gate that
  happens to return 403/503 with real content — the consequence is an
  unscored "could not confirm" entry, never a scored accusation against the
  site and never a fabricated content-quality finding. The task's own
  framing was followed exactly: *"if a completely reliable distinction
  cannot be made without unacceptable false positives, prefer an explicit
  limitation/unknown state over an aggressive classification."*
- Bug 1's existing behavior (empty-body 403/503, and any other status with
  no usable body) is untouched — same signal name, same evidence text, same
  regression test passing unchanged.

**Verified live** against the real sites that exposed it:

| Site | Before fix | After fix |
|---|---|---|
| Infosys | 7 fabricated findings, score 88, 22.9s | 0 findings, 1 limitation, score 100, 4.8s |
| Meesho | 6 fabricated findings, score 90, 24.0s | 0 findings, 1 limitation, score 100, 5.5s |
| Swiggy (control, Bug 1's case) | unaffected | unaffected — still `HTTP_ERROR_STATUS_BLOCKED` |
| Zoho (control, Bug 2's case) | unaffected | unaffected — still 15 pages crawled |

## Known limitation carried forward from this closeout

A 403/503 response with a **non-empty body that is not on the fingerprint
list** cannot be reliably classified as "genuinely blocked" versus
"legitimate content served with this status." This is reported honestly as
`HTTP_ERROR_STATUS_UNCONFIRMED` (unscored) rather than guessed in either
direction. Extending the anti-bot fingerprint list, or narrowing/widening
the (403, 503) status scope, would need new real-world evidence to justify —
not something to speculate into the ontology now.

## Other known gaps, unchanged by this closeout

- **Known limitation: with `--max-pages 1`, an unreachable apex may consume
  the single crawl budget before the queued depth-0 `www` fallback can be
  crawled. The default `max_pages=15` behaviour is unaffected.** Left as-is
  deliberately: `max_pages=1` semantically requests at most one page slot, and
  the audit still ends honestly — the apex is recorded unreachable, no page is
  analysable, so the score abstains and `NO_ANALYSABLE_PAGE_EVIDENCE` is
  reported rather than a number invented from nothing.
- **Phase 1 steps 2–4** (correlation gate across a labeled corpus, mode
  weighting, regression diff) remain blocked on an independently-labeled
  site corpus, which does not exist yet. Detector-writing (Phase 1 step 1)
  is complete; the statistical validation steps are not.
- **CHANGELOG.md is stale.** Its last dated entry documents version 0.5.0;
  `pyproject.toml` is at 0.9.0. The intervening entries (covering Phase 0's
  scoring rework and Phase 1's twelve new detectors) were never written up.
  Backfilling four versions of undocumented history was judged out of scope
  for this closeout and risks inaccuracy; flagged here rather than silently
  left unmentioned or hastily reconstructed.

## Explicit Phase 3 boundary

Phase 2 ends here. **Not implemented, and not started, by this closeout:**
assistant querying, LLM-based website evaluation, assistant representation
scoring, RAG, model-generated findings, or any new Phase 3 skill. The
intended future shape —

```
deterministic website audit → verified facts → assistant querying →
assistant observations → comparison against facts → unscored output
```

— remains a description of future work, not code in this repository.

---

**Phase 2 is frozen and ready for Phase 3.**
