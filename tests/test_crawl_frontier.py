"""Tests for AI-crawler robots evaluation, static link extraction, and the
BFS crawl frontier — the three pieces that make a multi-page audit work
without the optional render backend."""

from __future__ import annotations

import urllib.robotparser

import pytest
from bs4 import BeautifulSoup

from braiaudit.fetch import (
    AI_CRAWLER_USER_AGENTS,
    _extract_internal_links,
    canonical_crawl_url,
)
from braiaudit.pipeline import _crawl_order, _diagnose

ROBOTS_AI_BLOCKED = """
User-agent: Googlebot
Allow: /

User-agent: GPTBot
Disallow: /

User-agent: ClaudeBot
Disallow: /
"""


def _access(robots_text: str, url: str = "https://example.com/") -> dict[str, bool]:
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_text.splitlines())
    return {a: rp.can_fetch(a, url) for a in AI_CRAWLER_USER_AGENTS}


def test_ai_crawlers_blocked_while_googlebot_allowed():
    access = _access(ROBOTS_AI_BLOCKED)
    assert access["GPTBot"] is False
    assert access["ClaudeBot"] is False
    # An agent the file never names is not blocked by someone else's rule.
    assert access["PerplexityBot"] is True

    rp = urllib.robotparser.RobotFileParser()
    rp.parse(ROBOTS_AI_BLOCKED.splitlines())
    assert rp.can_fetch("Googlebot", "https://example.com/") is True


def test_permissive_robots_blocks_nothing():
    assert all(_access("User-agent: *\nAllow: /\n").values())


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://example.com/pricing#plans", "https://example.com/pricing"),
        ("https://example.com/pricing/", "https://example.com/pricing"),
        ("https://example.com/", "https://example.com/"),
    ],
)
def test_canonical_crawl_url_collapses_spellings(raw, expected):
    assert canonical_crawl_url(raw) == expected


def test_extract_internal_links_filters_and_dedupes():
    html = """
    <a href="/pricing">Pricing</a>
    <a href="/pricing/">Pricing again</a>
    <a href="/pricing#plans">Pricing anchor</a>
    <a href="https://example.com/about">About</a>
    <a href="https://other.com/x">Offsite</a>
    <a href="/brochure.pdf">PDF</a>
    <a href="mailto:hi@example.com">Mail</a>
    <a href="#top">Anchor</a>
    """
    links = _extract_internal_links(BeautifulSoup(html, "html.parser"), "https://example.com/")
    assert links == ["https://example.com/about", "https://example.com/pricing"]


def test_crawl_order_prefers_query_relevant_urls():
    links = [
        "https://example.com/privacy-policy",
        "https://example.com/pricing-plans",
        "https://example.com/careers",
    ]
    ordered = _crawl_order(links, ["what does this product cost pricing"])
    assert ordered[0] == "https://example.com/pricing-plans"
    # Without queries the caller's order is preserved — no wasted scoring.
    assert _crawl_order(links, []) == links


def test_raw_html_never_leaks_into_finding_evidence():
    bundle = {
        "signals": ["missing_schema_org"],
        "raw_html": "<html>" + "x" * 5000 + "</html>",
        "clean_text": "y" * 5000,
        "raw_text_length": 12,
    }
    findings = _diagnose("https://example.com/", bundle)
    evidence = findings[0]["evidence"]
    assert "xxxx" not in evidence
    assert "yyyy" not in evidence
    assert "raw_text_length=12" in evidence
    assert len(evidence) < 400


def test_crawl_budget_is_reported_not_silently_applied():
    """A partial crawl must never read as a complete one: when a budget stops
    the frontier, the report has to say so."""
    from braiaudit.report import assemble_report

    report = assemble_report(
        site="example.com",
        findings_by_url={},
        pages_crawled=3,
        crawl_note="Crawl stopped at the 240s time budget with 9 URL(s) still queued.",
    )
    crawl = report["meta"]["crawl"]
    assert crawl["stopped_early"] is True
    assert "still queued" in crawl["note"]

    complete = assemble_report(site="example.com", findings_by_url={}, pages_crawled=3)
    assert complete["meta"]["crawl"]["stopped_early"] is False
    assert complete["meta"]["crawl"]["note"] == ""


def test_full_urls_are_normalised_not_blindly_prefixed():
    """`site` is documented as "bare domain or full URL". Prefixing a full URL
    built https://https://host/, which resolves nowhere — the audit then
    reported zero pages crawled with no visible cause."""
    from braiaudit.fetch import normalize_url, site_label

    for typed in (
        "caterworld.ai",
        "www.caterworld.ai",
        "https://www.caterworld.ai/",
        "http://www.caterworld.ai/some/path",
    ):
        seed = normalize_url(site_label(typed))
        assert seed.count("https://") == 1, seed
        assert "caterworld.ai" in site_label(typed)
        assert not site_label(typed).startswith("http")


