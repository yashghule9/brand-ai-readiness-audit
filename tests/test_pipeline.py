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
    assert report["summary"]["critical"] == 1
    assert report["findings"][0]["title"] == "Robots.txt Rules Block AI Scrapers"


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
