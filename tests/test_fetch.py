"""Tests for the website-observer implementation (braiaudit.fetch)."""

from __future__ import annotations

import responses

from braiaudit.fetch import observe


@responses.activate
def test_robots_disallow_halts_before_fetching_target(fixture_html):
    responses.add(
        responses.GET,
        "https://example.com/robots.txt",
        body="User-agent: *\nDisallow: /\n",
        status=200,
    )
    # No stub registered for the target URL itself — if observe() tried to
    # fetch it despite the disallow, `responses` would raise ConnectionError
    # and fail this test.
    result = observe("https://example.com/", user_agent="TestBot/1.0")

    assert "robots_txt_disallow" in result["signals"]
    assert result["robots_txt"]["disallowed_for_agent"] is True
    assert result["http_status"] is None


@responses.activate
def test_app_shell_page_emits_all_three_signals(fixture_html):
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=fixture_html("app_shell.html"),
        status=200,
        content_type="text/html",
    )

    result = observe("https://example.com/", user_agent="TestBot/1.0")

    assert result["http_status"] == 200
    expected = {"low_raw_text", "high_script_count", "root_container_detected"}
    assert set(result["signals"]) >= expected
    assert result["root_container_detected"] is True


@responses.activate
def test_clean_article_page_has_no_render_or_shell_signals(fixture_html):
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/blog/post",
        body=fixture_html("clean_article.html"),
        status=200,
        content_type="text/html",
    )

    result = observe("https://example.com/blog/post", user_agent="TestBot/1.0")

    assert "low_raw_text" not in result["signals"]
    assert "root_container_detected" not in result["signals"]
    assert result["json_ld_present"] is True
    assert "missing_schema_org" not in result["signals"]
    assert result["canonical_tag_present"] is True
    assert "canonical_missing" not in result["signals"]


@responses.activate
def test_rate_limit_stops_after_one_retry():
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET, "https://example.com/", status=429, headers={"Retry-After": "0"}
    )
    responses.add(
        responses.GET, "https://example.com/", status=429, headers={"Retry-After": "0"}
    )

    result = observe("https://example.com/", user_agent="TestBot/1.0")

    assert "http_429_rate_limit" in result["signals"]


@responses.activate
def test_soft_404_detected_on_200_status(fixture_html):
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/missing",
        body="<html><head><title>Page Not Found</title></head><body>"
        "<p>Sorry, we couldn't find that page. It may have been moved or removed. "
        "Please check the URL or return to the homepage to continue browsing our site.</p>"
        "</body></html>",
        status=200,
        content_type="text/html",
    )

    result = observe("https://example.com/missing", user_agent="TestBot/1.0")

    assert result["soft_404_suspected"] is True
    assert "soft_404_suspected" in result["signals"]


# ---------------------------------------------------------------------------
# Soft-404: an error stub served at HTTP 200 must never be read as the site's
# own content. Found live on iitb.ac.in, whose apex returned 200 carrying
# "404 Unknown host"; the audit analysed it as the institution's homepage and
# produced seven content and identity findings from a page that was not the
# institution's at all.
#
# Detection is deliberately two-part — phrase AND short body — because a
# halt is destructive: see fetch._detect_soft_404.
# ---------------------------------------------------------------------------

_SOFT_404_FINDING = "Soft 404 Returns Success Status For Missing Content"

# Real prose, long enough to be unmistakably an article rather than a stub.
_DOCS_PARAGRAPH = (
    "The HTTP 404 Not Found response status code indicates that the server "
    "cannot find the requested resource. Links that lead to a 404 page are "
    "often called broken or dead links and can be subject to link rot. A 404 "
    "status code only indicates that the resource is missing: not whether the "
    "absence is temporary or permanent. If a resource is permanently removed, "
    "use the 410 Gone status instead. Browsers display this response when a "
    "page not found condition occurs, and servers may return a custom page "
    "not found document explaining the problem to the reader. Search engines "
    "treat a soft 404 - a page that says not found while returning 200 - as a "
    "crawl quality problem, because the status line and the body disagree "
    "about whether anything is there. "
)


# Above _SOFT_404_MAX_TEXT, so it reads as the article it is rather than a
# stub that happens to name the phrases.
_LONG_ARTICLE = _DOCS_PARAGRAPH * 2