def test_render_degrades_to_a_coverage_gap_when_the_browser_cannot_launch(monkeypatch):
    """Installing the playwright package without a usable browser must not
    take the audit down. Before this, a launch failure propagated out of
    render() and killed the whole run — installing the optional extra made
    the tool strictly worse than not having it."""
    from braiaudit import render as render_mod

    def boom(*args, **kwargs):
        raise RuntimeError("Executable doesn't exist at ...chrome.exe")

    monkeypatch.setattr(render_mod, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(render_mod, "_render_with_browser", boom)

    result = render_mod.render("https://example.com/")
    assert result["available"] is False
    assert result["signals"] == ["render_backend_unavailable"]


def test_render_reports_unavailable_without_the_package(monkeypatch):
    from braiaudit import render as render_mod

    monkeypatch.setattr(render_mod, "_PLAYWRIGHT_AVAILABLE", False)
    assert render_mod.render("https://example.com/")["available"] is False


@__import__("responses").activate
def test_frontier_never_leaves_the_audited_host():
    """A link to a third party — a social profile, a partner site — must not
    pull the crawler off the brand's own site."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    body = (
        '<html><body><main><h1>Home</h1>'
        '<a href="/about">About</a>'
        '<a href="https://www.linkedin.com/company/example/">LinkedIn</a>'
        "<p>" + "Example sells things. " * 30 + "</p></main></body></html>"
    )
    responses.add(
        responses.GET, "https://example.com/", body=body, status=200, content_type="text/html"
    )
    responses.add(
        responses.GET,
        "https://example.com/about",
        body=body,
        status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com", options=AuditOptions(max_pages=10, max_render_pages=0, max_depth=2)
    )

    touched = {u for f in report["findings"] for u in f["affected_urls"]}
    touched |= set(report["meta"]["crawl"]["pages_unreachable"])
    assert all("example.com" in u for u in touched), touched
    assert not any("linkedin" in u for u in touched)


@__import__("responses").activate
def test_robots_and_sitemap_are_fetched_once_per_origin_not_once_per_page():
    """They are per-site documents. Refetching them for every page cost two
    extra round trips per page — most of the wall-clock time on a slow host,
    and the difference between finishing inside the runtime budget or not."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", body="", status=200)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    page = (
        '<html><body><main><h1>Hi</h1><a href="/a">a</a><a href="/b">b</a>'
        "<p>" + "Words about the product. " * 30 + "</p></main></body></html>"
    )
    for path in ("/", "/a", "/b"):
        responses.add(
            responses.GET,
            f"https://example.com{path}",
            body=page,
            status=200,
            content_type="text/html",
        )

    report = run_audit(
        "example.com",
        options=AuditOptions(
            max_pages=5, max_render_pages=0, max_depth=2, corroborate=False
        ),
    )

    assert report["meta"]["crawl"]["pages_crawled"] == 3
    # Scoped to the audited host: off-site corroboration legitimately reads
    # robots.txt from the third parties it consults.
    robots_calls = [
        c for c in responses.calls if c.request.url == "https://example.com/robots.txt"
    ]
    assert len(robots_calls) == 1, f"robots.txt fetched {len(robots_calls)} times"


@__import__("responses").activate
def test_rendered_links_are_canonicalised_before_entering_the_frontier(monkeypatch):
    """The static extractor canonicalises; rendered links came straight from
    the DOM. `/a`, `/a/` and `/a#top` are one page, and treating them as three
    burns the page budget re-auditing it."""
    import responses

    from braiaudit import render as render_mod
    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    body = (
        '<html><head><title>t</title></head><body><main><h1>Hi</h1>'
        "<p>" + "Words about the product. " * 30 + "</p></main></body></html>"
    )
    for path in ("/", "/a"):
        responses.add(
            responses.GET,
            f"https://example.com{path}",
            body=body,
            status=200,
            content_type="text/html",
        )

    def fake_render(url, **kwargs):
        return {
            "url": url,
            "available": True,
            "render_timed_out": False,
            "rendered_html": body,
            "discovered_internal_links": [
                "https://example.com/a",
                "https://example.com/a/",
                "https://example.com/a#top",
            ],
            "signals": [],
        }

    monkeypatch.setattr(render_mod, "render", fake_render)
    monkeypatch.setattr(
        "braiaudit.pipeline._RENDER_TRIGGER_SIGNALS", {"missing_schema_org"}
    )

    report = run_audit(
        "example.com", options=AuditOptions(max_pages=10, max_render_pages=5, max_depth=2)
    )

    assert report["meta"]["crawl"]["pages_crawled"] == 2, "one page audited under three spellings"


