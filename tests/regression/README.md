# Regression corpus

Full audit-report JSON, one file per real site, captured at the Phase 2
closeout (`docs/PHASE2_BASELINE.md`) using default settings — zero flags,
the same invocation a real evaluator would use.

**These are observations, not assertions.** No test in this repository
asserts that a specific site "must" have a specific score, finding count, or
page count — a real site's content changes over time, and hard-coding a
website-specific expectation into production code or into a strict test
would be exactly the kind of website-specific hack this project's design
avoids.

What this corpus is for: a later change that silently reintroduces a known
bug — a false-clean on a blocked response, a crawl that silently collapses
to one page, a block page analyzed as real content — shows up as a large,
structural diff against these files (findings materializing or vanishing,
`pages_crawled` collapsing, a limitation disappearing) even though nothing
here is wired into `assert`. Diff a fresh run against the file here by hand
when investigating a suspected regression; do not write a test that fails
merely because a real site's content moved on.

## Files

| File | Site | Notable at capture time |
|---|---|---|
| `tata.json` | tata.com | apex→www redirect (Bug 2 case) |
| `infosys.json` | infosys.com | 403 + non-empty body (Bug 3 case) |
| `zerodha.json` | zerodha.com | — |
| `razorpay.json` | razorpay.com | slowest of the ten (full crawl + 3 renders) |
| `cred.json` | cred.club | JS-heavy, rendering triggers |
| `meesho.json` | meesho.com | 403 + non-empty body (Bug 3 case) |
| `swiggy.json` | swiggy.com | 403 + empty body (Bug 1 case) |
| `zoho.json` | zoho.com | apex→www redirect (Bug 2 case) |
| `thehindu.json` | thehindu.com | apex→www redirect (Bug 2 case) |
| `isro.json` | www.isro.gov.in | bare apex domain has no DNS record — audited via `www.` |