def test_hosting_soft_404_is_detected_on_a_short_body():
    """CASE A. The iitb.ac.in family: the origin never resolved the vhost and
    the edge returned its own stub at 200."""
    from braiaudit.fetch import _detect_soft_404

    assert _detect_soft_404(200, "404 Unknown host", "404 Unknown host") is True
    assert _detect_soft_404(200, "", "No such host at this address") is True
    assert _detect_soft_404(200, "", "default backend - 404") is True


def test_application_soft_404_still_detected():
    """CASE B. The original client-side-router family must keep working —
    adding corroboration must not silently disable the existing detector."""
    from braiaudit.fetch import _detect_soft_404

    assert (
        _detect_soft_404(
            200,
            "Page Not Found",
            "Sorry, we couldn't find that page. It may have been moved.",
        )
        is True
    )
    # Status still gates everything: a real 404 is not a *soft* 404.
    assert _detect_soft_404(404, "Page Not Found", "Page not found") is False


def test_long_documentation_page_about_404s_is_not_a_soft_404():
    """CASE C. The false-positive that matters. A page documenting HTTP status
    codes contains every phrase the detector looks for — and this corpus
    audits documentation sites. Length is what separates a page *about* 404s
    from a page that *is* one."""
    from braiaudit.fetch import _SOFT_404_MAX_TEXT, _detect_soft_404

    article = _DOCS_PARAGRAPH * 4
    assert len(article) > _SOFT_404_MAX_TEXT
    assert "404 not found" in article.lower()
    assert "page not found" in article.lower()

    assert _detect_soft_404(200, "404 Not Found - HTTP | MDN", article) is False


def test_short_page_mentioning_404_terminology_is_accepted_as_a_soft_404():
    """CASE D, documented rather than asserted-around: a *short* page carrying
    the phrase IS classified as a soft 404, and that is the deliberate trade.

    Below the length threshold the detector cannot distinguish a terse note
    about 404s from an actual error stub — nothing observable separates them.
    The bias is chosen: a short page has almost no content to lose by being
    halted, while analysing a real stub fabricates findings against the brand.
    A site wanting such a page audited should give it enough substance to
    clear the threshold, which is also what would make it useful to a reader."""
    from braiaudit.fetch import _SOFT_404_MAX_TEXT, _detect_soft_404

    terse = "Our 404 Not Found page explains what to do next."
    assert len(terse) < _SOFT_404_MAX_TEXT
    assert _detect_soft_404(200, "About our error pages", terse) is True


def test_soft_404_haystack_is_visible_text_not_markup():
    """Markup is the wrong haystack: an href, a CSS class or inline JSON can
    carry the phrase without a reader ever seeing it. Detection reads what
    `observe()` extracts, so a page whose only 'page not found' lives in a
    link target is analysed normally."""
    import responses

    from braiaudit.fetch import observe

    @responses.activate
    def run() -> dict:
        responses.add(responses.GET, "https://example.com/robots.txt", status=404)
        responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
        responses.add(
            responses.GET,
            "https://example.com/",
            body=(
                "<html><head><title>Example Tools</title></head><body><main>"
                '<a href="/page-not-found-handler">Support</a>'
                '<div class="page-not-found-banner"></div>'
                "<p>" + _LONG_ARTICLE + "</p></main></body></html>"
            ),
            status=200,
            content_type="text/html",
        )
        return observe("https://example.com/", user_agent="TestBot/1.0")

    assert run()["soft_404_suspected"] is False