@__import__("responses").activate
def test_error_status_with_no_body_is_a_finding_not_a_false_clean():
    """A 403 with zero bytes and no matching anti-bot fingerprint used to
    produce zero signals and count as a successfully crawled page — a site
    scored 100 while we had actually retrieved nothing at all. Found live
    against a real site returning a bare 403."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="",
        status=403,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=1, max_render_pages=0, corroborate=False),
    )

    titles = {f["title"] for f in report["findings"]}
    assert "Page Returns An Error Status With No Retrievable Content" in titles
    assert report["summary"]["critical"] >= 1
    # And it must not silently be counted as a clean, fully-audited page.
    # The 403 is the only page there was, so nothing about the site's content
    # was ever observed: the score abstains rather than reporting a number
    # derived entirely from the one thing we could see (that we saw nothing).
    assert report["summary"]["readiness_score"] is None
    assert all(a["score"] is None for a in report["summary"]["by_axis"].values())


@__import__("responses").activate
def test_seed_redirect_expands_allowed_hosts_but_a_later_redirect_does_not():
    """A bare-domain seed that 301s to www is the ordinary case (zoho.com ->
    www.zoho.com is what typing the domain into a browser does) — every link
    on that page lives on the resolved host, and rejecting them silently
    degraded a real audit to a single page. Scoped to the seed only: a
    redirect met deeper in the crawl must never expand scope, or a page could
    walk the crawler onto a third party through an ordinary link."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://www.example.com/robots.txt", status=404)
    home = (
        '<html><body><main><h1>Home</h1><a href="/pricing">Pricing</a>'
        "<p>" + "Example sells things. " * 30 + "</p></main></body></html>"
    )
    pricing = (
        '<html><body><main><h1>Pricing</h1>'
        '<a href="https://evil.example.org/">redirected elsewhere</a>'
        "<p>" + "Pricing details. " * 30 + "</p></main></body></html>"
    )
    responses.add(
        responses.GET,
        "https://example.com/",
        status=301,
        headers={"Location": "https://www.example.com/"},
    )
    responses.add(
        responses.GET, "https://www.example.com/", body=home, status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET, "https://www.example.com/pricing", body=pricing, status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=10, max_render_pages=0, max_depth=2, corroborate=False),
    )

    crawled = {u for f in report["findings"] for u in f["affected_urls"]}
    assert "https://www.example.com/pricing" in crawled, "seed redirect did not expand scope"
    assert not any("evil.example.org" in u for u in crawled), (
        "a redirect encountered mid-crawl must never expand allowed hosts"
    )


@__import__("responses").activate
def test_403_with_unrecognised_body_is_unconfirmed_not_analysed_as_content():
    """Infosys/Meesho-style case: a 403 with a real, non-empty HTML body and
    no matching anti-bot fingerprint. Analysing that body as the site's real
    content fabricates content-quality findings about a page never actually
    seen — this must land as an explicit "could not confirm" limitation
    instead, never as an ordinary defect, and never with content signals
    computed from the block page's own thin text."""
    import responses

    from braiaudit.fetch import observe

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="<html><head><title>Forbidden</title></head>"
        "<body>Access to this resource is denied.</body></html>",
        status=403,
        content_type="text/html",
    )

    result = observe("https://example.com/")

    assert result["signals"] == ["http_error_status_unconfirmed"]
    assert result["parse_status"] == "ok"
    assert result.get("raw_html") is None, "block page body must not be analysed as real content"
    assert "confirm" in result["http_error_evidence"].lower()


