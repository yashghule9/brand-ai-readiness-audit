"""End-to-end test of braiaudit.pipeline.run_audit against mocked HTTP,
exercising the full six-step sequence without a network or a browser."""

from __future__ import annotations

import requests
import responses

from braiaudit.pipeline import AuditOptions, run_audit
from braiaudit.schemas import validate


@responses.activate
def test_full_pipeline_produces_a_schema_valid_report(fixture_html):
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=fixture_html("boilerplate_heavy.html"),
        status=200,
        content_type="text/html",
    )

    report = run_audit("example.com", options=AuditOptions(max_pages=1, max_render_pages=0))

    validate(report, "audit-report")
    assert report["site"] == "example.com"
    titles = {f["title"] for f in report["findings"]}
    assert "High Noise-to-Content Ratio Obstructs Data Extraction" in titles
    assert "No Schema.org / JSON-LD Structured Data Available" in titles

    # meta-analysis: coverage reflects what actually ran (no render backend
    # engaged, so render-only categories read visibly below 100%), and
    # structural validation of the report itself passes.
    meta = report["meta"]
    assert "website-observer" in meta["coverage"]["skills_engaged"]
    assert "crawl-render-audit" not in meta["coverage"]["skills_engaged"]
    assert meta["coverage"]["categories"]["rendering_and_execution"]["coverage_pct"] < 100.0
    assert meta["validation"]["passed"] is True


@responses.activate
def test_pipeline_engages_render_dependent_coverage_only_when_render_ran(fixture_html):
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=fixture_html("clean_article.html"),
        status=200,
        content_type="text/html",
    )

    # clean_article.html has no CSR/app-shell signals, so render never
    # triggers even with a nonzero budget — coverage should reflect that
    # crawl-render-audit's checks were not engaged for this page.
    report = run_audit("example.com", options=AuditOptions(max_pages=1, max_render_pages=5))

    assert "crawl-render-audit" not in report["meta"]["coverage"]["skills_engaged"]


@responses.activate
def test_robots_disallow_halts_that_url_without_crashing():
    responses.add(
        responses.GET,
        "https://example.com/robots.txt",
        body="User-agent: *\nDisallow: /\n",
        status=200,
    )

    report = run_audit("example.com", options=AuditOptions(max_pages=1, max_render_pages=0))

    validate(report, "audit-report")
    # A blanket `Disallow: /` blocks this auditor *and* every AI answer-engine
    # crawler — two distinct critical findings, since a brand can fix one
    # without fixing the other.
    assert report["summary"]["critical"] == 2
    titles = {f["title"] for f in report["findings"]}
    assert titles == {
        "Robots.txt Rules Block AI Scrapers",
        "Robots.txt Blocks AI Answer-Engine Crawlers",
    }


@responses.activate
def test_ai_crawler_block_is_caught_even_when_this_auditor_is_allowed(fixture_html):
    """The headline visibility failure: a site that welcomes classic search
    crawlers while turning away the agents that feed AI assistants."""
    responses.add(
        responses.GET,
        "https://example.com/robots.txt",
        body="User-agent: Googlebot\nAllow: /\n\nUser-agent: GPTBot\nDisallow: /\n",
        status=200,
    )
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=fixture_html("clean_article.html"),
        status=200,
        content_type="text/html",
    )

    report = run_audit("example.com", options=AuditOptions(max_pages=1, max_render_pages=0))

    validate(report, "audit-report")
    finding = next(
        f for f in report["findings"] if f["title"] == "Robots.txt Blocks AI Answer-Engine Crawlers"
    )
    assert finding["severity"] == "critical"
    assert "GPTBot" in finding["evidence"]
    assert "Googlebot" in finding["evidence"]
    # The action must tell the brand what to change, not name an audit tool.
    assert "playwright" not in finding["suggested_action"]["summary"].lower()


@responses.activate
def test_unreachable_seed_produces_no_fabricated_findings():
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=requests.exceptions.ConnectionError("boom"),
    )

    report = run_audit("example.com", options=AuditOptions(max_pages=1, max_render_pages=0))

    validate(report, "audit-report")
    assert report["summary"]["total_findings"] == 0


def _page(path: str, links: list[str]) -> str:
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    body = "Garuda Footwear sells running shoes, cricket shoes and kids footwear. " * 12
    return (
        f"<html><head><title>{path}</title></head><body>"
        f"<nav>{anchors}</nav><main><h1>{path}</h1><p>{body}</p></main>"
        "</body></html>"
    )