@__import__("responses").activate
def test_soft_404_halts_before_any_content_conclusion_is_drawn(monkeypatch):
    """CASE E, the half that actually fixes iitb. Detecting the stub was never
    enough — the pipeline detected soft 404s already and analysed them anyway.
    Nothing downstream may treat the stub as site evidence: no render, no
    discovery, no corroboration, and above all no content or identity
    findings invented from a page that is not the site."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    rendered: list[str] = []

    def spy_render(url, *args, **kwargs):
        rendered.append(url)
        return {"available": False, "signals": []}

    monkeypatch.setattr("braiaudit.pipeline.render.render", spy_render)

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="<html><head><title>404 Unknown host</title></head>"
        "<body>404 Unknown host</body></html>",
        status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=5, max_render_pages=2, corroborate=True),
    )

    # The browser is never launched for a page we refused to read.
    assert rendered == []

    titles = {f["title"] for f in report["findings"]}

    # The one true observation survives: a 200 carrying an error stub.
    assert _SOFT_404_FINDING in titles

    # And nothing was concluded about content or identity from that stub.
    # These are exactly the seven iitb.ac.in produced.
    fabricated = {
        "No Machine-Readable Contact Or Location Details",
        "No Schema.org / JSON-LD Structured Data Available",
        "No Public Entity Record Corroborates This Brand",
        "Public Entity Record Does Not Point Back To This Site",
        "Content Is Not Written In The Register Users Ask Questions In",
        "Page Has No Meta Description",
        "Sitemap Change Dates Carry No Information",
        "Last-Modified Header Is Missing Or Long Out Of Date",
        "Page Does Not State What It Is",
    }
    assert not (titles & fabricated), titles & fabricated

    # Corroboration must not have used the stub as evidence of the brand.
    assert report["site_info"]["brand_name_candidates"] == []
    assert report["meta"]["crawl"]["pages_rendered"] == 0

    # P0 semantics hold on top: the stub is not analysable content, so the
    # audit abstains rather than scoring the brand on a page it never read.
    assert report["summary"]["readiness_score"] is None
    assert "No Page Yielded Content This Audit Could Analyse" in {
        lim["title"] for lim in report["audit_limitations"]
    }


@__import__("responses").activate
def test_normal_page_is_unaffected_by_the_soft_404_halt():
    """The halt must be narrow: a real page carrying real content is analysed
    exactly as before, scored, and never halted."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=(
            "<html><head><title>Example Tools</title></head><body><main>"
            "<h1>Example Tools</h1><p>" + _LONG_ARTICLE + "</p></main></body></html>"
        ),
        status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=1, max_render_pages=0, corroborate=False),
    )

    assert _SOFT_404_FINDING not in {f["title"] for f in report["findings"]}
    assert report["meta"]["crawl"]["pages_crawled"] == 1
    assert report["summary"]["readiness_score"] is not None


def test_short_error_status_stub_is_not_analysed_as_content():
    """The iitb.ac.in case as it actually is, corrected from the Stage 5
    write-up: the apex returns HTTP **404** (not 200) with 104 characters of
    "404 Unknown host". Two guards existed — a bodiless 4xx, and 403/503 with
    a body — and a short-bodied 404 fell between them into full content
    analysis, producing seven findings about a page belonging to no one.

    Length decides, not status: nothing this short describes a site."""
    import responses

    from braiaudit.fetch import observe

    @responses.activate
    def run() -> dict:
        responses.add(responses.GET, "https://example.com/robots.txt", status=404)
        responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
        responses.add(
            responses.GET,
            "https://example.com/",
            body="<html><head><title>404 Unknown host</title></head>"
            "<body>404 Unknown host</body></html>",
            status=404,
            content_type="text/html",
        )
        return observe("https://example.com/", user_agent="TestBot/1.0")

    result = run()

    assert result["http_status"] == 404
    assert "http_error_status_blocked" in result["signals"]

    # None of the content or identity signals may be emitted about a stub.
    fabricated = {
        "missing_schema_org",
        "contact_details_absent",
        "meta_description_absent",
        "last_modified_stale_or_absent",
        "sitemap_lastmod_meaningless",
        "no_page_identifying_heading",
        "canonical_missing",
    }
    assert not (set(result["signals"]) & fabricated), set(result["signals"]) & fabricated