@__import__("responses").activate
def test_403_unconfirmed_is_an_unscored_limitation_not_a_defect():
    """The critical requirement: unknown must not become an ordinary
    website defect. It must not count against the readiness score and must
    not appear in `findings` at all."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="<html><body>Access denied for this request.</body></html>",
        status=403,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=1, max_render_pages=0, corroborate=False),
    )

    titles = {f["title"] for f in report["findings"]}
    assert "Error-Status Response Could Not Be Confirmed As Blocked Or Genuine" not in titles
    limitation_titles = {lim["title"] for lim in report["audit_limitations"]}
    assert "Error-Status Response Could Not Be Confirmed As Blocked Or Genuine" in limitation_titles
    # Was `== 100`, which proved score-neutrality only by accident: the 403 was
    # the sole page, so that 100 was really "nothing was checked" wearing a
    # number. The score now abstains. Limitation score-neutrality is proven
    # properly, against a page that *was* readable, in
    # test_limitation_alongside_readable_page_is_score_neutral.
    assert report["summary"]["readiness_score"] is None


@__import__("responses").activate
def test_a_genuine_404_with_real_content_is_still_processed_normally():
    """The conservative scope matters: only 403/503 (the same pair the
    anti-bot fingerprint check already treats specially) are diverted to the
    "unconfirmed" limitation. An ordinary 404 with a real body — the
    overwhelmingly common case when a crawled link is simply dead — must
    keep going through normal content analysis exactly as before, so a
    legitimate error response is never swept into "possibly blocked".

    The body below is a genuine branded 404 page, which is what this test
    always meant by "real content". It previously asserted that against a
    24-character stub, and that stub was the bug in miniature: an error status
    carrying nothing but a line of text is now diverted rather than mined for
    content defects. See test_short_error_status_stub_is_not_analysed_as_content.
    """
    import responses

    from braiaudit.fetch import observe

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/gone",
        body="<html><head><title>Not Found</title></head><body><nav>"
        '<a href="/">Home</a> <a href="/products">Products</a> '
        '<a href="/support">Support</a></nav><main><h1>Page not found</h1>'
        "<p>The page you asked for is not here. It may have been moved when we "
        "reorganised the product catalogue in 2024, or the link that brought "
        "you here may be out of date. The most popular destinations are listed "
        "above, and our support team can find any discontinued product record "
        "for you if you tell them the old part number. Every discontinued item "
        "now redirects to its replacement, so an old bookmark should still "
        "reach something useful rather than landing on this page. If you "
        "believe this address should work, tell us where you followed the link "
        "from and we will repair it.</p></main></body></html>",
        status=404,
        content_type="text/html",
    )

    result = observe("https://example.com/gone")

    assert "http_error_status_unconfirmed" not in result["signals"]
    assert "http_error_status_blocked" not in result["signals"]
    assert result.get("raw_html") is not None
    assert result["soft_404_suspected"] is False  # status isn't 200, so N/A


@__import__("responses").activate
def test_discovery_callback_refuses_off_host_urls(monkeypatch):
    """The frontier's host guard was not the only way off-host URLs could be
    fetched. `observe()` follows redirects, so a depth-1 page that 301s
    off-host makes `_extract_internal_links` use the *redirected* host as its
    base — and the resulting third-party links, while correctly rejected by
    the frontier, were still handed to `discovery` in the same unfiltered
    list. Its `fetch_page_text` callback then fetched them, letting a third
    party's text feed answer-completeness for signals about the audited site.

    Reproduced before the fix: `https://evil.test/secret` — a URL appearing
    only on the off-host page — was fetched.
    """
    import responses

    from braiaudit import pipeline
    from braiaudit.pipeline import AuditOptions, run_audit

    fetched_by_callback: list[str] = []
    real = pipeline._observe_and_clean_text

    def spy(url, options, session, origin_cache=None):
        fetched_by_callback.append(url)
        return real(url, options, session, origin_cache)

    monkeypatch.setattr(pipeline, "_observe_and_clean_text", spy)

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(responses.GET, "https://evil.test/robots.txt", status=404)
    responses.add(responses.GET, "https://evil.test/sitemap.xml", status=404)

    home = (
        '<html><body><main><h1>Home</h1><a href="/hop">hop</a>'
        "<p>" + "Short. " * 5 + "</p></main></body></html>"
    )
    responses.add(
        responses.GET, "https://example.com/", body=home, status=200, content_type="text/html"
    )
    # An on-host page that redirects off-host mid-crawl — the only way a
    # third-party URL can reach `internal_links` at all, since the static
    # extractor filters plain off-host links at the producer.
    responses.add(
        responses.GET,
        "https://example.com/hop",
        status=301,
        headers={"Location": "https://evil.test/landing"},
    )
    offhost = (
        '<html><body><main><h1>Elsewhere</h1>'
        '<a href="https://evil.test/secret">secret</a>'
        "<p>" + "Third-party words about cost and support. " * 20 + "</p></main></body></html>"
    )
    responses.add(
        responses.GET,
        "https://evil.test/landing",
        body=offhost,
        status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET,
        "https://evil.test/secret",
        body=offhost,
        status=200,
        content_type="text/html",
    )

    run_audit(
        "example.com",
        options=AuditOptions(
            max_pages=10, max_render_pages=0, max_depth=2, corroborate=False
        ),
    )

    # The callback refused it: the fetch helper was never even reached for an
    # off-host URL, so nothing was fetched, rendered, cleaned or diagnosed.
    assert not any("evil.test" in u for u in fetched_by_callback), (
        f"discovery callback accepted an off-host URL: {fetched_by_callback}"
    )

    # And no request for it was ever issued.
    requested = [c.request.url for c in responses.calls]
    assert not any("evil.test/secret" in u for u in requested), (
        "an off-host URL discovered only on a third-party page was fetched"
    )

    # On-host candidates are unaffected — the guard rejects by host, not by
    # refusing to run discovery at all.
    assert any("example.com" in u for u in fetched_by_callback)


# ---------------------------------------------------------------------------
# Score honesty: "never checked" must not read as "checked and passed".
#
# Every audit below reached zero *analysable* pages — the site's content was
# never observed — so the readiness score abstains. That is an absent score,
# not a perfect one and not a failing one: nothing is deducted, a number is
# withheld. See report._score and the NO_ANALYSABLE_PAGE_EVIDENCE limitation.
# ---------------------------------------------------------------------------

_UNREACHABLE_LIMITATION = "No Page Yielded Content This Audit Could Analyse"


@__import__("responses").activate
def test_zero_page_unreachable_audit_abstains_from_scoring():
    """CASE A. Two real sites (asianpaints.com, nykaa.com) returned score 100
    off zero crawled pages: the observer was marked engaged before the fetch
    was known to have failed, so its modes counted as evaluable, none fired,
    and 100 x (1 - 0/possible) came out perfect. An unreachable site must
    yield no score at all."""
    import requests
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://example.com/", body=requests.ConnectionError("no route")
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=1, max_render_pages=0, corroborate=False),
    )

    assert report["meta"]["crawl"]["pages_crawled"] == 0
    assert report["summary"]["readiness_score"] is None
    assert all(a["score"] is None for a in report["summary"]["by_axis"].values())

    # Exactly one limitation, and it is the reachability one. The headline has
    # always said "See audit_limitations" here; until now that array was empty.
    limitations = report["audit_limitations"]
    assert [lim["title"] for lim in limitations] == [_UNREACHABLE_LIMITATION]
    assert "See audit_limitations" in report["summary"]["headline"]

    # A page we could not read is not a defect we may attribute to the site.
    assert report["findings"] == []
    assert report["summary"]["total_findings"] == 0

    # And the detail must not read "1 of 0 page(s) crawled".
    assert "of 0 page(s)" not in limitations[0]["detail"]


@__import__("responses").activate
def test_halted_only_audit_abstains_and_keeps_its_access_finding():
    """CASE B. etsy/lego/titan each fetched exactly one page — a bot challenge
    — and scored 90 or 85, better than gitlab's 77 off a complete 15-page
    crawl. The challenge is real evidence about reachability and keeps its
    critical finding; what it is not is evidence about the site's content."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="<html><body>Attention Required! | Cloudflare</body></html>",
        status=403,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=1, max_render_pages=0, corroborate=False),
    )

    # The page was fetched, so it counts as crawled — but nothing about the
    # site's content came back through it.
    assert report["meta"]["crawl"]["pages_crawled"] == 1
    assert report["summary"]["readiness_score"] is None
    assert all(a["score"] is None for a in report["summary"]["by_axis"].values())

    titles = {f["title"] for f in report["findings"]}
    assert "Anti-Bot Challenge Blocks Automated Access" in titles

    limitation_titles = [lim["title"] for lim in report["audit_limitations"]]
    assert _UNREACHABLE_LIMITATION in limitation_titles


