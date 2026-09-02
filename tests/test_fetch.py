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