@responses.activate
def test_frontier_crawls_link_graph_breadth_first_without_a_render_backend():
    """The multi-page crawl must work from static HTML alone: with no
    Playwright, links come from <a href> parsing, and depth 2 reaches a page
    only linked from a depth-1 page."""
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    site = {
        "https://example.com/": ["/pricing", "/about"],
        "https://example.com/pricing": ["/pricing/enterprise"],
        "https://example.com/about": [],
        "https://example.com/pricing/enterprise": [],
        # Depth 3 — must NOT be crawled at max_depth=2.
        "https://example.com/pricing/enterprise/legal": [],
    }
    responses.add(
        responses.GET,
        "https://example.com/pricing/enterprise",
        body=_page("/pricing/enterprise", ["/pricing/enterprise/legal"]),
        status=200,
        content_type="text/html",
    )
    for url, links in site.items():
        if url == "https://example.com/pricing/enterprise":
            continue
        responses.add(
            responses.GET, url, body=_page(url, links), status=200, content_type="text/html"
        )

    report = run_audit(
        "example.com", options=AuditOptions(max_pages=10, max_render_pages=0, max_depth=2)
    )

    validate(report, "audit-report")
    crawled = {u for f in report["findings"] for u in f["affected_urls"]}
    assert "https://example.com/pricing" in crawled, "depth 1 not reached"
    assert "https://example.com/pricing/enterprise" in crawled, "depth 2 not reached"
    assert "https://example.com/pricing/enterprise/legal" not in crawled, "max_depth not enforced"


@responses.activate
def test_max_pages_caps_the_frontier():
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    many = [f"/p{i}" for i in range(20)]
    responses.add(
        responses.GET,
        "https://example.com/",
        body=_page("/", many),
        status=200,
        content_type="text/html",
    )
    for path in many:
        responses.add(
            responses.GET,
            f"https://example.com{path}",
            body=_page(path, []),
            status=200,
            content_type="text/html",
        )

    report = run_audit(
        "example.com", options=AuditOptions(max_pages=3, max_render_pages=0, max_depth=2)
    )

    crawled = {u for f in report["findings"] for u in f["affected_urls"]}
    assert len(crawled) == 3


# ---------------------------------------------------------------------------
# Foundations the web layer needs: a progress hook, and an explicit opt-in for
# auditing a local target you own.
# ---------------------------------------------------------------------------


@__import__("responses").activate
def test_on_page_reports_every_crawled_url_exactly_once():
    """A median audit is ~26s and the long ones run minutes, so the UI needs
    the crawl as it happens. Every page must be reported once — including the
    ones that end early, which are precisely the interesting ones to watch."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    page = (
        "<html><head><title>Example Tools</title></head><body><main>"
        "<h1>Example Tools</h1><p>Example Tools is an accounting suite for "
        "small businesses based in Bengaluru, India, shipping since 2014 and "
        "used by forty thousand businesses today.</p>"
        '<a href="https://example.com/pricing">Pricing</a>'
        '<a href="https://example.com/gone">Gone</a></main></body></html>'
    )
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(responses.GET, "https://example.com/", body=page, status=200,
                  content_type="text/html")
    responses.add(responses.GET, "https://example.com/pricing", body=page, status=200,
                  content_type="text/html")
    responses.add(responses.GET, "https://example.com/gone",
                  body="<html><body>Attention Required! | Cloudflare</body></html>",
                  status=403, content_type="text/html")

    events: list[dict] = []
    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=5, max_render_pages=0, corroborate=False),
        on_page=events.append,
    )

    urls = [e["url"] for e in events]
    assert len(urls) == len(set(urls)), f"a page was reported twice: {urls}"
    # One event per URL the crawl actually touched.
    assert len(events) == report["meta"]["crawl"]["pages_crawled"] + len(
        report["meta"]["crawl"]["pages_unreachable"]
    )

    by_url = {e["url"]: e for e in events}
    assert by_url["https://example.com/"]["outcome"] == "analysed"
    # A challenge page is reached but never read as content.
    assert by_url["https://example.com/gone"]["outcome"] == "blocked"

    last = events[-1]
    for key in ("depth", "pages_crawled", "pages_analysed", "pages_rendered",
                "queued", "skills_engaged"):
        assert key in last, f"progress snapshot missing {key}"
    # The snapshot must agree with the finished report, not drift from it.
    assert last["pages_analysed"] <= last["pages_crawled"]
    assert last["pages_crawled"] == report["meta"]["crawl"]["pages_crawled"]


@__import__("responses").activate
def test_audit_runs_unchanged_without_a_progress_hook():
    """on_page is advisory: omitting it must change nothing about the result."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    def stub():
        responses.add(responses.GET, "https://example.com/robots.txt", status=404)
        responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
        responses.add(
            responses.GET, "https://example.com/",
            body="<html><head><title>T</title></head><body><main><h1>T</h1>"
                 "<p>" + "Example sells accounting software. " * 20 + "</p>"
                 "</main></body></html>",
            status=200, content_type="text/html")

    stub()
    opts = dict(max_pages=1, max_render_pages=0, corroborate=False)
    quiet = run_audit("example.com", options=AuditOptions(**opts))
    noisy = run_audit("example.com", options=AuditOptions(**opts), on_page=lambda e: None)

    assert quiet["summary"]["readiness_score"] == noisy["summary"]["readiness_score"]
    assert [f["title"] for f in quiet["findings"]] == [
        f["title"] for f in noisy["findings"]
    ]