@__import__("responses").activate
def test_readable_page_still_scores_normally():
    """CASE C. The abstention must be narrow. One analysable page is evidence,
    so the score stays a number — otherwise the fix would silently delete
    scoring for every site in the corpus."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=(
            "<html><head><title>Example Tools</title>"
            '<meta name="description" content="Example makes accounting tools.">'
            "</head><body><main><h1>Example Tools</h1>"
            "<p>Example Tools is an accounting suite for small businesses, "
            "based in Bengaluru, India. Pricing starts at 499 rupees a month. "
            "The product has shipped continuously since 2014 and is used by "
            "roughly forty thousand businesses across the country today.</p>"
            "</main></body></html>"
        ),
        status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=1, max_render_pages=0, corroborate=False),
    )

    assert report["meta"]["crawl"]["pages_crawled"] == 1
    assert isinstance(report["summary"]["readiness_score"], int)
    assert 0 <= report["summary"]["readiness_score"] <= 100
    assert any(a["score"] is not None for a in report["summary"]["by_axis"].values())
    assert _UNREACHABLE_LIMITATION not in {
        lim["title"] for lim in report["audit_limitations"]
    }


@__import__("responses").activate
def test_one_readable_page_among_unreachable_ones_still_scores():
    """CASE D. Partial evidence keeps the existing semantics untouched: a
    crawl that reached some pages and lost others is scored on what it read.
    No partial-credit gradient, no new philosophy — the abstention triggers
    only at zero."""
    import requests
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=(
            "<html><head><title>Example Tools</title></head><body><main>"
            "<h1>Example Tools</h1><p>Example Tools is an accounting suite "
            "for small businesses based in Bengaluru, India, shipping since "
            "2014 and used by forty thousand businesses today.</p>"
            '<a href="https://example.com/gone">Gone</a></main></body></html>'
        ),
        status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET,
        "https://example.com/gone",
        body=requests.ConnectionError("no route"),
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=5, max_render_pages=0, corroborate=False),
    )

    assert report["meta"]["crawl"]["pages_unreachable"] == ["https://example.com/gone"]
    assert report["summary"]["readiness_score"] is not None
    assert _UNREACHABLE_LIMITATION not in {
        lim["title"] for lim in report["audit_limitations"]
    }


def test_limitation_alongside_readable_page_is_score_neutral():
    """CASE E. Limitations describe the audit, not the site, so adding one
    must move no number. Asserted directly against the assembler: same
    findings, same evidence, once with a limitation-kind mode firing and once
    without."""
    from braiaudit.report import assemble_report

    def build(extra_signals: list[str]) -> dict:
        return assemble_report(
            site="example.com",
            findings_by_url={
                "https://example.com/": _diagnose(
                    "https://example.com/",
                    {"signals": ["missing_schema_org", *extra_signals]},
                )
            },
            pages_crawled=1,
            analysable_pages=1,
            skills_engaged={"pipeline", "website-observer", "crawl-render-audit"},
        )

    without = build([])
    with_limitation = build(["render_backend_unavailable"])

    assert with_limitation["audit_limitations"], "expected the limitation to fire"
    assert not without["audit_limitations"]
    assert (
        with_limitation["summary"]["readiness_score"]
        == without["summary"]["readiness_score"]
    )
    assert with_limitation["summary"]["by_axis"] == without["summary"]["by_axis"]


@__import__("responses").activate
def test_abstaining_report_still_validates_against_the_schema():
    """CASE F. `null` was always the documented intent — the schema types
    readiness_score as ["integer", "null"] and by_axis.score likewise — so the
    abstaining report must validate with no schema change at all."""
    import requests
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit
    from braiaudit.schemas import validate

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://example.com/", body=requests.ConnectionError("down")
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=1, max_render_pages=0, corroborate=False),
    )

    validate(report, "audit-report")
    assert report["summary"]["readiness_score"] is None


# ---------------------------------------------------------------------------
# Apex -> www fallback.
#
# Typing a bare domain into a browser resolves apex-or-www transparently; the
# crawler could not, so an apex with no DNS record (asianpaints.com) or one
# answering 404 (nykaa.com) ended the audit at zero readable pages while the
# real site sat on `www.`. The retry is narrow by design: a refusal (403/429/
# 503, any challenge) is never retried under a second hostname, because that
# is circumvention rather than reachability. See pipeline._www_fallback_url.
# ---------------------------------------------------------------------------

_WWW_HOME = (
    "<html><head><title>Example Tools</title></head><body><main>"
    '<h1>Example Tools</h1><a href="/pricing">Pricing</a>'
    "<p>Example Tools is an accounting suite for small businesses based in "
    "Bengaluru, India. Pricing starts at 499 rupees a month and the product "
    "has shipped continuously since 2014.</p></main></body></html>"
)


def _www_calls(calls, url: str = "https://www.example.com/") -> list[str]:
    """Every request made for `url`, so "exactly once" is measurable."""
    return [c.request.url for c in calls if c.request.url == url]


@__import__("responses").activate
def test_apex_connection_failure_falls_back_to_www_once():
    """CASE A. asianpaints.com: the apex has no usable DNS record at all, so
    the existing redirect-based expansion had nothing to expand from."""
    import requests
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://example.com/", body=requests.ConnectionError("no A record")
    )
    responses.add(responses.GET, "https://www.example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://www.example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET, "https://www.example.com/", body=_WWW_HOME, status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET, "https://www.example.com/pricing", body=_WWW_HOME, status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=5, max_render_pages=0, corroborate=False),
    )

    # The apex stays recorded as unreachable — the fallback adds reach, it does
    # not paper over what happened.
    assert report["meta"]["crawl"]["pages_unreachable"] == ["https://example.com/"]
    # And the crawl actually proceeded on www, including its link graph.
    assert report["meta"]["crawl"]["pages_crawled"] >= 2
    assert report["summary"]["readiness_score"] is not None
    assert len(_www_calls(responses.calls)) == 1


@__import__("responses").activate
def test_apex_404_falls_back_to_www():
    """CASE B. nykaa.com's shape: the apex resolves but answers 404."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://example.com/", body="", status=404,
        content_type="text/html",
    )
    responses.add(responses.GET, "https://www.example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://www.example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET, "https://www.example.com/", body=_WWW_HOME, status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET, "https://www.example.com/pricing", body=_WWW_HOME, status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=5, max_render_pages=0, corroborate=False),
    )

    assert report["meta"]["crawl"]["pages_crawled"] >= 2
    assert report["summary"]["readiness_score"] is not None
    assert len(_www_calls(responses.calls)) == 1