@__import__("responses").activate
def test_error_stub_seed_yields_an_access_finding_and_no_fabricated_defects():
    """End to end, the shape iitb.ac.in should have produced all along: one
    honest access finding, no content or identity accusations, and — via P0,
    since a stub is not analysable content — an abstained score rather than
    the 90 it was awarded."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="<html><head><title>404 Unknown host</title></head>"
        "<body>404 Unknown host</body></html>",
        status=404,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=5, max_render_pages=2, corroborate=True),
    )

    titles = {f["title"] for f in report["findings"]}
    assert titles == {"Page Returns An Error Status With No Retrievable Content"}, titles

    assert report["summary"]["readiness_score"] is None
    assert "No Page Yielded Content This Audit Could Analyse" in {
        lim["title"] for lim in report["audit_limitations"]
    }
    assert report["meta"]["crawl"]["pages_rendered"] == 0
    # The stub's title must never become a brand name for the site.
    assert report["site_info"]["brand_name_candidates"] == []


@responses.activate
def test_robots_disallow_is_reevaluated_per_path_within_one_origin():
    """Regression: the per-origin robots cache used to freeze the allow/deny
    verdict of the first URL, so a robots file allowing `/` but disallowing
    `/private/` was treated as allow-all for every later page."""
    responses.add(
        responses.GET,
        "https://example.com/robots.txt",
        body="User-agent: *\nDisallow: /private\n",
        status=200,
    )
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="<html><body><p>Welcome</p></body></html>",
        status=200,
        content_type="text/html",
    )

    cache: dict = {}
    allowed = observe("https://example.com/", user_agent="TestBot/1.0", origin_cache=cache)
    blocked = observe(
        "https://example.com/private/x", user_agent="TestBot/1.0", origin_cache=cache
    )

    assert "robots_txt_disallow" not in allowed["signals"]
    assert allowed["http_status"] == 200
    # No stub for /private/x: if observe() fetched it despite the disallow,
    # `responses` raises ConnectionError and fails the test.
    assert "robots_txt_disallow" in blocked["signals"]
    assert blocked["http_status"] is None


@responses.activate
def test_long_retry_after_is_honoured_without_an_immediate_refetch():
    """Regression: a 429 with Retry-After of an hour used to be re-requested
    5 seconds later; the server's requested delay must be taken at its word."""
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        status=429,
        headers={"Retry-After": "3600"},
    )

    result = observe("https://example.com/", user_agent="TestBot/1.0")

    assert "http_429_rate_limit" in result["signals"]
    assert responses.assert_call_count("https://example.com/", 1) is True  # no retry GET


@responses.activate
def test_xhtml_page_body_is_read_not_silently_dropped():
    """Regression: `application/xhtml+xml` matched no body-extraction rule,
    so a 200 XHTML page returned zero signals — a false clean."""
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="<html><head><title>XHTML page</title></head>"
        "<body><p>"
        + "A real page with real prose content for the reader. " * 10
        + "</p></body></html>",
        status=200,
        content_type="application/xhtml+xml",
    )

    result = observe("https://example.com/", user_agent="TestBot/1.0")

    assert result["http_status"] == 200
    assert result["raw_text_length"] > 0
    assert "low_raw_text" not in result["signals"]


# --- Sitemap handling: index expansion, bounded and honest -----------------

def _sitemap(loc_mod_pairs, kind="urlset"):
    body = "".join(
        f"<{'url' if kind == 'urlset' else 'sitemap'}>"
        f"<loc>{loc}</loc>{f'<lastmod>{mod}</lastmod>' if mod else ''}"
        f"</{'url' if kind == 'urlset' else 'sitemap'}>"
        for loc, mod in loc_mod_pairs
    )
    root = "urlset" if kind == "urlset" else "sitemapindex"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<{root} xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</{root}>'
    )


@responses.activate
def test_sitemap_index_expands_children():
    """A sitemapindex's <loc> entries are child sitemaps, not pages. The old
    parser returned them as the page-URL set, so every real page read as
    'not in sitemap' and per-page lastmod analysis ran over child dates."""
    import requests as rq

    from braiaudit.fetch import fetch_sitemap_urls

    responses.add(
        responses.GET, "https://example.com/sitemap.xml",
        body=_sitemap([
            ("https://example.com/sitemap-pages.xml", "2026-01-01"),
            ("https://example.com/sitemap-posts.xml", "2026-02-01"),
        ], kind="sitemapindex"), status=200,
    )
    responses.add(
        responses.GET, "https://example.com/sitemap-pages.xml",
        body=_sitemap([("https://example.com/about", "2026-01-02"),
                       ("https://example.com/pricing", None)]), status=200,
    )
    responses.add(
        responses.GET, "https://example.com/sitemap-posts.xml",
        body=_sitemap([("https://example.com/blog/1", "2026-03-03")]), status=200,
    )

    pages, skipped = fetch_sitemap_urls("https://example.com", [], rq.Session())

    assert all(not url.endswith(".xml") for url in pages)
    assert set(pages) == {
        "https://example.com/about",
        "https://example.com/pricing",
        "https://example.com/blog/1",
    }
    # lastmod is paired with its own page URL, not a parallel list.
    assert pages["https://example.com/about"] == "2026-01-02"
    assert pages["https://example.com/pricing"] is None
    assert pages["https://example.com/blog/1"] == "2026-03-03"
    assert skipped == []