def test_allow_private_hosts_is_opt_in_and_off_by_default():
    """The SSRF guard must stay closed unless the caller deliberately opens
    it — a hosted form must never be able to reach a private address, and the
    default is what a forgetful caller gets."""
    import requests

    from braiaudit.fetch import _PublicOnlyAdapter, harden_session
    from braiaudit.pipeline import AuditOptions

    assert AuditOptions().allow_private_hosts is False

    guarded = harden_session(requests.Session())
    assert isinstance(guarded.get_adapter("https://example.com/"), _PublicOnlyAdapter)

    opened = harden_session(requests.Session(), allow_private=True)
    assert not isinstance(opened.get_adapter("https://example.com/"), _PublicOnlyAdapter)


def test_allow_private_hosts_reaches_a_local_target():
    """The flag has to actually work, or 'audit your own dev server' is a
    promise the CLI cannot keep."""
    import http.server
    import socketserver
    import threading

    from braiaudit.fetch import observe

    body = (
        b"<html><head><title>My Dev Site</title></head><body><main><h1>My Dev Site</h1>"
        b"<p>A local development server serving real content for an audit.</p>"
        b"</main></body></html>"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            blocked = observe(f"http://127.0.0.1:{port}/", user_agent="TestBot/1.0")
            allowed = observe(
                f"http://127.0.0.1:{port}/", user_agent="TestBot/1.0", allow_private=True
            )
        finally:
            srv.shutdown()

    assert blocked["http_status"] is None
    assert allowed["http_status"] == 200
    assert "My Dev Site" in (allowed.get("raw_html") or "")


@__import__("responses").activate
def test_render_budget_and_missing_backend_are_reported_as_different_gaps(monkeypatch):
    """One signal used to cover both, so a user with a working browser was
    told to install one when the real cause was the per-audit render budget.
    The two gaps need opposite fixes and must stay distinguishable."""
    import responses

    from braiaudit import render as render_mod
    from braiaudit.pipeline import AuditOptions, run_audit

    # Static HTML that looks client-side-rendered, so a render is wanted.
    shell = ('<html><head><title>T</title></head><body><div id="root"></div>'
             + "<script>x</script>" * 20 + "</body></html>")

    def stub():
        responses.add(responses.GET, "https://example.com/robots.txt", status=404)
        responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
        responses.add(responses.GET, "https://example.com/", body=shell, status=200,
                      content_type="text/html")

    def limitation_titles(report):
        return {lim["title"] for lim in report["audit_limitations"]}

    budget = "Some Pages Were Not Rendered Within This Audit's Render Budget"
    missing = "Render-Dependent Checks Could Not Be Verified"

    # Budget spent (here: zero) — the browser is irrelevant.
    stub()
    spent = run_audit("example.com", options=AuditOptions(
        max_pages=1, max_render_pages=0, corroborate=False))
    assert budget in limitation_titles(spent)
    assert missing not in limitation_titles(spent)

    # Budget available but no browser.
    monkeypatch.setattr(render_mod, "_PLAYWRIGHT_AVAILABLE", False)
    stub()
    absent = run_audit("example.com", options=AuditOptions(
        max_pages=1, max_render_pages=3, corroborate=False))
    assert missing in limitation_titles(absent)
    assert budget not in limitation_titles(absent)