@__import__("pytest").mark.parametrize(
    "status,body",
    [
        (403, "<html><body>Forbidden</body></html>"),
        (503, "<html><body>Service Unavailable</body></html>"),
        (429, "<html><body>Too Many Requests</body></html>"),
        (403, "<html><body>Attention Required! | Cloudflare</body></html>"),
    ],
    ids=["403", "503", "429", "anti-bot-challenge"],
)
def test_deliberate_refusals_never_trigger_the_www_fallback(status, body):
    """CASES C-F, the safety boundary. 403, 503, 429 and an anti-bot challenge
    are the site declining to serve a crawler. Asking the same site again under
    a second hostname would be working around that refusal, so the fallback
    must not fire — and crucially it must not fire for reasons of *status*,
    since a bodiless 403 and a bodiless 404 raise the identical
    http_error_status_blocked halt signal."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    @responses.activate
    def run():
        responses.add(responses.GET, "https://example.com/robots.txt", status=404)
        responses.add(
            responses.GET, "https://example.com/", body=body, status=status,
            content_type="text/html",
        )
        # Registered but must never be reached.
        responses.add(responses.GET, "https://www.example.com/robots.txt", status=404)
        responses.add(
            responses.GET, "https://www.example.com/", body=_WWW_HOME, status=200,
            content_type="text/html",
        )
        rep = run_audit(
            "example.com",
            options=AuditOptions(max_pages=5, max_render_pages=0, corroborate=False),
        )
        return rep, _www_calls(responses.calls)

    report, www_hits = run()

    assert www_hits == [], f"HTTP {status} must not be retried on www"
    # P0 still governs the outcome: nothing analysable, so no score.
    assert report["summary"]["readiness_score"] is None


@__import__("responses").activate
def test_www_fallback_failure_does_not_retry_further():
    """CASE G. When www fails too, the audit stops trying: one attempt, both
    hosts recorded unreachable, and P0's abstention rather than a score."""
    import requests
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://example.com/", body=requests.ConnectionError("down")
    )
    responses.add(responses.GET, "https://www.example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://www.example.com/", body=requests.ConnectionError("down")
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=5, max_render_pages=0, corroborate=False),
    )

    assert sorted(report["meta"]["crawl"]["pages_unreachable"]) == [
        "https://example.com/",
        "https://www.example.com/",
    ]
    assert len(_www_calls(responses.calls)) == 1
    assert report["meta"]["crawl"]["pages_crawled"] == 0
    assert report["summary"]["readiness_score"] is None
    assert "No Page Yielded Content This Audit Could Analyse" in {
        lim["title"] for lim in report["audit_limitations"]
    }