@responses.activate
def test_sitemap_index_skips_malformed_child():
    import requests as rq

    from braiaudit.fetch import fetch_sitemap_urls

    responses.add(
        responses.GET, "https://example.com/sitemap.xml",
        body=_sitemap([
            ("https://example.com/sitemap-bad.xml", ""),
            ("https://example.com/sitemap-good.xml", ""),
        ], kind="sitemapindex"), status=200,
    )
    responses.add(
        responses.GET, "https://example.com/sitemap-bad.xml",
        body="<urlset><loc>unclosed", status=200,
    )
    responses.add(
        responses.GET, "https://example.com/sitemap-good.xml",
        body=_sitemap([("https://example.com/ok", "2026-04-01")]), status=200,
    )

    pages, skipped = fetch_sitemap_urls("https://example.com", [], rq.Session())

    assert pages == {"https://example.com/ok": "2026-04-01"}


@responses.activate
def test_sitemap_index_skips_nested_index_and_cross_origin_children():
    """A nested index and a third-party child sitemap must never have their
    URLs read as pages; both are skipped with a recorded reason."""
    import requests as rq

    from braiaudit.fetch import fetch_sitemap_urls

    responses.add(
        responses.GET, "https://example.com/sitemap.xml",
        body=_sitemap([
            ("https://example.com/sitemap-inner.xml", ""),
            ("https://other.example.org/sitemap.xml", ""),
        ], kind="sitemapindex"), status=200,
    )
    responses.add(
        responses.GET, "https://example.com/sitemap-inner.xml",
        body=_sitemap([
            ("https://example.com/sitemap-deeper.xml", ""),
        ], kind="sitemapindex"), status=200,
    )

    pages, skipped = fetch_sitemap_urls("https://example.com", [], rq.Session())

    assert pages == {}
    assert any("nested sitemap index" in s for s in skipped)
    assert any("cross-origin" in s for s in skipped)
    # The nested index's own children were never requested.
    assert not any("sitemap-deeper" in c.request.url for c in responses.calls)


@responses.activate
def test_plain_urlset_returns_pages_with_lastmods():
    import requests as rq

    from braiaudit.fetch import fetch_sitemap_urls

    responses.add(
        responses.GET, "https://example.com/sitemap.xml",
        body=_sitemap([("https://example.com/a", "2026-05-01"),
                       ("https://example.com/b", "2026-06-01")]), status=200,
    )

    pages, skipped = fetch_sitemap_urls("https://example.com", [], rq.Session())

    assert pages == {"https://example.com/a": "2026-05-01",
                     "https://example.com/b": "2026-06-01"}
    assert skipped == []


# --- URL canonicalization: case-insensitive hosts, case-sensitive paths ----

def test_canonical_lowercases_host_and_preserves_path_case():
    from braiaudit.fetch import canonical_crawl_url, site_label

    assert site_label("EXAMPLE.COM") == "example.com"
    assert (
        canonical_crawl_url("https://EXAMPLE.COM/docs/API#Top")
        == "https://example.com/docs/API"
    )
    # The path is case-sensitive; a content-management URL that differs only
    # by path case is a different page, not a duplicate spelling.
    assert canonical_crawl_url("https://example.com/About") == "https://example.com/About"


def test_tracking_params_stripped_meaningful_params_preserved():
    from braiaudit.fetch import canonical_crawl_url

    assert canonical_crawl_url("https://example.com/pricing?utm_source=x") == (
        "https://example.com/pricing"
    )
    assert canonical_crawl_url("https://example.com/pricing?page=2") == (
        "https://example.com/pricing?page=2"
    )
    assert canonical_crawl_url("https://example.com/product?id=1&utm_source=x&fbclid=abc") == (
        "https://example.com/product?id=1"
    )
    assert canonical_crawl_url("https://example.com/search?q=things+to+buy&gclid=xyz") == (
        "https://example.com/search?q=things+to+buy"
    )