@__import__("responses").activate
def test_successful_apex_to_www_redirect_adds_no_duplicate_fetch():
    """CASE H. The ordinary case must be untouched: a 301 onto www already
    lands on the right host, so the fallback has nothing to add and must not
    re-request it."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://www.example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://www.example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET, "https://example.com/", status=301,
        headers={"Location": "https://www.example.com/"},
    )
    responses.add(
        responses.GET, "https://www.example.com/", body=_WWW_HOME, status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET, "https://www.example.com/pricing", body=_WWW_HOME, status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=5, max_render_pages=0, corroborate=False),
    )

    # Exactly one request for the www home page: the redirect's own, with no
    # second one bolted on by the fallback.
    assert len(_www_calls(responses.calls)) == 1
    assert report["summary"]["readiness_score"] is not None


@__import__("responses").activate
def test_a_404_after_the_redirect_is_not_retried_on_the_same_host():
    """The awkward corner of CASE H: the apex 301s to www and *www* is the
    thing returning 404. The candidate host is then the host already fetched,
    so retrying would repeat an identical failure."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://www.example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://example.com/", status=301,
        headers={"Location": "https://www.example.com/"},
    )
    responses.add(
        responses.GET, "https://www.example.com/", body="", status=404,
        content_type="text/html",
    )

    run_audit(
        "example.com",
        options=AuditOptions(max_pages=5, max_render_pages=0, corroborate=False),
    )

    assert len(_www_calls(responses.calls)) == 1


@__import__("responses").activate
def test_subdomain_seed_is_never_rewritten_to_www():
    """CASE I. `docs.example.com` is a subdomain someone chose, not an apex
    missing its `www.`. Rewriting it would audit a different site."""
    import requests
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://docs.example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://docs.example.com/", body=requests.ConnectionError("x")
    )
    responses.add(responses.GET, "https://www.example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://www.example.com/", body=_WWW_HOME, status=200,
        content_type="text/html",
    )

    report = run_audit(
        "docs.example.com",
        options=AuditOptions(max_pages=5, max_render_pages=0, corroborate=False),
    )

    assert _www_calls(responses.calls) == []
    assert report["meta"]["crawl"]["pages_unreachable"] == ["https://docs.example.com/"]
    assert report["summary"]["readiness_score"] is None


@__import__("responses").activate
def test_www_robots_disallow_is_respected_not_bypassed():
    """CASE J. The fallback reaches a new origin, so that origin's robots.txt
    governs it. observe() fetches robots per origin, so the www host's own
    rules apply rather than the apex's — and a disallow halts the page."""
    import requests
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://example.com/", body=requests.ConnectionError("x")
    )
    responses.add(
        responses.GET,
        "https://www.example.com/robots.txt",
        body="User-agent: *\nDisallow: /\n",
        status=200,
        content_type="text/plain",
    )
    responses.add(
        responses.GET, "https://www.example.com/", body=_WWW_HOME, status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=5, max_render_pages=0, corroborate=False),
    )

    # The disallow is observed and reported, not worked around.
    assert any("Robots" in f["title"] for f in report["findings"]), [
        f["title"] for f in report["findings"]
    ]
    # Disallowed means not analysed, so P0 abstains rather than scoring it.
    assert report["summary"]["readiness_score"] is None


@__import__("pytest").mark.parametrize(
    "apex", ["example.co.in", "example.co.uk", "example.gov.in", "example.ac.in"]
)
def test_multipart_suffix_apexes_still_fall_back(apex):
    """CASE K. `example.co.in` is an apex, not a subdomain of the `co.in`
    public suffix — exactly the distinction registrable_domain() exists to
    draw, and the one the fallback's apex test depends on."""
    import requests
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    @responses.activate
    def run():
        responses.add(responses.GET, f"https://{apex}/robots.txt", status=404)
        responses.add(
            responses.GET, f"https://{apex}/", body=requests.ConnectionError("x")
        )
        responses.add(responses.GET, f"https://www.{apex}/robots.txt", status=404)
        responses.add(responses.GET, f"https://www.{apex}/sitemap.xml", status=404)
        responses.add(
            responses.GET, f"https://www.{apex}/", body=_WWW_HOME, status=200,
            content_type="text/html",
        )
        rep = run_audit(
            apex, options=AuditOptions(max_pages=3, max_render_pages=0, corroborate=False)
        )
        return rep, _www_calls(responses.calls, f"https://www.{apex}/")

    report, hits = run()
    assert hits == [f"https://www.{apex}/"]
    assert report["summary"]["readiness_score"] is not None


@__import__("responses").activate
def test_fallback_cannot_widen_scope_to_an_unrelated_domain():
    """CASE L. `www.` is prepended, never substituted, so the registrable
    domain is unchanged by construction. A third-party link on the recovered
    www page is still refused by the frontier."""
    import requests
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://example.com/", body=requests.ConnectionError("x")
    )
    responses.add(responses.GET, "https://www.example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://www.example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://www.example.com/",
        body=_WWW_HOME.replace(
            '<a href="/pricing">Pricing</a>',
            '<a href="/pricing">Pricing</a><a href="https://evil.test/x">Partner</a>',
        ),
        status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET, "https://www.example.com/pricing", body=_WWW_HOME, status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(
            max_pages=5, max_render_pages=0, max_depth=2, corroborate=False
        ),
    )

    requested = {c.request.url for c in responses.calls}
    assert not any("evil.test" in u for u in requested), requested
    touched = {u for f in report["findings"] for u in f["affected_urls"]}
    touched |= set(report["meta"]["crawl"]["pages_unreachable"])
    assert all("example.com" in u for u in touched), touched


def test_www_fallback_url_unit_boundaries():
    """The whole condition table in one place, so the boundary is readable
    without reconstructing it from five integration tests."""
    from braiaudit.pipeline import _www_fallback_url

    apex = "https://example.com/"
    assert _www_fallback_url(apex, {"http_status": None}) == "https://www.example.com/"
    assert _www_fallback_url(apex, {"http_status": 404}) == "https://www.example.com/"

    # Refusals and successes alike are left alone.
    for status in (200, 301, 401, 403, 429, 500, 503):
        assert _www_fallback_url(apex, {"http_status": status}) is None, status

    # Not an apex.
    assert _www_fallback_url("https://docs.example.com/", {"http_status": 404}) is None
    assert _www_fallback_url("https://www.example.com/", {"http_status": 404}) is None

    # Multi-part suffixes are apexes.
    assert (
        _www_fallback_url("https://titan.co.in/", {"http_status": None})
        == "https://www.titan.co.in/"
    )
    assert (
        _www_fallback_url("https://iitb.ac.in/", {"http_status": 404})
        == "https://www.iitb.ac.in/"
    )

    # Already landed on www via redirect: nothing left to try.
    assert (
        _www_fallback_url(
            apex, {"http_status": 404, "final_url": "https://www.example.com/"}
        )
        is None
    )
